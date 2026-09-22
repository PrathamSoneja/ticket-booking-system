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


class Client:
    def __init__(self, addr="127.0.0.1:50051"):
        self.addr = addr
        self.token = None
        self.user = None
        self.channel = grpc.insecure_channel(self.addr)
        self.stub = pb_grpc.ClientServiceStub(self.channel)

    def close(self):
        if self.channel:
            self.channel.close()

    def login(self, uname, pw):
        res = self.stub.Login(pb.LoginRequest(username=uname, password=pw))
        if res.status == "OK":
            self.token = res.token
            self.user = uname
            return True, f"Login successful as '{uname}'. Token acquired."
        else:
            return False, f"Login failed: {res.message} (status: {res.status})"

    def signup(self, uname, pw):
        res = self.stub.Signup(pb.LoginRequest(username=uname, password=pw))
        if res.status == "OK":
            self.token = res.token
            self.user = uname
            return True, f"Signup successful as '{uname}'. Token acquired."
        else:
            return False, f"Signup failed: {res.message} (status: {res.status})"

    def logout(self):
        if not self.token:
            return False, "Not logged in."
        res = self.stub.Logout(pb.LogoutRequest(token=self.token))
        if res.status == "OK":
            self.token = None
            self.user = None
            return True, "Logout successful."
        else:
            return False, f"Logout failed: {res.message}"

    def get_shows(self):
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        res = self.stub.Get(pb.GetRequest(token=self.token, type="SHOWS", params=json.dumps({}).encode()))
        items = [json.loads(it.data.decode()) for it in res.items]
        return res.status, items, res.message

    def get_seats(self, show_id):
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        res = self.stub.Get(
            pb.GetRequest(token=self.token, type="SEATS", params=json.dumps({"show_id": show_id}).encode())
        )
        items = [json.loads(it.data.decode()) for it in res.items]
        return res.status, items, res.message

    def book_seat(self, show_id, seat_id, card="4242424242424242", amount=50.0, req_id=None, hops=3):
        if not self.token:
            return "AUTH_FAILED", "", "Please log in first."
        body = {"show_id": show_id, "seat_id": seat_id, "card_number": card, "amount": str(amount)}
        if req_id:
            rid = req_id
        else:
            rid = str(uuid4())
        n = 0
        while n < hops:
            res = self.stub.Post(
                pb.PostRequest(token=self.token, type="BOOK_SEAT", data=json.dumps(body).encode(), request_id=rid)
            )
            if res.status != "NOT_LEADER" or not res.redirect_to:
                return res.status, res.booking_id, res.message
            self.addr = res.redirect_to
            if self.channel:
                self.channel.close()
            self.channel = grpc.insecure_channel(self.addr)
            self.stub = pb_grpc.ClientServiceStub(self.channel)
            n = n + 1
        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def cancel_seat(self, booking_id, req_id=None, hops=3):
        if not self.token:
            return "AUTH_FAILED", "", "Please log in first."
        body = {"booking_id": booking_id}
        if req_id:
            rid = req_id
        else:
            rid = str(uuid4())
        n = 0
        while n < hops:
            res = self.stub.Post(
                pb.PostRequest(token=self.token, type="CANCEL_SEAT", data=json.dumps(body).encode(), request_id=rid)
            )
            if res.status != "NOT_LEADER" or not res.redirect_to:
                return res.status, res.booking_id, res.message
            self.addr = res.redirect_to
            if self.channel:
                self.channel.close()
            self.channel = grpc.insecure_channel(self.addr)
            self.stub = pb_grpc.ClientServiceStub(self.channel)
            n = n + 1
        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def ask_faq(self, query, context="", show_id=""):
        if not self.token:
            return f"FAQ failed (AUTH_FAILED): Please log in first."
        res = self.stub.Get(
            pb.GetRequest(
                token=self.token,
                type="FAQ",
                params=json.dumps({"query": query, "context": context, "show_id": show_id}).encode(),
            )
        )
        if res.status == "OK":
            return res.message
        else:
            return f"FAQ failed ({res.status}): {res.message}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="127.0.0.1:50051")
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="wonderland")
    parser.add_argument("--action", choices=["signup", "shows", "seats", "book", "cancel", "faq"], default="shows")
    parser.add_argument("--show-id", default="show-1")
    parser.add_argument("--seat-id", default="A1")
    parser.add_argument("--booking-id", default="")
    parser.add_argument("--query", default="How do I cancel a booking?")
    args = parser.parse_args()
    c = Client(args.server)
    if args.action == "signup":
        ok, msg = c.signup(args.username, args.password)
        print(msg)
        c.close()
        if not ok:
            raise SystemExit(msg)
        return
    ok, msg = c.login(args.username, args.password)
    if not ok:
        raise SystemExit(msg)
    if args.action == "shows":
        print(c.get_shows())
    elif args.action == "seats":
        print(c.get_seats(args.show_id))
    elif args.action == "book":
        print(c.book_seat(args.show_id, args.seat_id))
    elif args.action == "cancel":
        print(c.cancel_seat(args.booking_id))
    else:
        print(c.ask_faq(args.query, show_id=args.show_id))
    c.close()


if __name__ == "__main__":
    main()
