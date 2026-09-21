from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from ticket_booking.application import TicketApplication
from ticket_booking.payment import PaymentGateway


def _logged_in_app() -> tuple[TicketApplication, str]:
    app = TicketApplication()
    login = app.login("alice", "wonderland")
    return app, login.token


def test_only_one_concurrent_booking_succeeds() -> None:
    app, token = _logged_in_app()

    def attempt(_: int) -> str:
        return app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A1"}, str(uuid4())).status

    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(attempt, range(16)))
    assert statuses.count("OK") == 1
    assert statuses.count("ALREADY_BOOKED") == 15


def test_booking_is_idempotent_and_cancellation_releases_the_seat() -> None:
    app, token = _logged_in_app()
    request_id = str(uuid4())
    first = app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A1"}, request_id)
    replay = app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A1"}, request_id)
    assert first.status == replay.status == "OK"
    assert first.booking_id == replay.booking_id

    cancelled = app.post(token, "CANCEL_SEAT", {"booking_id": first.booking_id}, str(uuid4()))
    assert cancelled.status == "OK"
    assert app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A1"}, str(uuid4())).status == "OK"


def test_post_and_get_require_a_valid_token() -> None:
    app = TicketApplication()
    assert app.get("missing", "SHOWS")[0] == "AUTH_FAILED"
    assert app.post("missing", "BOOK_SEAT", {}, str(uuid4())).status == "AUTH_FAILED"


def test_booking_charges_only_when_the_seat_is_taken() -> None:
    payment = PaymentGateway()
    app = TicketApplication(payment=payment)
    alice = app.login("alice", "wonderland").token
    bob = app.login("bob", "builder").token
    first = app.post(alice, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A3"}, str(uuid4()))
    second = app.post(bob, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A3"}, str(uuid4()))
    assert first.status == "OK"
    assert second.status == "ALREADY_BOOKED"
    assert payment.successful_charge_count() == 1
