import json
import sys
import urllib.error
import urllib.request
from concurrent import futures
from pathlib import Path

import grpc

from ticket_booking.faq_data import FaqIndex

GEN = Path(__file__).resolve().parent / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc


class LLMService(pb_grpc.LLMServiceServicer):
    def __init__(self, faq=None, ollama="http://127.0.0.1:11434/api/generate", model="llama3.2:1b", timeout=30.0):
        self.faq = faq if faq else FaqIndex()
        self.ollama = ollama
        self.model = model
        self.timeout = timeout

    def GetLLMAnswer(self, req, ctx):
        ctx_txt = req.context.strip()
        if ctx_txt == "":
            ctx_txt, _ = self.faq.retrieve(req.query)
        prompt = (
            "You are a ticket-booking support assistant. Answer only from this context.\n\n"
            "Context:\n" + ctx_txt + "\n\nQuestion: " + req.query.strip()
        )
        answer = None
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode()
        http_req = urllib.request.Request(
            self.ollama, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(http_req, timeout=self.timeout) as resp:
                answer = json.loads(resp.read().decode()).get("response", "").strip() or None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            answer = None
        if answer is None:
            ctx.abort(grpc.StatusCode.UNAVAILABLE, "Ollama is unavailable.")
        return pb.LLMResponse(request_id=req.request_id, answer=answer)


def serve(port=50055, workers=16):
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    pb_grpc.add_LLMServiceServicer_to_server(LLMService(), srv)
    srv.add_insecure_port(f"[::]:{port}")
    srv.start()
    return srv


if __name__ == "__main__":
    serve().wait_for_termination()
