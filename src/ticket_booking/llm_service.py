
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent import futures
from pathlib import Path
import grpc
from ticket_booking.faq_data import FAQRetriever

GEN = Path(__file__).resolve().parent / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc

class LLMService(pb_grpc.LLMServiceServicer):
    def __init__(
        self,
        ret: FAQRetriever | None = None,
        url: str = "http://127.0.0.1:11434/api/generate",
        model: str = "llama3.2:1b",
        wait: float = 5.0,
        ask: Callable[[str], str | None] | None = None,
    ):
        self.ret = ret or FAQRetriever()
        self.url = url
        self.model = model
        self.wait = wait
        self.ask = ask

    def _ask(self, prompt: str) -> str | None:
        if self.ask is not None:
            return self.ask(prompt)
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.wait) as res:
                data = json.loads(res.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None
        ans = data.get("response", "").strip()
        return ans or None

    def GetLLMAnswer(self, req: pb.LLMRequest, ctx: grpc.ServicerContext) -> pb.LLMResponse:
        text = req.context.strip()
        if not text:
            text, _ = self.ret.retrieve(req.query)
        prompt = (
            "You are a ticket-booking support assistant. Answer only from this context.\n\n"
            f"Context:\n{text}\n\nQuestion: {req.query.strip()}"
        )
        ans = self._ask(prompt)
        if ans is None:
            ctx.abort(grpc.StatusCode.UNAVAILABLE, "Ollama is unavailable.")
        return pb.LLMResponse(request_id=req.request_id, answer=ans)


def serve(port: int = 50055, workers: int = 5) -> grpc.Server:
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    pb_grpc.add_LLMServiceServicer_to_server(LLMService(), srv)
    srv.add_insecure_port(f"[::]:{port}")
    srv.start()
    return srv


if __name__ == "__main__":
    serve().wait_for_termination()


