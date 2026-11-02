import json
from uuid import NAMESPACE_URL, uuid5

from .auth import Auth
from .payment import Payments
from .state_machine import SeatMap, Outcome


class BookingApp:
    def __init__(self, auth=None, seats=None, pay=None):
        if auth:
            self.auth = auth
        else:
            self.auth = Auth()
        if seats:
            self.seats = seats
        else:
            self.seats = SeatMap()
        if pay:
            self.pay = pay
        else:
            self.pay = Payments()

    def login(self, uname, pw):
        return self.auth.login(uname, pw)

    def signup(self, uname, pw):
        return self.auth.signup(uname, pw)

    def logout(self, tok):
        if self.auth.logout(tok) == True:
            return Outcome("OK", "Logged out.")
        else:
            return Outcome("AUTH_FAILED", "Invalid or expired session.")

    def get(self, tok, kind, params=None):
        user = self.auth.token_user(tok)
        if user is None:
            return "AUTH_FAILED", [], "A valid session token is required."
        if params == None:
            params = {}
        if kind == "SHOWS":
            return "OK", self.seats.shows(), ""
        if kind == "SEATS":
            lst = self.seats.seats(params.get("show_id", ""))
            if lst is None:
                return "NOT_FOUND", [], "Show does not exist."
            else:
                return "OK", lst, ""
        if kind == "BOOKING_ANALYTICS":
            return "OK", self.seats.analytics(), ""
        if kind == "FAQ":
            return "OK", [], ""
        return "INVALID_REQUEST", [], "Unsupported get request type."

    def faq_context(self, query, params=None):
        if params == None:
            params = {}
        bits = []
        extra = params.get("context", "")
        if extra.strip() != "":
            bits.append(extra.strip())
        show_id = params.get("show_id", "").strip()
        hit = False
        q = query.lower()
        for w in ("seat", "available", "availability", "empty"):
            if w in q:
                hit = True
        if show_id and hit:
            lst = self.seats.seats(show_id)
            if lst is not None:
                free = []
                for s in lst:
                    if s["status"] == "AVAILABLE":
                        free.append(s["seat_id"])
                booked = len(lst) - len(free)
                if free:
                    free_str = ", ".join(free)
                else:
                    free_str = "none"
                bits.append(
                    f"Live committed seat map for {show_id}: {len(free)} available, {booked} booked. AVAILABLE seats: {free_str}."
                )
        return "\n\n".join(bits)

    def post(self, tok, kind, data, req_id):
        user = self.auth.token_user(tok)
        if user is None:
            return Outcome("AUTH_FAILED", "A valid session token is required.")
        if kind == "BOOK_SEAT":
            amt_str = data.get("amount", "")
            money = None
            if amt_str != "":
                try:
                    money = float(amt_str)
                except (TypeError, ValueError):
                    return Outcome("INVALID_AMOUNT", "Payment amount is not a valid number.")
            card = data.get("card_number", "4242424242424242")
            show_id = data.get("show_id", "")
            seat_id = data.get("seat_id", "")
            picked = []
            for part in str(seat_id).replace(";", ",").split(","):
                bit = part.strip()
                if bit != "" and bit not in picked:
                    picked.append(bit)
            if money is None and picked:
                money = self.pay.price * len(picked)
            seat_id = ",".join(picked)

            def pay():
                r = self.pay.charge(user, money, card, req_id)
                return r.status, r.message, r.txn

            return self.seats.book(user, show_id, seat_id, req_id, pay)
        if kind == "CANCEL_SEAT":
            out = self.seats.cancel(user, data.get("booking_id", ""), req_id)
            if out.status == "OK" and out.txn:
                self.pay.refund(out.txn)
            return out
        return Outcome("INVALID_REQUEST", "Unsupported post request type.")

    def raft_post(self, tok, kind, data, req_id, raft):
        user = self.auth.token_user(tok)
        if user is None:
            return Outcome("AUTH_FAILED", "A valid session token is required.")
        if not req_id:
            return Outcome("INVALID_REQUEST", "A request_id is required for Raft writes.")
        already = self.seats.result(req_id)
        if already:
            return already
        if kind == "BOOK_SEAT":
            amt_str = data.get("amount", "")
            money = None
            if amt_str != "":
                try:
                    money = float(amt_str)
                except (TypeError, ValueError):
                    return Outcome("INVALID_AMOUNT", "Payment amount is not a valid number.")
            picked = []
            for part in str(data.get("seat_id", "")).replace(";", ",").split(","):
                bit = part.strip()
                if bit != "" and bit not in picked:
                    picked.append(bit)
            if money is None and picked:
                money = self.pay.price * len(picked)
            check = self.pay.check(money, data.get("card_number", "4242424242424242"))
            txn = f"tx_{uuid5(NAMESPACE_URL, 'ticket-booking/' + req_id).hex[:12]}"
            body = {
                "user_id": user,
                "show_id": data.get("show_id", ""),
                "seat_id": ",".join(picked),
                "amount": money,
                "txn": txn,
                "pay_status": check.status,
                "pay_message": check.message,
            }
        elif kind == "CANCEL_SEAT":
            body = {"user_id": user, "booking_id": data.get("booking_id", "")}
        else:
            return Outcome("INVALID_REQUEST", "Unsupported post request type.")
        status, out = raft.propose(kind, json.dumps(body, separators=(",", ":")).encode(), req_id)
        if status != "OK":
            return Outcome(status, "The request could not be committed by a Raft majority.")
        return out

    def apply_raft(self, kind, payload, req_id):
        body = json.loads(payload.decode())
        if kind == "AUTH_SESSION":
            self.auth.add_session_hash(body["token_hash"], body["user_id"], body["expiry"])
            return Outcome("OK", "Session replicated.")
        if kind == "AUTH_SIGNUP":
            digest = bytes.fromhex(body["password_hash"])
            if not self.auth.add_user_hash(body["user_id"], digest):
                return Outcome("USERNAME_TAKEN", "That username is already registered.")
            self.auth.add_session_hash(body["token_hash"], body["user_id"], body["expiry"])
            return Outcome("OK", "Account registered.")
        if kind == "AUTH_LOGOUT":
            self.auth.revoke_hash(body["token_hash"])
            return Outcome("OK", "Session revoked.")
        if kind == "BOOK_SEAT":
            def pay():
                if body["pay_status"] != "SUCCESS":
                    return body["pay_status"], body["pay_message"], ""
                self.pay.apply_charge(body["user_id"], body["amount"], body["txn"], req_id)
                return "SUCCESS", "Payment processed successfully.", body["txn"]

            out = self.seats.book(
                body["user_id"],
                body["show_id"],
                body["seat_id"],
                req_id,
                pay=pay,
            )
            return out
        if kind == "CANCEL_SEAT":
            out = self.seats.cancel(body["user_id"], body["booking_id"], req_id)
            if out.status == "OK" and out.txn:
                self.pay.refund(out.txn)
            return out
        return Outcome("INVALID_REQUEST", "Unsupported Raft command.")
