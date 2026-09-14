"""Acceptance and unit tests for the LLM FAQ gRPC service (FR-LLM-1 to FR-LLM-4)."""

from __future__ import annotations

import sys
from concurrent import futures
from pathlib import Path
from uuid import uuid4

# Ensure generated proto package is in sys.path
_GENERATED_DIR = Path(__file__).resolve().parents[1] / "ticket_booking" / "generated"
if str(_GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(_GENERATED_DIR))

import grpc
import pytest
import ticket_booking_pb2
import ticket_booking_pb2_grpc
from ticket_booking.faq_data import FAQRetriever, KnowledgeSnippet
from ticket_booking.llm_service import LLMService


@pytest.fixture(scope="module")
def llm_server():
    """Spin up an in-process gRPC LLM server for the test suite."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    retriever = FAQRetriever()
    service = LLMService(retriever=retriever)
    ticket_booking_pb2_grpc.add_LLMServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = ticket_booking_pb2_grpc.LLMServiceStub(channel)

    yield {"server": server, "stub": stub, "service": service, "retriever": retriever}

    channel.close()
    server.stop(grace=None)


def test_grpc_llm_service_returns_answer(llm_server) -> None:
    """FR-LLM-1: gRPC communication returns answer for request."""
    stub = llm_server["stub"]
    req_id = str(uuid4())
    req = ticket_booking_pb2.LLMRequest(
        request_id=req_id,
        query="How do I cancel a booking?",
        context="",
    )
    resp = stub.GetLLMAnswer(req)
    assert resp.request_id == req_id
    assert len(resp.answer) > 0
    assert "cancel" in resp.answer.lower() or "2 hours" in resp.answer.lower()


@pytest.mark.parametrize(
    ("query", "expected_keywords"),
    [
        ("How do I cancel a booking?", ["cancel", "2 hours"]),
        ("Is payment refundable on cancellation?", ["refund", "2 hours"]),
        ("What seats are available for the show?", ["seat", "available"]),
        ("How far in advance can I book?", ["30 days", "15 minutes"]),
        ("Can I change my seat after booking?", ["cancel", "direct seat changes are not supported"]),
        ("How to book a seat?", ["log in", "book_seat"]),
    ],
)
def test_domain_faq_queries_are_grounded(llm_server, query: str, expected_keywords: list[str]) -> None:
    """FR-LLM-2: Chatbot answers domain FAQs referencing the correct policy."""
    stub = llm_server["stub"]
    req = ticket_booking_pb2.LLMRequest(
        request_id=str(uuid4()),
        query=query,
        context="",
    )
    resp = stub.GetLLMAnswer(req)
    answer_lower = resp.answer.lower()
    for kw in expected_keywords:
        assert kw.lower() in answer_lower, f"Expected '{kw}' in answer for query '{query}', got: {resp.answer}"


def test_swapping_knowledge_base_changes_answer() -> None:
    """FR-LLM-3: Grounding in custom knowledge base visibly changes the response."""
    retriever = FAQRetriever()
    service = LLMService(retriever=retriever)

    custom_snippets = [
        KnowledgeSnippet(
            topic_id="cancellation_policy",
            title="Custom Strict Cancellation Policy",
            keywords=("cancel", "cancellation"),
            content="Cancellations are strictly permitted up to 48 hours in advance with a 20% penalty fee.",
        )
    ]
    retriever.set_knowledge_base(custom_snippets)

    req = ticket_booking_pb2.LLMRequest(
        request_id=str(uuid4()),
        query="What is the cancellation policy?",
        context="",
    )
    resp = service.GetLLMAnswer(req, context=None)
    assert "48 hours" in resp.answer
    assert "20% penalty fee" in resp.answer


def test_explicit_context_injection_in_request(llm_server) -> None:
    """Passing explicit context in LLMRequest overrides knowledge base lookup."""
    stub = llm_server["stub"]
    custom_context = "Special VIP policy: VIP guests receive free champagne upon booking."
    req = ticket_booking_pb2.LLMRequest(
        request_id=str(uuid4()),
        query="What do VIP guests receive?",
        context=custom_context,
    )
    resp = stub.GetLLMAnswer(req)
    assert "free champagne" in resp.answer.lower()
