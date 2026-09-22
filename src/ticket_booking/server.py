import argparse
import json
import logging
import sys
from concurrent import futures
from pathlib import Path

import grpc

GEN = Path(__file__).resolve().parent / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.application import BookingApp

log = logging.getLogger("ticket_booking")


class AppServer(pb_grpc.ClientServiceServicer):
    def __init__(self, app=None, pay=None, is_leader=True, leader_url="", llm_url=""):
        if app:
            self.app = app
        else:
            self.app = BookingApp(pay=pay)
        if pay:
            self.app.pay = pay
        self.is_leader = is_leader
        self.leader_url = leader_url
        if llm_url:
            self.llm_ch = grpc.insecure_channel(llm_url)
            self.llm = pb_grpc.LLMServiceStub(self.llm_ch)
        else:
            self.llm_ch = None
            self.llm = None

    def close(self):
        if self.llm_ch:
            self.llm_ch.close()

    def Login(self, req, ctx):
        out = self.app.login(req.username, req.password)
        return pb.LoginResponse(status=out.status, token=out.token, message=out.message)

    def Signup(self, req, ctx):
        out = self.app.signup(req.username, req.password)
        return pb.LoginResponse(status=out.status, token=out.token, message=out.message)

    def Logout(self, req, ctx):
        out = self.app.logout(req.token)
        return pb.StatusResponse(status=out.status, message=out.message)

    def Get(self, req, ctx):
        raw = req.params
        if not raw:
            data = {}
            err = ""
        else:
            try:
                data = json.loads(raw.decode())
                if not isinstance(data, dict):
                    data = None
                    err = "Request payload must be a JSON object."
                else:
                    err = ""
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
                err = "Request payload is not valid JSON."
        if err:
            return pb.GetResponse(status="INVALID_REQUEST", message=err)
        params = {}
        for k in data:
            params[str(k)] = str(data[k])
        status, items, msg = self.app.get(req.token, req.type, params)
        if status != "OK":
            return pb.GetResponse(status=status, message=msg)
        if req.type == "FAQ":
            q = params.get("query", "").strip()
            if not q:
                return pb.GetResponse(status="INVALID_REQUEST", message="FAQ requests require a query parameter.")
            if self.llm is None:
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service is not configured.")
            try:
                ctx_str = self.app.faq_context(q, params)
                ans = self.llm.GetLLMAnswer(
                    pb.LLMRequest(request_id=req.token, query=q, context=ctx_str), timeout=60
                ).answer
            except grpc.RpcError as err:
                log.warning("LLM RPC failed: %s", err)
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service could not be reached.")
            return pb.GetResponse(status="OK", message=ans)
        proto_items = []
        for it in items:
            proto_items.append(pb.Item(id=it.get("show_id") or it.get("seat_id") or "", data=json.dumps(it).encode()))
        return pb.GetResponse(status=status, items=proto_items, message=msg)

    def Post(self, req, ctx):
        if not self.is_leader:
            return pb.StatusResponse(
                status="NOT_LEADER", message="Redirect to current cluster leader.", redirect_to=self.leader_url
            )
        raw = req.data
        if not raw:
            data = {}
            err = ""
        else:
            try:
                data = json.loads(raw.decode())
                if not isinstance(data, dict):
                    data = None
                    err = "Request payload must be a JSON object."
                else:
                    err = ""
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
                err = "Request payload is not valid JSON."
        if err:
            return pb.StatusResponse(status="INVALID_REQUEST", message=err)
        clean = {}
        for k in data:
            v = data[k]
            if v is None:
                clean[str(k)] = ""
            else:
                clean[str(k)] = str(v)
        out = self.app.post(req.token, req.type, clean, req.request_id)
        return pb.StatusResponse(status=out.status, message=out.message, booking_id=out.booking_id)


def serve(app=None, pay=None, port=50051, is_leader=True, leader_url="", llm_url="", workers=64, bind="[::]"):
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    pb_grpc.add_ClientServiceServicer_to_server(AppServer(app, pay, is_leader, leader_url, llm_url), srv)
    bound = srv.add_insecure_port(f"{bind}:{port}")
    srv.start()
    return srv, bound


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--llm-server", default="127.0.0.1:50055")
    parser.add_argument("--follower", action="store_true")
    parser.add_argument("--leader", default="")
    args = parser.parse_args()
    srv, bound = serve(port=args.port, is_leader=not args.follower, leader_url=args.leader, llm_url=args.llm_server)
    print(f"ClientService listening on port {bound} (leader={not args.follower})")
    srv.wait_for_termination()


if __name__ == "__main__":
    main()
