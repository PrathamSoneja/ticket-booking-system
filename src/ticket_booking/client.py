"""Token-aware gRPC client with leader redirect support and human-readable CLI (TASK-CLIENT-1)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

# Ensure generated proto package is in sys.path
_GENERATED_DIR = Path(__file__).resolve().parent / "generated"
if str(_GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(_GENERATED_DIR))

import grpc
import ticket_booking_pb2
import ticket_booking_pb2_grpc


class TicketClient:
    """Client for the Distributed Ticket Booking System with transparent leader redirect handling."""

    def __init__(self, server_address: str = "127.0.0.1:50051", llm_address: str = "127.0.0.1:50055"):
        self.server_address = server_address
        self.llm_address = llm_address  # retained for CLI flags; FAQ is proxied via ClientService
        self.token: str | None = None
        self.username: str | None = None
        self._channel: grpc.Channel | None = None
        self._stub: ticket_booking_pb2_grpc.ClientServiceStub | None = None
        self._connect()

    def _connect(self) -> None:
        if self._channel:
            self._channel.close()
        self._channel = grpc.insecure_channel(self.server_address)
        self._stub = ticket_booking_pb2_grpc.ClientServiceStub(self._channel)

    def close(self) -> None:
        if self._channel:
            self._channel.close()

    def login(self, username: str, password: str) -> tuple[bool, str]:
        """Authenticate user and store session token."""
        assert self._stub is not None
        req = ticket_booking_pb2.LoginRequest(username=username, password=password)
        resp = self._stub.Login(req)
        if resp.status == "OK":
            self.token = resp.token
            self.username = username
            return True, f"Login successful as '{username}'. Token acquired."
        return False, f"Login failed: {resp.message} (status: {resp.status})"

    def logout(self) -> tuple[bool, str]:
        """Invalidate session token."""
        if not self.token:
            return False, "Not logged in."
        assert self._stub is not None
        req = ticket_booking_pb2.LogoutRequest(token=self.token)
        resp = self._stub.Logout(req)
        if resp.status == "OK":
            self.token = None
            self.username = None
            return True, "Logout successful."
        return False, f"Logout failed: {resp.message}"

    def get_shows(self) -> tuple[str, list[dict], str]:
        """Retrieve list of available shows."""
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        assert self._stub is not None
        req = ticket_booking_pb2.GetRequest(token=self.token, type="SHOWS", params=b"{}")
        resp = self._stub.Get(req)
        shows = [json.loads(item.data.decode("utf-8")) for item in resp.items]
        return resp.status, shows, resp.message

    def get_seats(self, show_id: str) -> tuple[str, list[dict], str]:
        """Retrieve seat availability map for a specific show."""
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        assert self._stub is not None
        params_bytes = json.dumps({"show_id": show_id}).encode("utf-8")
        req = ticket_booking_pb2.GetRequest(token=self.token, type="SEATS", params=params_bytes)
        resp = self._stub.Get(req)
        seats = [json.loads(item.data.decode("utf-8")) for item in resp.items]
        return resp.status, seats, resp.message

    def book_seat(
        self,
        show_id: str,
        seat_id: str,
        card_number: str = "4242424242424242",
        amount: float = 50.0,
        request_id: str | None = None,
        max_redirects: int = 3,
    ) -> tuple[str, str, str]:
        """Book a seat with automatic leader redirect handling."""
        if not self.token:
            return "AUTH_FAILED", "", "Please log in first."
        req_id = request_id or str(uuid4())
        payload = json.dumps({
            "show_id": show_id,
            "seat_id": seat_id,
            "card_number": card_number,
            "amount": str(amount),
        }).encode("utf-8")

        for _ in range(max_redirects):
            assert self._stub is not None
            req = ticket_booking_pb2.PostRequest(
                token=self.token,
                type="BOOK_SEAT",
                data=payload,
                request_id=req_id,
            )
            resp = self._stub.Post(req)

            # Handle Raft leader redirection
            if resp.status == "NOT_LEADER" and resp.redirect_to:
                print(f"[CLIENT] Node {self.server_address} is not leader. Redirecting to leader: {resp.redirect_to}")
                self.server_address = resp.redirect_to
                self._connect()
                continue

            return resp.status, resp.booking_id, resp.message

        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def cancel_seat(
        self,
        booking_id: str,
        request_id: str | None = None,
        max_redirects: int = 3,
    ) -> tuple[str, str, str]:
        """Cancel a previously confirmed seat reservation."""
        if not self.token:
            return "AUTH_FAILED", "", "Please log in first."
        req_id = request_id or str(uuid4())
        payload = json.dumps({"booking_id": booking_id}).encode("utf-8")

        for _ in range(max_redirects):
            assert self._stub is not None
            req = ticket_booking_pb2.PostRequest(
                token=self.token,
                type="CANCEL_SEAT",
                data=payload,
                request_id=req_id,
            )
            resp = self._stub.Post(req)

            if resp.status == "NOT_LEADER" and resp.redirect_to:
                print(f"[CLIENT] Node {self.server_address} is not leader. Redirecting to leader: {resp.redirect_to}")
                self.server_address = resp.redirect_to
                self._connect()
                continue

            return resp.status, resp.booking_id, resp.message

        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def ask_faq(self, query: str, context: str = "", show_id: str = "") -> str:
        """Ask the FAQ chatbot through the application server (LLM is not client-reachable)."""
        if not self.token:
            return "Please log in first."
        assert self._stub is not None
        payload = {"query": query, "context": context}
        if show_id:
            payload["show_id"] = show_id
        req = ticket_booking_pb2.GetRequest(
            token=self.token,
            type="FAQ",
            params=json.dumps(payload).encode("utf-8"),
        )
        resp = self._stub.Get(req)
        if resp.status != "OK":
            return f"FAQ failed ({resp.status}): {resp.message}"
        return resp.message


def _run_interactive(client: TicketClient) -> None:
    print("Commands: shows | seats <show_id> | book <show_id> <seat_id> | cancel <booking_id> | faq <question> | quit")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        parts = line.split()
        command = parts[0].lower()
        if command in {"quit", "exit"}:
            return
        if command == "shows":
            status, shows, err = client.get_shows()
            print(f"[{status}] {err}".strip())
            for show in shows:
                print(f"  [{show.get('show_id')}] {show.get('name')} @ {show.get('venue')}")
        elif command == "seats" and len(parts) >= 2:
            status, seats, err = client.get_seats(parts[1])
            print(f"[{status}] {err}".strip())
            for seat in seats:
                print(f"  {seat.get('seat_id')}: {seat.get('status')}")
        elif command == "book" and len(parts) >= 3:
            status, booking_id, err = client.book_seat(parts[1], parts[2])
            print(f"[{status}] booking_id={booking_id} {err}")
        elif command == "cancel" and len(parts) >= 2:
            status, booking_id, err = client.cancel_seat(parts[1])
            print(f"[{status}] booking_id={booking_id} {err}")
        elif command == "faq":
            question = line.split(" ", 1)[1] if " " in line else ""
            print(client.ask_faq(question))
        else:
            print("Unrecognized command.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Distributed Ticket Booking System CLI")
    parser.add_argument("--server", default="127.0.0.1:50051", help="App server address (host:port)")
    parser.add_argument("--llm-server", default="127.0.0.1:50055", help="LLM server address (host:port)")
    parser.add_argument("--username", default="alice", help="Username")
    parser.add_argument("--password", default="wonderland", help="Password")
    parser.add_argument("--action", choices=["shows", "seats", "book", "cancel", "faq", "interactive"], default="shows")
    parser.add_argument("--show-id", default="show-1", help="Show ID for seats or booking")
    parser.add_argument("--seat-id", default="A1", help="Seat ID for booking")
    parser.add_argument("--booking-id", default="", help="Booking ID for cancellation")
    parser.add_argument("--query", default="How do I cancel a booking?", help="FAQ query for chatbot")

    args = parser.parse_args()
    client = TicketClient(server_address=args.server, llm_address=args.llm_server)

    print(f"=== Connecting to App Server at {args.server} ===")
    ok, msg = client.login(args.username, args.password)
    print(f"[{'OK' if ok else 'FAIL'}] {msg}")
    if not ok:
        sys.exit(1)

    if args.action == "shows":
        status, shows, err = client.get_shows()
        print(f"\n--- Shows (status: {status}) ---")
        for s in shows:
            print(f"[{s.get('show_id')}] {s.get('name')} @ {s.get('venue')} (Starts: {s.get('start_time')})")

    elif args.action == "seats":
        status, seats, err = client.get_seats(args.show_id)
        print(f"\n--- Seats for {args.show_id} (status: {status}) ---")
        for st in seats:
            print(f"Seat {st.get('seat_id')}: {st.get('status')}")

    elif args.action == "book":
        status, booking_id, err = client.book_seat(args.show_id, args.seat_id)
        print(f"\n[BOOK] Status: {status} | Booking ID: {booking_id} | Message: {err}")

    elif args.action == "cancel":
        status, booking_id, err = client.cancel_seat(args.booking_id)
        print(f"\n[CANCEL] Status: {status} | Booking ID: {booking_id} | Message: {err}")

    elif args.action == "faq":
        print(f"\n--- FAQ Chatbot Query: '{args.query}' ---")
        ans = client.ask_faq(args.query, show_id=args.show_id)
        print(f"Answer:\n{ans}")

    elif args.action == "interactive":
        _run_interactive(client)

    client.close()


if __name__ == "__main__":
    main()
