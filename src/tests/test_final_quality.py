import sys

import pytest

from tests.raft_faults import wait_converged
from tests.test_m2_features import FakeLLM, start_app, start_llm
from tests.test_raft_m2 import Cluster
from ticket_booking import client as cli
from ticket_booking.client import Client
from ticket_booking.generated import ticket_booking_pb2 as pb
from ticket_booking.raft_node import RaftNode
from ticket_booking.state_machine import Outcome


@pytest.fixture
def app_server():
    srv, addr, app = start_app()
    yield addr, app
    srv.stop(grace=None)


def test_booking_charges_server_price_not_client_amount(app_server):
    addr, app = app_server
    c = Client(addr)
    assert c.login("alice", "wonderland")[0]
    assert c.book_seat("show-1", "A1", amount=500)[0] == "OK"
    assert c.book_seat("show-1", "A2,A3", amount="100.00")[0] == "OK"
    assert c.book_seat("show-1", "A4", amount="49.99")[0] == "INVALID_AMOUNT"
    amounts = sorted(r["amount"] for r in c.get_my_bookings()[1])
    assert amounts == [app.pay.price, app.pay.price * 2]
    c.close()


def test_raft_booking_charges_server_price_not_client_amount(tmp_path):
    c = Cluster(tmp_path)
    try:
        assert c.leader()
        cl = Client(list(c.addrs.values()))
        assert cl.login("alice", "wonderland")[0]
        status, bid, _ = cl.book_seat("show-2", "A1", amount=999)
        assert status == "OK"
        assert wait_converged(c.nodes)
        for node in c.nodes.values():
            txn = node.app.seats.bookings[bid].txn
            assert node.app.pay.txn_summary(txn)["amount"] == node.app.pay.price
        cl.close()
    finally:
        c.close()


@pytest.mark.parametrize(
    "query",
    [
        "What time is it in Tokyo?",
        "How do I change my car tyre?",
        "What is the price of bitcoin?",
        "Tell me a joke about money",
        "When is the next solar eclipse?",
        "Exchange rate of dollar to euro?",
    ],
)
def test_off_topic_queries_sharing_faq_words_are_rejected(query):
    fake = FakeLLM()
    lsrv, url = start_llm(fake)
    srv, addr, _ = start_app(url)
    c = Client(addr)
    assert c.login("alice", "wonderland")[0]
    status, text, support = c.ask_faq_detailed(query)
    assert status == "OUT_OF_DOMAIN", (status, support.get("sources"))
    assert fake.seen == []
    c.close()
    srv.stop(grace=None)
    lsrv.stop(grace=None)


@pytest.mark.parametrize(
    "query",
    [
        "How do I cancel a booking?",
        "Is payment refundable on cancellation?",
        "What seats are available for the show?",
        "How far in advance can I book?",
        "Can I change my seat after booking?",
        "How to book a seat?",
        "What is the refund policy for tickets?",
    ],
)
def test_in_domain_queries_still_reach_the_llm(query):
    fake = FakeLLM()
    lsrv, url = start_llm(fake)
    srv, addr, _ = start_app(url)
    c = Client(addr)
    assert c.login("alice", "wonderland")[0]
    assert c.ask_faq_detailed(query, show_id="show-1")[0] == "OK"
    assert len(fake.seen) == 1
    c.close()
    srv.stop(grace=None)
    lsrv.stop(grace=None)


def run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["client", *args])
    cli.main()
    return capsys.readouterr().out


def test_cli_lists_own_bookings_and_can_use_a_declining_mock_card(app_server, monkeypatch, capsys):
    addr, _ = app_server
    out = run_cli(monkeypatch, capsys, "--server", addr, "--action", "book", "--show-id", "show-3", "--seat-id", "A5")
    assert "'OK'" in out
    out = run_cli(monkeypatch, capsys, "--server", addr, "--action", "mybookings")
    assert "show-3" in out and "A5" in out and "CONFIRMED" in out
    out = run_cli(
        monkeypatch, capsys, "--server", addr, "--username", "bob", "--password", "builder", "--action", "mybookings"
    )
    assert "A5" not in out
    out = run_cli(
        monkeypatch,
        capsys,
        "--server",
        addr,
        "--action",
        "book",
        "--show-id",
        "show-3",
        "--seat-id",
        "A6",
        "--card",
        "4000000000000000",
    )
    assert "PAYMENT_FAILED" in out


def leader_node(tmp_path, name="n1"):
    node = RaftNode(name, {}, tmp_path / f"{name}.db", lambda c, p, r: Outcome("OK", r))
    node.term = 1
    node.role = "leader"
    node.leader_id = name
    return node


def test_propose_does_not_scan_the_log_for_request_ids(tmp_path):
    node = leader_node(tmp_path)
    for i in range(50):
        assert node.propose("TEST", b"x", f"r{i}")[0] == "OK"
    calls = []
    real = node.store.entries_from
    node.store.entries_from = lambda idx: calls.append(idx) or real(idx)
    assert node.propose("TEST", b"x", "r7") == ("OK", Outcome("OK", "r7"))
    assert node.propose("TEST", b"x", "new")[0] == "OK"
    assert 1 not in calls
    assert node.store.last_index == 51
    node.close()


def test_request_index_is_rebuilt_on_restart(tmp_path):
    node = leader_node(tmp_path)
    assert node.propose("TEST", b"x", "keep")[0] == "OK"
    node.close()
    again = leader_node(tmp_path)
    assert again.propose("TEST", b"x", "keep")[0] == "OK"
    assert again.store.last_index == 1
    again.close()


def test_request_index_forgets_truncated_entries(tmp_path):
    seen = []
    node = RaftNode("n1", {"n2": "127.0.0.1:1"}, tmp_path / "t.db", lambda c, p, r: seen.append(r) or Outcome("OK", r))
    first = pb.AppendEntriesArgs(
        **{
            "from": "n2",
            "term": 1,
            "prev_index": 0,
            "prev_term": 0,
            "commit_index": 0,
            "entries": [pb.LogEntry(index=1, term=1, command_type="TEST", payload=b"a", request_id="gone")],
        }
    )
    assert node.append_entries(first).entry_appended
    replace = pb.AppendEntriesArgs(
        **{
            "from": "n2",
            "term": 2,
            "prev_index": 0,
            "prev_term": 0,
            "commit_index": 1,
            "entries": [pb.LogEntry(index=1, term=2, command_type="TEST", payload=b"b", request_id="other")],
        }
    )
    assert node.append_entries(replace).entry_appended
    assert seen == ["other"]
    node.term = 3
    node.role = "leader"
    node.leader_id = "n1"
    node.peers = {}
    node.majority = 1
    assert node.propose("TEST", b"c", "gone")[0] == "OK"
    assert node.store.entry_at(2)[4] == "gone"
    assert seen == ["other", "gone"]
    node.close()


def test_raft_retry_with_unnormalized_seat_list_returns_same_booking(tmp_path):
    c = Cluster(tmp_path)
    try:
        assert c.leader()
        cl = Client(list(c.addrs.values()))
        assert cl.login("alice", "wonderland")[0]
        first = cl.book_seat("show-4", "A1; A2", req_id="same-key")
        again = cl.book_seat("show-4", "A1; A2", req_id="same-key")
        assert first[0] == "OK"
        assert again[:2] == first[:2]
        cl.close()
    finally:
        c.close()


def test_user_cannot_cancel_or_see_another_users_booking(app_server):
    addr, app = app_server
    alice, bob = Client(addr), Client(addr)
    assert alice.login("alice", "wonderland")[0] and bob.login("bob", "builder")[0]
    status, bid, _ = alice.book_seat("show-5", "A9")
    assert status == "OK"
    assert bob.cancel_seat(bid)[0] == "FORBIDDEN"
    assert bid not in [r["booking_id"] for r in bob.get_my_bookings()[1]]
    row = alice.get_my_bookings()[1][0]
    assert row["status"] == "CONFIRMED" and row["refunded"] is False
    alice.close()
    bob.close()


def test_raft_user_cannot_cancel_another_users_booking(tmp_path):
    c = Cluster(tmp_path)
    try:
        assert c.leader()
        seeds = list(c.addrs.values())
        alice, mallory = Client(seeds), Client(seeds)
        assert alice.login("alice", "wonderland")[0]
        assert mallory.signup("mallory", "GoodPass1")[0]
        status, bid, _ = alice.book_seat("show-5", "A9")
        assert status == "OK"
        assert mallory.cancel_seat(bid)[0] == "FORBIDDEN"
        assert wait_converged(c.nodes)
        for node in c.nodes.values():
            assert node.app.seats.bookings[bid].status == "CONFIRMED"
            assert not node.app.pay.refunds
        alice.close()
        mallory.close()
    finally:
        c.close()
