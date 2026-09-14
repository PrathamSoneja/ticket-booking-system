"""Protocol-independent application facade for ClientService handlers."""

from __future__ import annotations

from .auth import AuthService, LoginResult
from .payment import PaymentGateway
from .state_machine import BookingStateMachine, Outcome


class TicketApplication:
    def __init__(
        self,
        auth: AuthService | None = None,
        state: BookingStateMachine | None = None,
        payment: PaymentGateway | None = None,
    ):
        self.auth = auth or AuthService()
        self.state = state or BookingStateMachine()
        self.payment = payment or PaymentGateway()

    def login(self, username: str, password: str) -> LoginResult:
        return self.auth.login(username, password)

    def logout(self, token: str) -> Outcome:
        if self.auth.logout(token):
            return Outcome("OK", "Logged out.")
        return Outcome("AUTH_FAILED", "Invalid or expired session.")

    def get(self, token: str, query_type: str, params: dict[str, str] | None = None) -> tuple[str, list[dict[str, str]], str]:
        if self.auth.user_for_token(token) is None:
            return "AUTH_FAILED", [], "A valid session token is required."
        params = params or {}
        if query_type == "SHOWS":
            return "OK", self.state.shows(), ""
        if query_type == "SEATS":
            seats = self.state.seats(params.get("show_id", ""))
            if seats is None:
                return "NOT_FOUND", [], "Show does not exist."
            return "OK", seats, ""
        if query_type == "FAQ":
            return "OK", [], ""
        return "INVALID_REQUEST", [], "Unsupported get request type."

    def faq_context(self, query: str, params: dict[str, str] | None = None) -> str:
        """Build extra context for the LLM node (live seat maps when the query is about availability)."""
        params = params or {}
        caller_context = params.get("context", "").strip()
        show_id = params.get("show_id", "").strip()
        parts: list[str] = []
        if caller_context:
            parts.append(caller_context)
        availability_query = any(
            word in query.lower() for word in ("seat", "available", "availability", "empty")
        )
        if show_id and availability_query:
            seats = self.state.seats(show_id)
            if seats is not None:
                booked = sum(1 for seat in seats if seat["status"] == "BOOKED")
                available = [seat["seat_id"] for seat in seats if seat["status"] == "AVAILABLE"]
                parts.append(
                    f"Live committed seat map for {show_id}: "
                    f"{len(available)} available, {booked} booked. "
                    f"AVAILABLE seats: {', '.join(available) if available else 'none'}."
                )
        return "\n\n".join(parts)

    def post(self, token: str, command_type: str, data: dict[str, str], request_id: str) -> Outcome:
        user_id = self.auth.user_for_token(token)
        if user_id is None:
            return Outcome("AUTH_FAILED", "A valid session token is required.")
        return self.process_business_request(user_id, command_type, data, request_id)

    def process_business_request(
        self,
        user_id: str,
        command_type: str,
        data: dict[str, str],
        request_id: str,
    ) -> Outcome:
        """Leader-side booking/cancellation apply path (FR-BOOK-1)."""
        if command_type == "BOOK_SEAT":
            card = data.get("card_number", "4242424242424242")
            amount_raw = data.get("amount")
            try:
                amount = float(amount_raw) if amount_raw not in (None, "") else None
            except (TypeError, ValueError):
                return Outcome("INVALID_AMOUNT", "Payment amount is not a valid number.")

            def charge() -> tuple[str, str, str]:
                result = self.payment.process_payment(
                    user_id=user_id,
                    amount=amount,
                    card_number=card,
                    idempotency_key=request_id,
                )
                return result.status, result.message, result.transaction_id

            return self.state.book(
                user_id,
                data.get("show_id", ""),
                data.get("seat_id", ""),
                request_id,
                charge=charge,
            )
        if command_type == "CANCEL_SEAT":
            outcome = self.state.cancel(user_id, data.get("booking_id", ""), request_id)
            if outcome.status == "OK" and outcome.payment_transaction_id:
                self.payment.refund_payment(outcome.payment_transaction_id)
            return outcome
        return Outcome("INVALID_REQUEST", "Unsupported post request type.")
