"""gRPC ClientService server implementation connecting ClientService to TicketApplication."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent import futures
from pathlib import Path

_GENERATED_DIR = Path(__file__).resolve().parent / "generated"
if str(_GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(_GENERATED_DIR))

import grpc
import ticket_booking_pb2
import ticket_booking_pb2_grpc
from ticket_booking.application import TicketApplication
from ticket_booking.payment import PaymentGateway

logger = logging.getLogger("ClientServer")


def _decode_json_object(raw: bytes) -> tuple[dict[str, str] | None, str]:
    if not raw:
        return {}, ""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "Request payload is not valid JSON."
    if not isinstance(parsed, dict):
        return None, "Request payload must be a JSON object."
    return {str(key): parsed[key] if isinstance(parsed[key], str) else parsed[key] for key in parsed}, ""


class ClientServer(ticket_booking_pb2_grpc.ClientServiceServicer):
    """gRPC servicer for end-user operations: login, logout, browse (get), and book/cancel (post)."""

    def __init__(
        self,
        app: TicketApplication | None = None,
        payment_gateway: PaymentGateway | None = None,
        is_leader: bool = True,
        leader_address: str = "",
        llm_address: str = "",
    ):
        self.app = app or TicketApplication(payment=payment_gateway)
        if payment_gateway is not None:
            self.app.payment = payment_gateway
        self.is_leader = is_leader
        self.leader_address = leader_address
        self.llm_address = llm_address
        self._llm_channel: grpc.Channel | None = None
        self._llm_stub: ticket_booking_pb2_grpc.LLMServiceStub | None = None
        if llm_address:
            self._llm_channel = grpc.insecure_channel(llm_address)
            self._llm_stub = ticket_booking_pb2_grpc.LLMServiceStub(self._llm_channel)

    def close(self) -> None:
        if self._llm_channel is not None:
            self._llm_channel.close()
            self._llm_channel = None
            self._llm_stub = None

    def _redirect_if_follower(self) -> ticket_booking_pb2.StatusResponse | None:
        if self.is_leader:
            return None
        return ticket_booking_pb2.StatusResponse(
            status="NOT_LEADER",
            message="Redirect to current cluster leader.",
            redirect_to=self.leader_address,
        )

    def _ask_llm(self, request_id: str, query: str, context: str) -> str:
        if self._llm_stub is None:
            return "FAQ service is not configured on this application server."
        response = self._llm_stub.GetLLMAnswer(
            ticket_booking_pb2.LLMRequest(request_id=request_id, query=query, context=context),
            timeout=10,
        )
        return response.answer

    def Login(
        self,
        request: ticket_booking_pb2.LoginRequest,
        context: grpc.ServicerContext,
    ) -> ticket_booking_pb2.LoginResponse:
        res = self.app.login(request.username, request.password)
        return ticket_booking_pb2.LoginResponse(
            status=res.status,
            token=res.token,
            message=res.message,
        )

    def Logout(
        self,
        request: ticket_booking_pb2.LogoutRequest,
        context: grpc.ServicerContext,
    ) -> ticket_booking_pb2.StatusResponse:
        outcome = self.app.logout(request.token)
        return ticket_booking_pb2.StatusResponse(
            status=outcome.status,
            message=outcome.message,
        )

    def Get(
        self,
        request: ticket_booking_pb2.GetRequest,
        context: grpc.ServicerContext,
    ) -> ticket_booking_pb2.GetResponse:
        params, error = _decode_json_object(request.params)
        if error:
            return ticket_booking_pb2.GetResponse(status="INVALID_REQUEST", message=error)

        status, items_data, msg = self.app.get(request.token, request.type, params)
        if status != "OK":
            return ticket_booking_pb2.GetResponse(status=status, message=msg)

        if request.type == "FAQ":
            query = str(params.get("query", "")).strip()
            if not query:
                return ticket_booking_pb2.GetResponse(
                    status="INVALID_REQUEST",
                    message="FAQ requests require a query parameter.",
                )
            extra_context = self.app.faq_context(query, {str(k): str(v) for k, v in params.items()})
            try:
                answer = self._ask_llm(request.token, query, extra_context)
            except grpc.RpcError as exc:
                logger.warning("LLM RPC failed: %s", exc)
                return ticket_booking_pb2.GetResponse(
                    status="LLM_UNAVAILABLE",
                    message="The FAQ service could not be reached.",
                )
            return ticket_booking_pb2.GetResponse(
                status="OK",
                items=[
                    ticket_booking_pb2.Item(id="answer", data=answer.encode("utf-8")),
                ],
                message=answer,
            )

        proto_items = [
            ticket_booking_pb2.Item(
                id=item.get("show_id") or item.get("seat_id") or "",
                data=json.dumps(item).encode("utf-8"),
            )
            for item in items_data
        ]
        return ticket_booking_pb2.GetResponse(
            status=status,
            items=proto_items,
            message=msg,
        )

    def Post(
        self,
        request: ticket_booking_pb2.PostRequest,
        context: grpc.ServicerContext,
    ) -> ticket_booking_pb2.StatusResponse:
        redirected = self._redirect_if_follower()
        if redirected is not None:
            return redirected

        data, error = _decode_json_object(request.data)
        if error:
            return ticket_booking_pb2.StatusResponse(status="INVALID_REQUEST", message=error)

        string_data = {str(key): "" if value is None else str(value) for key, value in data.items()}
        outcome = self.app.post(request.token, request.type, string_data, request.request_id)
        return ticket_booking_pb2.StatusResponse(
            status=outcome.status,
            message=outcome.message,
            booking_id=outcome.booking_id,
        )


def serve(
    app: TicketApplication | None = None,
    payment_gateway: PaymentGateway | None = None,
    port: int = 50051,
    is_leader: bool = True,
    leader_address: str = "",
    llm_address: str = "",
    max_workers: int = 10,
    bind_address: str = "[::]",
) -> tuple[grpc.Server, int]:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    servicer = ClientServer(
        app=app,
        payment_gateway=payment_gateway,
        is_leader=is_leader,
        leader_address=leader_address,
        llm_address=llm_address,
    )
    ticket_booking_pb2_grpc.add_ClientServiceServicer_to_server(servicer, server)
    bound_port = server.add_insecure_port(f"{bind_address}:{port}")
    server.start()
    return server, bound_port


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Ticket booking application server")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--llm-server", default="127.0.0.1:50055")
    parser.add_argument("--follower", action="store_true", help="Reject writes and redirect to --leader")
    parser.add_argument("--leader", default="", help="Leader host:port advertised to clients")
    args = parser.parse_args()
    server, bound = serve(
        port=args.port,
        is_leader=not args.follower,
        leader_address=args.leader,
        llm_address=args.llm_server,
    )
    print(f"ClientService listening on port {bound} (leader={not args.follower})")
    server.wait_for_termination()


if __name__ == "__main__":
    main()
