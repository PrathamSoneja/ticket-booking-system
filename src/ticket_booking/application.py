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
