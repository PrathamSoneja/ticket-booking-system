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
from ticket_booking.faq_data import FAQRetriever, FaqTopic
from ticket_booking.llm_service import LLMService


@pytest.fixture(scope="module")
def llm():
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    svc = LLMService(fake_ask_fn=lambda prompt: prompt)
    pb_grpc.add_LLMServiceServicer_to_server(svc, srv)
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
    assert "cancel" in res.answer.lower()


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
    ans = llm.GetLLMAnswer(pb.LLMRequest(request_id=str(uuid4()), query=query)).answer.lower()
    assert all(word in ans for word in words)


def test_custom_knowledge() -> None:
    ret = FAQRetriever()
    ret.set_knowledge_base([
        FaqTopic(
            topic_key="cancel",
            display_title="Strict Policy",
            trigger_words=("cancel",),
            body_text="Cancellations are permitted up to 48 hours in advance with a 20% penalty fee.",
        )
    ])
    svc = LLMService(faq_engine=ret, fake_ask_fn=lambda prompt: prompt)
    req = pb.LLMRequest(request_id=str(uuid4()), query="What is the cancellation policy?")
    res = svc.GetLLMAnswer(req, None)
    assert "48 hours" in res.answer
    assert "20% penalty fee" in res.answer


def test_request_context(llm) -> None:
    text = "VIP guests receive free champagne upon booking."
    res = llm.GetLLMAnswer(pb.LLMRequest(request_id=str(uuid4()), query="VIP", context=text))
    assert "free champagne" in res.answer.lower()


def test_unavailable_ollama() -> None:
    svc = LLMService(fake_ask_fn=lambda _: None)
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
