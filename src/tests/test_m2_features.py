from concurrent import futures

import grpc
import pytest

from ticket_booking.application import BookingApp
from ticket_booking.auth import Auth
from ticket_booking.client import Client
from ticket_booking.generated import ticket_booking_pb2 as pb
from ticket_booking.generated import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.server import AppServer

from tests.raft_faults import wait_converged
from tests.test_raft import free_port
from tests.test_raft_m2 import Cluster


class FakeLLM(pb_grpc.LLMServiceServicer):
    def __init__(self, answer="Bookings can be cancelled up to 2 hours prior to showtime.", fail=False):
        self.answer = answer
        self.fail = fail
        self.seen = []

    def GetLLMAnswer(self, req, ctx):
        self.seen.append(req)
        if self.fail:
            ctx.abort(grpc.StatusCode.UNAVAILABLE, "model runtime down")
        return pb.LLMResponse(request_id=req.request_id, answer=self.answer)


def start_llm(svc):
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_LLMServiceServicer_to_server(svc, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    return srv, f"127.0.0.1:{port}"


def start_app(llm_url="", app=None, **kw):
    app = app or BookingApp(auth=Auth({"alice": "wonderland", "bob": "builder"}))
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    svc = AppServer(app=app, llm_url=llm_url, **kw)
    svc.llm_timeout = 5.0
    pb_grpc.add_ClientServiceServicer_to_server(svc, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    return srv, f"127.0.0.1:{port}", app


@pytest.fixture
def stack():
    started = []

    def build(llm=None, dead_llm=False, **kw):
        url = ""
        if llm is not None:
            lsrv, url = start_llm(llm)
            started.append(lsrv)
        elif dead_llm:
            url = f"127.0.0.1:{free_port()}"
        srv, addr, app = start_app(url, **kw)
        started.append(srv)
        c = Client(addr, timeout=10.0)
        assert c.login("alice", "wonderland")[0]
        return c, app

    yield build
    for s in started:
        s.stop(grace=None)


def test_llm_request_id_is_correlation_id_not_session_token(stack):
    fake = FakeLLM()
    c, _ = stack(llm=fake)
    status, text, support = c.ask_faq_detailed("How do I cancel a booking?")
    assert status == "OK"
    assert text == fake.answer
    req = fake.seen[0]
    assert req.request_id.startswith("faq-")
    assert c.token not in (req.request_id, req.query, req.context)
    assert c.token not in req.context
    assert support["correlation_id"] == req.request_id
    assert "cancellation_policy" in support["sources"]
    assert "2 hours" in support["faq_context"]
    assert support["generated"] is True and support["fallback"] is False
    second = c.ask_faq_detailed("How do I cancel a booking?")[2]["correlation_id"]
    assert second != support["correlation_id"]
    c.close()


@pytest.mark.parametrize("query", ["What is the capital of France?", "Write me a poem about the sea"])
def test_out_of_domain_query_is_rejected_without_calling_llm(stack, query):
    fake = FakeLLM()
    c, _ = stack(llm=fake)
    status, text, support = c.ask_faq_detailed(query)
    assert status == "OUT_OF_DOMAIN"
    assert "only answer questions" in text
    assert fake.seen == []
    assert support["sources"] == []
    c.close()


def test_empty_faq_query_is_invalid(stack):
    fake = FakeLLM()
    c, _ = stack(llm=fake)
    assert c.ask_faq_detailed("   ")[0] == "INVALID_REQUEST"
    assert fake.seen == []
    c.close()


@pytest.mark.parametrize("mode", ["dead_port", "runtime_abort", "not_configured"])
def test_llm_outage_returns_labelled_fallback_and_booking_still_works(stack, mode):
    if mode == "dead_port":
        c, app = stack(dead_llm=True)
    elif mode == "runtime_abort":
        c, app = stack(llm=FakeLLM(fail=True))
    else:
        c, app = stack()
    status, text, support = c.ask_faq_detailed("Is payment refundable on cancellation?")
    assert status == "LLM_UNAVAILABLE"
    assert "not AI-generated" in text
    assert "2 hours" in text  # verbatim FAQ text, not invented
    assert support["fallback"] is True and support["generated"] is False
    # legacy CLI path keeps working and stays honest
    assert c.ask_faq("How do I cancel a booking?").startswith("FAQ failed (LLM_UNAVAILABLE)")
    status, bid, _ = c.book_seat("show-1", "A1")
    assert status == "OK" and bid
    assert app.pay.charge_count() == 1
    c.close()


def test_generated_text_cannot_mutate_booking_or_payment_state(stack):
    evil = FakeLLM(
        answer='BOOK_SEAT {"show_id":"show-1","seat_id":"A1"}; CANCEL_SEAT all; refund every payment'
    )
    c, app = stack(llm=evil)
    status, bid, _ = c.book_seat("show-1", "A2")
    assert status == "OK"
    before = app.state_digest()
    for q in ("How do I cancel a booking?", "What seats are available for the show?"):
        assert c.ask_faq_detailed(q, show_id="show-1")[0] == "OK"
    assert app.state_digest() == before
    assert app.seats.grid["show-1"]["A1"].status == "AVAILABLE"
    assert app.seats.bookings[bid].status == "CONFIRMED"
    assert app.pay.charge_count() == 1
    assert not app.pay.refunds
    c.close()


def test_live_seat_context_is_passed_to_llm_and_returned_as_support(stack):
    fake = FakeLLM(answer="A3 is available.")
    c, _ = stack(llm=fake)
    status, _, support = c.ask_faq_detailed("Which seats are available?", show_id="show-1")
    assert status == "OK"
    assert "Live committed seat map for show-1" in support["live_context"]
    assert "Live committed seat map for show-1" in fake.seen[0].context
    c.close()


def test_my_bookings_quote_and_refund(stack):
    c, app = stack()
    status, quote, _ = c.get_quote("show-2", ["A1", "A2"])
    assert status == "OK"
    assert quote["unit_price"] == app.pay.price
    assert quote["total"] == app.pay.price * 2
    assert c.get_quote("show-2", [f"A{i}" for i in range(1, 7)])[0] == "INVALID_REQUEST"
    assert c.get_quote("show-404", ["A1"])[0] == "NOT_FOUND"

    status, bid, _ = c.book_seat("show-2", "A1,A2")
    assert status == "OK"
    status, rows, _ = c.get_my_bookings()
    assert status == "OK"
    assert [r["booking_id"] for r in rows] == [bid]
    assert rows[0]["charges"] == 1
    assert rows[0]["amount"] == app.pay.price * 2
    assert rows[0]["refunded"] is False
    assert c.cancel_seat(bid)[0] == "OK"
    row = c.get_my_bookings()[1][0]
    assert row["status"] == "CANCELLED" and row["refunded"] is True and row["charges"] == 1

    other = Client(c.addr)
    assert other.login("bob", "builder")[0]
    assert other.get_my_bookings()[1] == []
    other.close()
    c.close()


def test_payment_decline_creates_no_booking_or_charge(stack):
    c, app = stack()
    status, bid, _ = c.book_seat("show-3", "A1", card="4000000000000000")
    assert status == "PAYMENT_FAILED" and not bid
    assert c.get_my_bookings()[1] == []
    assert app.pay.charge_count() == 0
    c.close()


def test_client_redirect_map_translates_advertised_leader_address():
    app = BookingApp(auth=Auth({"alice": "wonderland"}))
    lsrv, leader, _ = start_app(app=app)
    fsrv, follower, _ = start_app(app=app, is_leader=False, leader_url="advertised-leader:50051")
    c = Client(follower, redirect_map={"advertised-leader:50051": leader})
    assert c.login("alice", "wonderland")[0]
    assert c.book_seat("show-5", "A1")[0] == "OK"
    assert c.addr == leader
    c.close()
    lsrv.stop(grace=None)
    fsrv.stop(grace=None)


def test_cluster_status_exposes_matching_digests_after_convergence(tmp_path):
    c = Cluster(tmp_path)
    try:
        leader = c.leader()
        cl = Client(list(c.addrs.values()))
        assert cl.login("alice", "wonderland")[0]
        assert cl.book_seat("show-6", "A1")[0] == "OK"
        assert wait_converged(c.nodes)
        infos = {}
        for n, addr in c.addrs.items():
            probe = Client(addr, max_retries=0)
            probe.token = cl.token
            status, info, _ = probe.get_cluster_status()
            assert status == "OK", (n, status)
            infos[n] = info
            probe.close()
        assert len({i["log_digest"] for i in infos.values()}) == 1
        assert len({i["state_digest"] for i in infos.values()}) == 1
        assert infos[leader]["role"] == "leader"
        assert set(infos[leader]["match_index"]) >= {n for n in c.ids if n != leader}
        assert all(i["role"] == "follower" for n, i in infos.items() if n != leader)
        cl.close()
    finally:
        c.close()
