from .auth import AuthService, LoginResult
from .payment import PaymentGateway
from .state_machine import BookingStateMachine, Outcome


class TicketApplication:
    def __init__(self, auth: AuthService | None = None, state: BookingStateMachine | None = None, payment: PaymentGateway | None = None):
        self.auth = auth or AuthService()
        self.state = state or BookingStateMachine()
        self.payment = payment or PaymentGateway()

    def login(self, username: str, password: str) -> LoginResult:
        return self.auth.login(username, password)

    def logout(self, token: str) -> Outcome:
        if self.auth.logout(token):
            return Outcome("OK", "Logged out.")
        return Outcome("AUTH_FAILED", "Invalid or expired session.")

    def get(self, token: str, kind: str, params: dict[str, str] | None = None) -> tuple[str, list[dict[str, str]], str]:
        if self.auth.user_for_token(token) is None:
            return "AUTH_FAILED", [], "A valid session token is required."
        params = params or {}
        if kind == "SHOWS":
            return "OK", self.state.shows(), ""
        if kind == "SEATS":
            seats = self.state.seats(params.get("show_id", ""))
            return ("NOT_FOUND", [], "Show does not exist.") if seats is None else ("OK", seats, "")
        if kind == "FAQ":
            return "OK", [], ""
        return "INVALID_REQUEST", [], "Unsupported get request type."

    def faq_context(self, query: str, params: dict[str, str] | None = None) -> str:
        params = params or {}
        parts = [params["context"].strip()] if params.get("context", "").strip() else []
        show_id = params.get("show_id", "").strip()
        words = ("seat", "available", "availability", "empty")
        if show_id and any(word in query.lower() for word in words):
            seats = self.state.seats(show_id)
            if seats is not None:
                free = [seat["seat_id"] for seat in seats if seat["status"] == "AVAILABLE"]
                used = len(seats) - len(free)
                parts.append(f"Live committed seat map for {show_id}: {len(free)} available, {used} booked. AVAILABLE seats: {', '.join(free) if free else 'none'}.")
        return "\n\n".join(parts)

    def post(self, token: str, kind: str, data: dict[str, str], request_id: str) -> Outcome:
        user = self.auth.user_for_token(token)
        if user is None:
            return Outcome("AUTH_FAILED", "A valid session token is required.")
        return self.process_business_request(user, kind, data, request_id)

    def process_business_request(self, user: str, kind: str, data: dict[str, str], request_id: str) -> Outcome:
        if kind == "BOOK_SEAT":
            try:
                amount = float(data["amount"]) if data.get("amount", "") else None
            except (TypeError, ValueError):
                return Outcome("INVALID_AMOUNT", "Payment amount is not a valid number.")

            def charge() -> tuple[str, str, str]:
                result = self.payment.process_payment(user, amount, data.get("card_number", "4242424242424242"), request_id)
                return result.status, result.message, result.transaction_id

            return self.state.book(user, data.get("show_id", ""), data.get("seat_id", ""), request_id, charge)
        if kind == "CANCEL_SEAT":
            out = self.state.cancel(user, data.get("booking_id", ""), request_id)
            if out.status == "OK" and out.payment_transaction_id:
                self.payment.refund_payment(out.payment_transaction_id)
            return out
        return Outcome("INVALID_REQUEST", "Unsupported post request type.")
