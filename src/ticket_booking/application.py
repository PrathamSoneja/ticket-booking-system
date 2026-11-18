from dataclasses import replace
import json
import math
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from .auth import Auth
from .payment import Payments
from .state_machine import Outcome, SeatMap, parse_seats


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
        if kind == "MY_BOOKINGS":
            rows = self.seats.user_bookings(user)
            for row in rows:
                row.update(self.pay.txn_summary(row["txn"]) if row["txn"] else {"charges": 0, "amount": 0.0, "refunded": False})
            return "OK", rows, ""
        if kind == "QUOTE":
            show_id = params.get("show_id", "")
            if show_id not in self.seats.catalog:
                return "NOT_FOUND", [], "Show does not exist."
            picked = parse_seats(params.get("seat_id", ""))
            if not picked or len(picked) > 5:
                return "INVALID_REQUEST", [], "Select between 1 and 5 seats."
            return (
                "OK",
                [
                    {
                        "show_id": show_id,
                        "seat_ids": picked,
                        "unit_price": self.pay.price,
                        "total": self.pay.price * len(picked),
                    }
                ],
                "",
            )
        return "INVALID_REQUEST", [], "Unsupported get request type."

    def state_digest(self):
        h = sha256()
        for row in self.seats.digest_rows() + self.pay.digest_rows():
            h.update(repr(row).encode())
        return h.hexdigest()[:16]

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

    def _price(self, data, seats):
        due = self.pay.price * len(seats)
        raw = data.get("amount", "")
        if raw == "":
            return None, due
        try:
            offered = float(raw)
        except (TypeError, ValueError):
            offered = math.nan
        if not math.isfinite(offered):
            return Outcome("INVALID_AMOUNT", "Payment amount is not a valid finite number."), None
        if offered < due:
            return Outcome("INVALID_AMOUNT", "Payment amount is below the required booking total."), None
        return None, due

    def post(self, tok, kind, data, req_id):
        user = self.auth.token_user(tok)
        if user is None:
            return Outcome("AUTH_FAILED", "A valid session token is required.")
        if kind == "BOOK_SEAT":
            seats = parse_seats(data.get("seat_id", ""))
            err, due = self._price(data, seats)
            if err:
                return err
            card = data.get("card_number", "4242424242424242")
            show_id = data.get("show_id", "")
            joined = ",".join(seats)

            def pay():
                r = self.pay.charge(user, due, card, req_id)
                return r.status, r.message, r.txn

            return self.seats.book(
                user, show_id, joined, req_id, pay, kind=kind, payload={"show_id": show_id, "seat_id": joined}
            )
        if kind == "CANCEL_SEAT":
            bid = data.get("booking_id", "")
            out = self.seats.cancel(user, bid, req_id, kind=kind, payload={"booking_id": bid})
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

        if kind == "BOOK_SEAT":
            seats = parse_seats(data.get("seat_id", ""))
            payload = {"show_id": data.get("show_id", ""), "seat_id": ",".join(seats)}
        elif kind == "CANCEL_SEAT":
            payload = {"booking_id": data.get("booking_id", "")}
        else:
            return Outcome("INVALID_REQUEST", "Unsupported post request type.")

        already = self.seats.result(req_id, user, kind, payload)

        if already:
            idx = raft.req_index.get(req_id)
            row = raft.store.entry_at(idx) if idx is not None else None

            if row and idx <= raft.commit_index and idx <= raft.last_applied:
                return replace(
                    already,
                    raft_leader=raft.node_id,
                    raft_term=row[1],
                    raft_log_index=idx,
                    raft_commit_index=raft.commit_index,
                    raft_last_applied=raft.last_applied,
                    raft_committed=True,
                    request_replayed=True,
                )

            return already

        body = {"user_id": user, **payload}

        if kind == "BOOK_SEAT":
            err, due = self._price(data, seats)
            if err:
                return err

            check = self.pay.check(due, data.get("card_number", "4242424242424242"))
            body.update(
                amount=due,
                txn=f"tx_{uuid5(NAMESPACE_URL, 'ticket-booking/' + req_id).hex[:12]}",
                pay_status=check.status,
                pay_message=check.message,
            )

        status, out, proof = raft.propose(
            kind,
            json.dumps(body, separators=(",", ":")).encode(),
            req_id,
            return_metadata=True,
        )

        if status != "OK":
            return Outcome(status, "The request could not be committed by a Raft majority.")

        return replace(
            out,
            raft_leader=proof["leader"],
            raft_term=proof["term"],
            raft_log_index=proof["log_index"],
            raft_commit_index=proof["commit_index"],
            raft_last_applied=proof["last_applied"],
            raft_committed=proof["committed"],
        )
    def apply_raft(self, kind, payload, req_id):
        if kind == "NOOP":
            return Outcome("OK", "No-op committed for read barrier.")
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
