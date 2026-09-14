"""Independent gRPC LLM FAQ Service with CPU-only local fallback (FR-LLM-1, FR-LLM-4)."""

from __future__ import annotations

import json
import logging
import sys
import urllib.error
import urllib.request
from concurrent import futures
from pathlib import Path

# Ensure generated proto package is in sys.path
_GENERATED_DIR = Path(__file__).resolve().parent / "generated"
if str(_GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(_GENERATED_DIR))

import grpc
import ticket_booking_pb2
import ticket_booking_pb2_grpc
from ticket_booking.faq_data import FAQRetriever

logger = logging.getLogger("LLMService")


class CPUFallbackEngine:
    """CPU-only, zero-external-dependency rule-and-context synthesizer for FAQ queries."""

    @staticmethod
    def answer(query: str, context: str) -> str:
        query_lower = query.lower().strip()

        if not context:
            return (
                "I am your Ticket Booking Assistant. I can help answer questions about shows, "
                "how to book tickets, seat availability, advance booking windows, and our cancellation/refund policies. "
                "Please ask a question related to these topics!"
            )

        # Synthesize answer clearly referencing the grounded context
        lines = [line.strip() for line in context.splitlines() if line.strip()]
        header = "Based on our ticketing policies:\n"

        # Check for specific FAQ intents
        if any(w in query_lower for w in ("cancel", "cancellation", "cancelling", "drop")):
            relevant = [l for l in lines if not l.startswith("[")]
            return f"{header}- " + "\n- ".join(relevant)

        if any(w in query_lower for w in ("refund", "money", "reimbursement", "cost")):
            relevant = [l for l in lines if not l.startswith("[")]
            return f"{header}- " + "\n- ".join(relevant)

        if any(w in query_lower for w in ("seat", "available", "empty", "browse")):
            relevant = [l for l in lines if not l.startswith("[")]
            return f"{header}- " + "\n- ".join(relevant)

        if any(w in query_lower for w in ("advance", "window", "early", "when")):
            relevant = [l for l in lines if not l.startswith("[")]
            return f"{header}- " + "\n- ".join(relevant)

        if any(w in query_lower for w in ("change", "modify", "switch", "exchange")):
            relevant = [l for l in lines if not l.startswith("[")]
            return f"{header}- " + "\n- ".join(relevant)

        if any(w in query_lower for w in ("how", "book", "procedure", "process", "steps")):
            relevant = [l for l in lines if not l.startswith("[")]
            return f"{header}- " + "\n- ".join(relevant)

        # Default contextual synthesis
        body = "\n".join(l for l in lines if not l.startswith("["))
        return f"{header}{body}"


class LLMService(ticket_booking_pb2_grpc.LLMServiceServicer):
    """Independent gRPC servicer fulfilling FR-LLM-1 to FR-LLM-4."""

    def __init__(
        self,
        retriever: FAQRetriever | None = None,
        ollama_endpoint: str = "http://127.0.0.1:11434/api/generate",
        model_name: str = "llama3.2:1b",
        connect_timeout: float = 0.5,
    ):
        self.retriever = retriever or FAQRetriever()
        self.ollama_endpoint = ollama_endpoint
        self.model_name = model_name
        self.connect_timeout = connect_timeout
        self.fallback = CPUFallbackEngine()

    def _query_ollama(self, prompt: str) -> str | None:
        """Attempt to query local Ollama if available on CPU."""
        payload = json.dumps({
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.ollama_endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.connect_timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    answer = data.get("response", "").strip()
                    if answer:
                        return answer
        except Exception:
            # Silently fall back to CPU fallback engine on connection error, refusal, or timeout
            pass
        return None

    def GetLLMAnswer(
        self,
        request: ticket_booking_pb2.LLMRequest,
        context: grpc.ServicerContext,
    ) -> ticket_booking_pb2.LLMResponse:
        query = request.query.strip()
        request_id = request.request_id

        retrieved_ctx, _ = self.retriever.retrieve(query)
        extra_context = request.context.strip()
        injected_context = "\n\n".join(part for part in (retrieved_ctx, extra_context) if part)

        # Format prompt for instruct LLM
        prompt = (
            f"You are a helpful customer support chatbot for a ticket booking system.\n"
            f"Context:\n{injected_context}\n\n"
            f"User Query: {query}\n\n"
            f"Answer the query accurately based only on the provided context:"
        )

        # 1. Try local Ollama CPU model
        answer = self._query_ollama(prompt)

        # 2. Fall back to local CPU rule/context synthesis engine
        if not answer:
            answer = self.fallback.answer(query, injected_context)

        return ticket_booking_pb2.LLMResponse(
            request_id=request_id,
            answer=answer,
        )


def serve(port: int = 50055, max_workers: int = 5) -> grpc.Server:
    """Start the LLM gRPC server."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    ticket_booking_pb2_grpc.add_LLMServiceServicer_to_server(LLMService(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = 50055
    server = serve(port=port)
    print(f"LLM gRPC Server running on port {port}...")
    server.wait_for_termination()
