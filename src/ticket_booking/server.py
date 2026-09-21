
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
from ticket_booking.application import TicketApplication
from ticket_booking.payment import PaymentGateway

log = logging.getLogger("ticket_booking")


def _data(raw: bytes) -> tuple[dict[str, object] | None, str]:
    if not raw:
        return {}, ""
    try:
        data = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "Request payload is not valid JSON."
    if not isinstance(data, dict):
        return None, "Request payload must be a JSON object."
    return data, ""


class ClientServer(pb_grpc.ClientServiceServicer):
    def __init__(self, app: TicketApplication | None = None, payment_gateway: PaymentGateway | None = None, is_leader: bool = True, leader_address: str = "", llm_address: str = ""):
        self.app = app or TicketApplication(payment=payment_gateway)
        if payment_gateway:
            self.app.payment = payment_gateway
        self.is_leader = is_leader
        self.leader_address = leader_address
        self._llm_channel = grpc.insecure_channel(llm_address) if llm_address else None
        self._llm_stub = pb_grpc.LLMServiceStub(self._llm_channel) if self._llm_channel else None

    def close(self) -> None:
        if self._llm_channel:
            self._llm_channel.close()

    def Login(self, req: pb.LoginRequest, ctx: grpc.ServicerContext) -> pb.LoginResponse:
        out = self.app.login(req.username, req.password)
        return pb.LoginResponse(status=out.status, token=out.token, message=out.message)

    def Logout(self, req: pb.LogoutRequest, ctx: grpc.ServicerContext) -> pb.StatusResponse:
        out = self.app.logout(req.token)
        return pb.StatusResponse(status=out.status, message=out.message)

    def Get(self, req: pb.GetRequest, ctx: grpc.ServicerContext) -> pb.GetResponse:
        data, error = _data(req.params)
        if error:
            return pb.GetResponse(status="INVALID_REQUEST", message=error)
        params = {str(key): str(value) for key, value in data.items()}
        status, items, msg = self.app.get(req.token, req.type, params)
        if status != "OK":
            return pb.GetResponse(status=status, message=msg)
        if req.type == "FAQ":
            query = params.get("query", "").strip()
            if not query:
                return pb.GetResponse(status="INVALID_REQUEST", message="FAQ requests require a query parameter.")
            if self._llm_stub is None:
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service is not configured.")
            try:
                answer = self._llm_stub.GetLLMAnswer(pb.LLMRequest(request_id=req.token, query=query, context=self.app.faq_context(query, params)), timeout=10).answer
            except grpc.RpcError as err:
                log.warning("LLM RPC failed: %s", err)
                return pb.GetResponse(status="LLM_UNAVAILABLE", message="The FAQ service could not be reached.")
            return pb.GetResponse(status="OK", message=answer)
        proto_items = [pb.Item(id=item.get("show_id") or item.get("seat_id") or "", data=json.dumps(item).encode()) for item in items]
        return pb.GetResponse(status=status, items=proto_items, message=msg)

    def Post(self, req: pb.PostRequest, ctx: grpc.ServicerContext) -> pb.StatusResponse:
        if not self.is_leader:
            return pb.StatusResponse(status="NOT_LEADER", message="Redirect to current cluster leader.", redirect_to=self.leader_address)
        data, error = _data(req.data)
        if error:
            return pb.StatusResponse(status="INVALID_REQUEST", message=error)
        out = self.app.post(req.token, req.type, {str(key): "" if value is None else str(value) for key, value in data.items()}, req.request_id)
        return pb.StatusResponse(status=out.status, message=out.message, booking_id=out.booking_id)


def serve(app: TicketApplication | None = None, payment_gateway: PaymentGateway | None = None, port: int = 50051, is_leader: bool = True, leader_address: str = "", llm_address: str = "", max_workers: int = 10, bind_address: str = "[::]") -> tuple[grpc.Server, int]:
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_ClientServiceServicer_to_server(ClientServer(app, payment_gateway, is_leader, leader_address, llm_address), srv)
    bound = srv.add_insecure_port(f"{bind_address}:{port}")
    srv.start()
    return srv, bound


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--llm-server", default="127.0.0.1:50055")
    parser.add_argument("--follower", action="store_true")
    parser.add_argument("--leader", default="")
    args = parser.parse_args()
    srv, bound = serve(port=args.port, is_leader=not args.follower, leader_address=args.leader, llm_address=args.llm_server)
    print(f"ClientService listening on port {bound} (leader={not args.follower})")
    srv.wait_for_termination()


if __name__ == "__main__":
    main()
