import json
import sys
import urllib.error
import urllib.request
from concurrent import futures
from pathlib import Path

import grpc

from ticket_booking.faq_data import FaqSearchEngine

GEN_DIR_PATH = Path(__file__).resolve().parent / "generated"
if str(GEN_DIR_PATH) not in sys.path:
    sys.path.insert(0, str(GEN_DIR_PATH))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc


class LLMService(pb_grpc.LLMServiceServicer):
    def __init__(self, faq_engine=None, ollama_url="http://127.0.0.1:11434/api/generate", model_name="llama3.2:1b", wait_secs=5.0, fake_ask_fn=None):
        if faq_engine:
            self.faq_engine = faq_engine
        else:
            self.faq_engine = FaqSearchEngine()

        self.ollama_url = ollama_url
        self.model_name = model_name
        self.wait_secs = wait_secs
        self.fake_ask_fn = fake_ask_fn

    def GetLLMAnswer(self, req, ctx):
        context_text = req.context.strip()
        if context_text == "":
            context_text, _ = self.faq_engine.retrieve(req.query)

        prompt_text = (
            "You are a ticket-booking support assistant. Answer only from this context.\n\n"
            f"Context:\n{context_text}\n\nQuestion: {req.query.strip()}"
        )

        final_answer = None
        if self.fake_ask_fn is not None:
            final_answer = self.fake_ask_fn(prompt_text)
        else:
            request_body = json.dumps({"model": self.model_name, "prompt": prompt_text, "stream": False}).encode()
            http_req = urllib.request.Request(
                self.ollama_url,
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(http_req, timeout=self.wait_secs) as resp:
                    resp_json = json.loads(resp.read().decode())
                raw_answer = resp_json.get("response", "").strip()
                if raw_answer:
                    final_answer = raw_answer
                else:
                    final_answer = None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                final_answer = None

        if final_answer is None:
            ctx.abort(grpc.StatusCode.UNAVAILABLE, "Ollama is unavailable.")

        return pb.LLMResponse(request_id=req.request_id, answer=final_answer)


def serve(port=50055, worker_count=16):
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=worker_count))
    pb_grpc.add_LLMServiceServicer_to_server(LLMService(), srv)
    srv.add_insecure_port(f"[::]:{port}")
    srv.start()
    return srv


if __name__ == "__main__":
    server_obj = serve()
    server_obj.wait_for_termination()
