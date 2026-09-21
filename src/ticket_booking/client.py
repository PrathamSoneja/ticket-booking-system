import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4
import grpc

GEN = Path(__file__).resolve().parent / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc

class TicketClient:
    def __init__(self, server_address: str = "127.0.0.1:50051"):
        self.server_address = server_address
        self.token: str | None = None
        self.username: str | None = None
        self._channel: grpc.Channel | None = None
        self._stub: pb_grpc.ClientServiceStub | None = None
        self._connect()

    def _connect(self) -> None:
        if self._channel:
            self._channel.close()
        self._channel = grpc.insecure_channel(self.server_address)
        self._stub = pb_grpc.ClientServiceStub(self._channel)

    def close(self) -> None:
        if self._channel:
            self._channel.close()

    def login(self, username: str, password: str) -> tuple[bool, str]:
        res = self._stub.Login(pb.LoginRequest(username=username, password=password))
        if res.status == "OK":
            self.token, self.username = res.token, username
            return True, f"Login successful as '{username}'. Token acquired."
        return False, f"Login failed: {res.message} (status: {res.status})"

    def logout(self) -> tuple[bool, str]:
        if not self.token:
            return False, "Not logged in."
        res = self._stub.Logout(pb.LogoutRequest(token=self.token))
        if res.status == "OK":
            self.token = self.username = None
            return True, "Logout successful."
        return False, f"Logout failed: {res.message}"

    def _get(self, kind: str, data: dict[str, str]) -> tuple[str, list[dict], str]:
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        req = pb.GetRequest(token=self.token, type=kind, params=json.dumps(data).encode())
        res = self._stub.Get(req)
        items = [] if kind == "FAQ" else [json.loads(item.data.decode()) for item in res.items]
        return res.status, items, res.message

    def get_shows(self) -> tuple[str, list[dict], str]:
        return self._get("SHOWS", {})

    def get_seats(self, show_id: str) -> tuple[str, list[dict], str]:
        return self._get("SEATS", {"show_id": show_id})

    def _post(self, kind: str, data: dict[str, str], request_id: str | None, max_redirects: int) -> tuple[str, str, str]:
        if not self.token:
            return "AUTH_FAILED", "", "Please log in first."
        request_id = request_id or str(uuid4())
        for _ in range(max_redirects):
            req = pb.PostRequest(token=self.token, type=kind, data=json.dumps(data).encode(), request_id=request_id)
            res = self._stub.Post(req)
            if res.status != "NOT_LEADER" or not res.redirect_to:
                return res.status, res.booking_id, res.message
            self.server_address = res.redirect_to
            self._connect()
        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def book_seat(self, show_id: str, seat_id: str, card_number: str = "4242424242424242", amount: float = 50.0, request_id: str | None = None, max_redirects: int = 3) -> tuple[str, str, str]:
        data = {"show_id": show_id, "seat_id": seat_id, "card_number": card_number, "amount": str(amount)}
        return self._post("BOOK_SEAT", data, request_id, max_redirects)

    def cancel_seat(self, booking_id: str, request_id: str | None = None, max_redirects: int = 3) -> tuple[str, str, str]:
        return self._post("CANCEL_SEAT", {"booking_id": booking_id}, request_id, max_redirects)

    def ask_faq(self, query: str, context: str = "", show_id: str = "") -> str:
        status, _, msg = self._get("FAQ", {"query": query, "context": context, "show_id": show_id})
        return msg if status == "OK" else f"FAQ failed ({status}): {msg}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="127.0.0.1:50051")
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="wonderland")
    parser.add_argument("--action", choices=["shows", "seats", "book", "cancel", "faq"], default="shows")
    parser.add_argument("--show-id", default="show-1")
    parser.add_argument("--seat-id", default="A1")
    parser.add_argument("--booking-id", default="")
    parser.add_argument("--query", default="How do I cancel a booking?")
    args = parser.parse_args()
    client = TicketClient(args.server)
    ok, msg = client.login(args.username, args.password)
    if not ok:
        raise SystemExit(msg)
    if args.action == "shows":
        print(client.get_shows())
    elif args.action == "seats":
        print(client.get_seats(args.show_id))
    elif args.action == "book":
        print(client.book_seat(args.show_id, args.seat_id))
    elif args.action == "cancel":
        print(client.cancel_seat(args.booking_id))
    else:
        print(client.ask_faq(args.query, show_id=args.show_id))
    client.close()


if __name__ == "__main__":
    main()
