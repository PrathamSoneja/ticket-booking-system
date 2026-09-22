from .auth import UserLoginManager, LoginResultData
from .payment import PaymentGateway
from .state_machine import BookingStateMachine, Outcome


class BookingAppMain:
    def __init__(self, login_mgr=None, booking_state=None, pay_gateway=None):
        if login_mgr:
            self.login_mgr = login_mgr
        else:
            self.login_mgr = UserLoginManager()

        if booking_state:
            self.booking_state = booking_state
        else:
            self.booking_state = BookingStateMachine()

        if pay_gateway:
            self.pay_gateway = pay_gateway
        else:
            self.pay_gateway = PaymentGateway()

    def login(self, uname, pw):
        return self.login_mgr.login(uname, pw)

    def signup(self, uname, pw):
        return self.login_mgr.signup(uname, pw)

    def logout(self, tok):
        did_it_work = self.login_mgr.logout(tok)
        if did_it_work == True:
            return Outcome("OK", "Logged out.")
        else:
            return Outcome("AUTH_FAILED", "Invalid or expired session.")

    def get(self, tok, req_kind, extra_params=None):
        current_user = self.login_mgr.user_for_token(tok)
        if current_user is None:
            return "AUTH_FAILED", [], "A valid session token is required."

        if extra_params == None:
            extra_params = {}

        if req_kind == "SHOWS":
            all_shows = self.booking_state.shows()
            return "OK", all_shows, ""

        if req_kind == "SEATS":
            the_show_id = extra_params.get("show_id", "")
            seat_list = self.booking_state.seats(the_show_id)
            if seat_list is None:
                return "NOT_FOUND", [], "Show does not exist."
            else:
                return "OK", seat_list, ""

        if req_kind == "FAQ":
            return "OK", [], ""

        return "INVALID_REQUEST", [], "Unsupported get request type."

    def faq_context(self, user_query, extra_params=None):
        if extra_params == None:
            extra_params = {}

        ctx_pieces = []
        raw_ctx = extra_params.get("context", "")
        if raw_ctx.strip() != "":
            ctx_pieces.append(raw_ctx.strip())

        show_id_val = extra_params.get("show_id", "").strip()
        seat_related_words = ("seat", "available", "availability", "empty")
        query_lower = user_query.lower()

        got_a_match = False
        for w in seat_related_words:
            if w in query_lower:
                got_a_match = True

        if show_id_val and got_a_match:
            seat_list = self.booking_state.seats(show_id_val)
            if seat_list is not None:
                free_seats = []
                for s in seat_list:
                    if s["status"] == "AVAILABLE":
                        free_seats.append(s["seat_id"])
                num_booked = len(seat_list) - len(free_seats)
                if free_seats:
                    free_str = ", ".join(free_seats)
                else:
                    free_str = "none"
                ctx_pieces.append(
                    f"Live committed seat map for {show_id_val}: {len(free_seats)} available, {num_booked} booked. AVAILABLE seats: {free_str}."
                )

        return "\n\n".join(ctx_pieces)

    def post(self, tok, req_kind, form_data, req_id):
        current_user = self.login_mgr.user_for_token(tok)
        if current_user is None:
            return Outcome("AUTH_FAILED", "A valid session token is required.")

        if req_kind == "BOOK_SEAT":
            amt_str = form_data.get("amount", "")
            money_amount = None
            if amt_str != "":
                try:
                    money_amount = float(amt_str)
                except (TypeError, ValueError):
                    return Outcome("INVALID_AMOUNT", "Payment amount is not a valid number.")

            card_val = form_data.get("card_number", "4242424242424242")
            show_id_val = form_data.get("show_id", "")
            seat_id_val = form_data.get("seat_id", "")
            picked = []
            for part in str(seat_id_val).replace(";", ",").split(","):
                bit = part.strip()
                if bit != "" and bit not in picked:
                    picked.append(bit)
            if money_amount is None and picked:
                money_amount = self.pay_gateway.ticket_price_default * len(picked)
            seat_id_val = ",".join(picked)

            def pay_for_it():
                pay_res = self.pay_gateway.process_payment(current_user, money_amount, card_val, req_id)
                return pay_res.status_txt, pay_res.info_message, pay_res.txn_id

            return self.booking_state.book(current_user, show_id_val, seat_id_val, req_id, pay_for_it)

        if req_kind == "CANCEL_SEAT":
            booking_id_val = form_data.get("booking_id", "")
            cancel_result = self.booking_state.cancel(current_user, booking_id_val, req_id)
            if cancel_result.status == "OK" and cancel_result.payment_transaction_id:
                self.pay_gateway.refund_payment(cancel_result.payment_transaction_id)
            return cancel_result

        return Outcome("INVALID_REQUEST", "Unsupported post request type.")
