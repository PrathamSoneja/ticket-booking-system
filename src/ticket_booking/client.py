import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import grpc

GEN = Path(__file__).resolve().parent / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc


class Client:
    def __init__(self, addr="127.0.0.1:50051", timeout=3.0, max_retries=3, backoff=0.25, redirect_map=None):
        if isinstance(addr, (list, tuple, set)):
            seeds = [str(x) for x in addr if str(x).strip()]
        else:
            seeds = [str(addr)]
        self.seed_addresses = seeds or ["127.0.0.1:50051"]
        self.addr = self.seed_addresses[0]
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        # optional translation of leader addresses advertised by servers (e.g. host-facing
        # 127.0.0.1:5005x) into addresses reachable from where this client runs (e.g. app-2:50051)
        self.redirect_map = dict(redirect_map or {})
        self.token = None
        self.user = None
        self.channel = grpc.insecure_channel(self.addr)
        self.stub = pb_grpc.ClientServiceStub(self.channel)

    def _switch_server(self, address):
        if not address:
            return
        address = self.redirect_map.get(str(address), str(address))
        if self.channel:
            self.channel.close()
        self.addr = str(address)
        self.channel = grpc.insecure_channel(self.addr)
        self.stub = pb_grpc.ClientServiceStub(self.channel)

    def _next_seed(self):
        if len(self.seed_addresses) <= 1:
            return self.addr
        idx = self.seed_addresses.index(self.addr) if self.addr in self.seed_addresses else 0
        return self.seed_addresses[(idx + 1) % len(self.seed_addresses)]

    @staticmethod
    def _rpc_failed_response(method_name, err):
        details = getattr(err, "details", lambda: str(err))()
        return SimpleNamespace(
            status="UNAVAILABLE",
            message=f"{method_name} failed: {details}",
            token="",
            items=[],
            redirect_to="",
            booking_id="",
        )

    def _call_with_retry(self, method_name, request, *, redirect_on_not_leader=True):
        attempts = 0
        while attempts <= self.max_retries:
            try:
                method = getattr(self.stub, method_name)
                res = method(request, timeout=self.timeout)
            except grpc.RpcError as err:
                if err.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                    attempts += 1
                    if attempts <= self.max_retries:
                        time.sleep(self.backoff * attempts)
                        self._switch_server(self._next_seed())
                        continue
                    return self._rpc_failed_response(method_name, err)
                return self._rpc_failed_response(method_name, err)
            if res.status == "NO_LEADER" and attempts < self.max_retries:
                # cluster is electing; back off and try another seed
                attempts += 1
                time.sleep(self.backoff * attempts)
                self._switch_server(self._next_seed())
                continue
            if redirect_on_not_leader and res.status == "NOT_LEADER" and getattr(res, "redirect_to", ""):
                self._switch_server(res.redirect_to)
                attempts += 1
                if attempts <= self.max_retries:
                    time.sleep(self.backoff * attempts)
                    continue
            return res
        return SimpleNamespace(status="UNAVAILABLE", message=f"{method_name} failed: request timed out.", token="", items=[], redirect_to="", booking_id="")

    def close(self):
        if self.channel:
            self.channel.close()

    def login(self, uname, pw):
        res = self._call_with_retry("Login", pb.LoginRequest(username=uname, password=pw))
        if res.status == "OK":
            self.token = res.token
            self.user = uname
            return True, f"Login successful as '{uname}'. Token acquired."
        else:
            return False, f"Login failed: {res.message} (status: {res.status})"

    def signup(self, uname, pw):
        res = self._call_with_retry("Signup", pb.LoginRequest(username=uname, password=pw))
        if res.status == "OK":
            self.token = res.token
            self.user = uname
            return True, f"Signup successful as '{uname}'. Token acquired."
        else:
            return False, f"Signup failed: {res.message} (status: {res.status})"

    def logout(self):
        if not self.token:
            return False, "Not logged in."
        res = self._call_with_retry("Logout", pb.LogoutRequest(token=self.token))
        if res.status == "OK":
            self.token = None
            self.user = None
            return True, "Logout successful."
        else:
            return False, f"Logout failed: {res.message}"

    def get_shows(self):
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        res = self._call_with_retry("Get", pb.GetRequest(token=self.token, type="SHOWS", params=json.dumps({}).encode()))
        items = [json.loads(it.data.decode()) for it in res.items]
        return res.status, items, res.message

    def get_seats(self, show_id):
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        res = self._call_with_retry(
            "Get",
            pb.GetRequest(token=self.token, type="SEATS", params=json.dumps({"show_id": show_id}).encode()),
        )
        items = [json.loads(it.data.decode()) for it in res.items]
        return res.status, items, res.message

    def get_cluster_status(self):
        if not self.token:
            return "AUTH_FAILED", {}, "Please log in first."
        res = self._call_with_retry("Get", pb.GetRequest(token=self.token, type="CLUSTER_STATUS"))
        if res.status != "OK":
            return res.status, {}, res.message
        return res.status, json.loads(res.message), ""

    def get_booking_analytics(self):
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        res = self._call_with_retry("Get", pb.GetRequest(token=self.token, type="BOOKING_ANALYTICS"))
        items = [json.loads(it.data.decode()) for it in res.items]
        return res.status, items, res.message

    def get_my_bookings(self):
        if not self.token:
            return "AUTH_FAILED", [], "Please log in first."
        res = self._call_with_retry("Get", pb.GetRequest(token=self.token, type="MY_BOOKINGS"))
        items = [json.loads(it.data.decode()) for it in res.items]
        return res.status, items, res.message

    def get_quote(self, show_id, seat_ids):
        if not self.token:
            return "AUTH_FAILED", {}, "Please log in first."
        if isinstance(seat_ids, (list, tuple)):
            seat_ids = ",".join(seat_ids)
        res = self._call_with_retry(
            "Get",
            pb.GetRequest(
                token=self.token,
                type="QUOTE",
                params=json.dumps({"show_id": show_id, "seat_id": seat_ids}).encode(),
            ),
        )
        items = [json.loads(it.data.decode()) for it in res.items]
        return res.status, (items[0] if items else {}), res.message

    def ask_faq_detailed(self, query, show_id=""):
        """Returns (status, answer_text, support) where support carries the correlation ID,
        FAQ sources, grounding context and whether the text is generated or a fallback."""
        if not self.token:
            return "AUTH_FAILED", "Please log in first.", {}
        res = self._call_with_retry(
            "Get",
            pb.GetRequest(
                token=self.token,
                type="FAQ",
                params=json.dumps({"query": query, "show_id": show_id}).encode(),
            ),
        )
        support = {}
        for it in res.items:
            if it.id == "faq_support":
                support = json.loads(it.data.decode())
        return res.status, res.message, support

    def book_seat(self, show_id, seat_id, card="4242424242424242", amount=None, req_id=None, hops=3):
        if not self.token:
            return "AUTH_FAILED", "", "Please log in first."
        body = {"show_id": show_id, "seat_id": seat_id, "card_number": card}
        if amount is not None:
            body["amount"] = str(amount)
        if req_id:
            rid = req_id
        else:
            rid = str(uuid4())
        n = 0
        while n < hops:
            try:
                res = self.stub.Post(
                    pb.PostRequest(token=self.token, type="BOOK_SEAT", data=json.dumps(body).encode(), request_id=rid),
                    timeout=self.timeout,
                )
            except grpc.RpcError as err:
                if err.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                    n += 1
                    if n >= hops:
                        return "UNAVAILABLE", "", "The booking service is unavailable."
                    time.sleep(self.backoff * n)
                    self._switch_server(self._next_seed())
                    continue
                raise
            if res.status != "NOT_LEADER" or not res.redirect_to:
                return res.status, res.booking_id, res.message
            self._switch_server(res.redirect_to)
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
            try:
                res = self.stub.Post(
                    pb.PostRequest(token=self.token, type="CANCEL_SEAT", data=json.dumps(body).encode(), request_id=rid),
                    timeout=self.timeout,
                )
            except grpc.RpcError as err:
                if err.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                    n += 1
                    if n >= hops:
                        return "UNAVAILABLE", "", "The booking service is unavailable."
                    time.sleep(self.backoff * n)
                    self._switch_server(self._next_seed())
                    continue
                raise
            if res.status != "NOT_LEADER" or not res.redirect_to:
                return res.status, res.booking_id, res.message
            self._switch_server(res.redirect_to)
            n = n + 1
        return "REDIRECT_FAILED", "", "Exceeded maximum redirect hops to reach cluster leader."

    def ask_faq(self, query, context="", show_id=""):
        if not self.token:
            return f"FAQ failed (AUTH_FAILED): Please log in first."
        res = self._call_with_retry(
            "Get",
            pb.GetRequest(
                token=self.token,
                type="FAQ",
                params=json.dumps({"query": query, "context": context, "show_id": show_id}).encode(),
            ),
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
    parser.add_argument(
        "--action",
        choices=["signup", "shows", "seats", "book", "cancel", "faq", "status", "analytics"],
        default="shows",
    )
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
    elif args.action == "status":
        print(c.get_cluster_status())
    elif args.action == "analytics":
        print(c.get_booking_analytics())
    else:
        print(c.ask_faq(args.query, show_id=args.show_id))
    c.close()


if __name__ == "__main__":
    main()
