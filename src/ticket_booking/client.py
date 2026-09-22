import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

import grpc

GEN_DIR_PATH = Path(__file__).resolve().parent / "generated"
if str(GEN_DIR_PATH) not in sys.path:
    sys.path.insert(0, str(GEN_DIR_PATH))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc


class TicketClient:
    def __init__(self, server_addr="127.0.0.1:50051"):
        self.server_addr = server_addr
        self.my_token = None
        self.my_username = None
        self.channel_obj = grpc.insecure_channel(self.server_addr)
        self.stub_obj = pb_grpc.ClientServiceStub(self.channel_obj)

    def close(self):
        if self.channel_obj:
            self.channel_obj.close()

    def login(self, uname, pw):
        res = self.stub_obj.Login(pb.LoginRequest(username=uname, password=pw))
        if res.status == "OK":
            self.my_token = res.token
            self.my_username = uname
            return True, f"Login successful as '{uname}'. Token acquired."
        else:
            return False, f"Login failed: {res.message} (status: {res.status})"

    def signup(self, uname, pw):
        res = self.stub_obj.Signup(pb.LoginRequest(username=uname, password=pw))
        if res.status == "OK":
            self.my_token = res.token
            self.my_username = uname
            return True, f"Signup successful as '{uname}'. Token acquired."
        else:
            return False, f"Signup failed: {res.message} (status: {res.status})"

    def logout(self):
        if not self.my_token:
            return False, "Not logged in."
        res = self.stub_obj.Logout(pb.LogoutRequest(token=self.my_token))
        if res.status == "OK":
            self.my_token = None
            self.my_username = None
            return True, "Logout successful."
        else:
            return False, f"Logout failed: {res.message}"

    def get_shows(self):
        if not self.my_token:
            return "AUTH_FAILED", [], "Please log in first."
        req = pb.GetRequest(token=self.my_token, type="SHOWS", params=json.dumps({}).encode())
        res = self.stub_obj.Get(req)
        item_list = [json.loads(it.data.decode()) for it in res.items]
        return res.status, item_list, res.message

    def get_seats(self, show_id):
        if not self.my_token:
            return "AUTH_FAILED", [], "Please log in first."
        req_data = {"show_id": show_id}
        req = pb.GetRequest(token=self.my_token, type="SEATS", params=json.dumps(req_data).encode())
        res = self.stub_obj.Get(req)
        item_list = [json.loads(it.data.decode()) for it in res.items]
        return res.status, item_list, res.message

    def book_seat(self, show_id, seat_id, card_number="4242424242424242", amount=50.0, request_id=None, max_redirects=3):
        if not self.my_token:
            return "AUTH_FAILED", "", "Please log in first."

        post_data = {"show_id": show_id, "seat_id": seat_id, "card_number": card_number, "amount": str(amount)}
        real_req_id = request_id if request_id else str(uuid4())

        tries = 0
        while tries < max_redirects:
            req = pb.PostRequest(token=self.my_token, type="BOOK_SEAT", data=json.dumps(post_data).encode(), request_id=real_req_id)
            res = self.stub_obj.Post(req)
            if res.status != "NOT_LEADER" or not res.redirect_to:
                return res.status, res.booking_id, res.message

            self.server_addr = res.redirect_to
            if self.channel_obj:
                self.channel_obj.close()
            self.channel_obj = grpc.insecure_channel(self.server_addr)
            self.stub_obj = pb_grpc.ClientServiceStub(self.channel_obj)
            tries = tries + 1

        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def cancel_seat(self, booking_id, request_id=None, max_redirects=3):
        if not self.my_token:
            return "AUTH_FAILED", "", "Please log in first."

        post_data = {"booking_id": booking_id}
        real_req_id = request_id if request_id else str(uuid4())

        tries = 0
        while tries < max_redirects:
            req = pb.PostRequest(token=self.my_token, type="CANCEL_SEAT", data=json.dumps(post_data).encode(), request_id=real_req_id)
            res = self.stub_obj.Post(req)
            if res.status != "NOT_LEADER" or not res.redirect_to:
                return res.status, res.booking_id, res.message

            self.server_addr = res.redirect_to
            if self.channel_obj:
                self.channel_obj.close()
            self.channel_obj = grpc.insecure_channel(self.server_addr)
            self.stub_obj = pb_grpc.ClientServiceStub(self.channel_obj)
            tries = tries + 1

        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def ask_faq(self, query_text, context_text="", show_id=""):
        if not self.my_token:
            return f"FAQ failed (AUTH_FAILED): Please log in first."

        req_data = {"query": query_text, "context": context_text, "show_id": show_id}
        req = pb.GetRequest(token=self.my_token, type="FAQ", params=json.dumps(req_data).encode())
        res = self.stub_obj.Get(req)
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

    my_client = TicketClient(args.server)
    if args.action == "signup":
        ok, msg = my_client.signup(args.username, args.password)
        print(msg)
        my_client.close()
        if not ok:
            raise SystemExit(msg)
        return

    login_ok, login_msg = my_client.login(args.username, args.password)
    if not login_ok:
        raise SystemExit(login_msg)

    if args.action == "shows":
        print(my_client.get_shows())
    elif args.action == "seats":
        print(my_client.get_seats(args.show_id))
    elif args.action == "book":
        print(my_client.book_seat(args.show_id, args.seat_id))
    elif args.action == "cancel":
        print(my_client.cancel_seat(args.booking_id))
    else:
        print(my_client.ask_faq(args.query, show_id=args.show_id))

    my_client.close()


if __name__ == "__main__":
    main()
