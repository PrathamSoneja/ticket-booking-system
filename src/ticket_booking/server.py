import argparse
import json
import logging
import sys
from concurrent import futures
from pathlib import Path

import grpc

GEN_DIR_PATH = Path(__file__).resolve().parent / "generated"
if str(GEN_DIR_PATH) not in sys.path:
    sys.path.insert(0, str(GEN_DIR_PATH))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.application import BookingAppMain
from ticket_booking.payment import PaymentGateway

logger_thing = logging.getLogger("ticket_booking")


class ClientServer(pb_grpc.ClientServiceServicer):
    def __init__(self, app_obj=None, pay_gateway=None, am_i_leader=True, leader_addr="", llm_addr=""):
        if app_obj:
            self.app_obj = app_obj
        else:
            self.app_obj = BookingAppMain(pay_gateway=pay_gateway)

        if pay_gateway:
            self.app_obj.pay_gateway = pay_gateway

        self.am_i_leader = am_i_leader
        self.leader_addr = leader_addr

        if llm_addr:
            self.llm_channel = grpc.insecure_channel(llm_addr)
            self.llm_stub = pb_grpc.LLMServiceStub(self.llm_channel)
        else:
            self.llm_channel = None
            self.llm_stub = None

    def close(self):
        if self.llm_channel:
            self.llm_channel.close()

    def Login(self, req, ctx):
        out = self.app_obj.login(req.username, req.password)
        return pb.LoginResponse(status=out.status_code, token=out.session_token, message=out.info_msg)

    def Signup(self, req, ctx):
        out = self.app_obj.signup(req.username, req.password)
        return pb.LoginResponse(status=out.status_code, token=out.session_token, message=out.info_msg)

    def Logout(self, req, ctx):
        out = self.app_obj.logout(req.token)
        return pb.StatusResponse(status=out.status, message=out.message)

    def Get(self, req, ctx):
        raw_bytes = req.params
        if not raw_bytes:
            parsed_data = {}
            parse_err = ""
        else:
            try:
                parsed_data = json.loads(raw_bytes.decode())
                if not isinstance(parsed_data, dict):
                    parsed_data = None
                    parse_err = "Request payload must be a JSON object."
                else:
                    parse_err = ""
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_data = None
                parse_err = "Request payload is not valid JSON."

        if parse_err:
            return pb.GetResponse(status="INVALID_REQUEST", message=parse_err)

        clean_params = {}
        for k in parsed_data:
            clean_params[str(k)] = str(parsed_data[k])

        status_val, item_list, msg_val = self.app_obj.get(req.token, req.type, clean_params)
        if status_val != "OK":
            return pb.GetResponse(status=status_val, message=msg_val)

        if req.type == "FAQ":
            q = clean_params.get("query", "").strip()
            if not q:
                return pb.GetResponse(status="INVALID_REQUEST", message="FAQ requests require a query parameter.")
            if self.llm_stub is None:
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service is not configured.")
            try:
                ctx_str = self.app_obj.faq_context(q, clean_params)
                llm_resp = self.llm_stub.GetLLMAnswer(pb.LLMRequest(request_id=req.token, query=q, context=ctx_str), timeout=10)
                answer_text = llm_resp.answer
            except grpc.RpcError as err:
                logger_thing.warning("LLM RPC failed: %s", err)
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service could not be reached.")
            return pb.GetResponse(status="OK", message=answer_text)

        proto_item_list = []
        for it in item_list:
            item_key = it.get("show_id") or it.get("seat_id") or ""
            proto_item_list.append(pb.Item(id=item_key, data=json.dumps(it).encode()))

        return pb.GetResponse(status=status_val, items=proto_item_list, message=msg_val)

    def Post(self, req, ctx):
        if not self.am_i_leader:
            return pb.StatusResponse(status="NOT_LEADER", message="Redirect to current cluster leader.", redirect_to=self.leader_addr)

        raw_bytes = req.data
        if not raw_bytes:
            parsed_data = {}
            parse_err = ""
        else:
            try:
                parsed_data = json.loads(raw_bytes.decode())
                if not isinstance(parsed_data, dict):
                    parsed_data = None
                    parse_err = "Request payload must be a JSON object."
                else:
                    parse_err = ""
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_data = None
                parse_err = "Request payload is not valid JSON."

        if parse_err:
            return pb.StatusResponse(status="INVALID_REQUEST", message=parse_err)

        clean_data = {}
        for k in parsed_data:
            v = parsed_data[k]
            if v is None:
                clean_data[str(k)] = ""
            else:
                clean_data[str(k)] = str(v)

        out = self.app_obj.post(req.token, req.type, clean_data, req.request_id)
        return pb.StatusResponse(status=out.status, message=out.message, booking_id=out.booking_id)


def serve(app_obj=None, pay_gateway=None, port=50051, am_i_leader=True, leader_addr="", llm_addr="", max_workers=64, bind_addr="[::]"):
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_ClientServiceServicer_to_server(ClientServer(app_obj, pay_gateway, am_i_leader, leader_addr, llm_addr), srv)
    bound_port = srv.add_insecure_port(f"{bind_addr}:{port}")
    srv.start()
    return srv, bound_port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--llm-server", default="127.0.0.1:50055")
    parser.add_argument("--follower", action="store_true")
    parser.add_argument("--leader", default="")
    args = parser.parse_args()

    srv, bound_port = serve(port=args.port, am_i_leader=not args.follower, leader_addr=args.leader, llm_addr=args.llm_server)
    print(f"ClientService listening on port {bound_port} (leader={not args.follower})")
    srv.wait_for_termination()


if __name__ == "__main__":
    main()
