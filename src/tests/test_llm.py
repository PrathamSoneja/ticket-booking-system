import sys
from concurrent import futures
from pathlib import Path
from uuid import uuid4

import grpc
import pytest

GEN = Path(__file__).resolve().parents[1] / "ticket_booking" / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.faq_data import FaqIndex, Topic
from ticket_booking.llm_service import LLMService


@pytest.fixture(scope="module")
def llm():
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_LLMServiceServicer_to_server(LLMService(), srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield pb_grpc.LLMServiceStub(ch)
    ch.close()
    srv.stop(grace=None)


def test_llm_reply(llm) -> None:
    req_id = str(uuid4())
    res = llm.GetLLMAnswer(pb.LLMRequest(request_id=req_id, query="How do I cancel a booking?"))
    assert res.request_id == req_id
    assert res.answer.strip() != ""


@pytest.mark.parametrize(
    ("query", "words"),
    [
        ("How do I cancel a booking?", ["cancel", "2 hours"]),
        ("Is payment refundable on cancellation?", ["refund", "2 hours"]),
        ("What seats are available for the show?", ["seat", "available"]),
        ("How far in advance can I book?", ["30 days", "15 minutes"]),
        ("Can I change my seat after booking?", ["cancel", "direct seat changes are not supported"]),
        ("How to book a seat?", ["log in", "book_seat"]),
    ],
)
def test_faq_context(llm, query: str, words: list[str]) -> None:
    ctx, _ = FaqIndex().retrieve(query)
    assert all(word in ctx.lower() for word in words)
    assert llm.GetLLMAnswer(pb.LLMRequest(request_id=str(uuid4()), query=query)).answer.strip() != ""


def test_faq_swap() -> None:
    idx = FaqIndex()
    idx.set_topics([
        Topic(
            key="cancel",
            title="Strict Policy",
            words=("cancel",),
            body="Cancellations are permitted up to 48 hours in advance with a 20% penalty fee.",
        )
    ])
    ctx, _ = idx.retrieve("What is the cancellation policy?")
    assert "48 hours" in ctx
    assert "20% penalty fee" in ctx
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    pb_grpc.add_LLMServiceServicer_to_server(LLMService(faq=idx), srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = pb_grpc.LLMServiceStub(ch)
    res = stub.GetLLMAnswer(pb.LLMRequest(request_id=str(uuid4()), query="What is the cancellation policy?"))
    assert res.answer.strip() != ""
    ch.close()
    srv.stop(grace=None)


def test_request_context(llm) -> None:
    text = "VIP guests receive free champagne upon booking."
    res = llm.GetLLMAnswer(pb.LLMRequest(request_id=str(uuid4()), query="VIP", context=text))
    assert res.answer.strip() != ""


def test_ollama_down() -> None:
    svc = LLMService(ollama="http://127.0.0.1:1/api/generate", timeout=1.0)
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    pb_grpc.add_LLMServiceServicer_to_server(svc, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = pb_grpc.LLMServiceStub(ch)
    with pytest.raises(grpc.RpcError) as err:
        stub.GetLLMAnswer(pb.LLMRequest(request_id="1", query="How do I book?"))
    assert err.value.code() == grpc.StatusCode.UNAVAILABLE
    ch.close()
    srv.stop(grace=None)
