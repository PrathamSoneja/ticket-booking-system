from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from ticket_booking.application import BookingAppMain
from ticket_booking.payment import PaymentGateway


def _logged_in_app() -> tuple[BookingAppMain, str]:
    app = BookingAppMain()
    login = app.login("alice", "wonderland")
    return app, login.session_token


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
    app = BookingAppMain()
    assert app.get("missing", "SHOWS")[0] == "AUTH_FAILED"
    assert app.post("missing", "BOOK_SEAT", {}, str(uuid4())).status == "AUTH_FAILED"


def test_booking_charges_only_when_the_seat_is_taken() -> None:
    payment = PaymentGateway()
    app = BookingAppMain(pay_gateway=payment)
    alice = app.login("alice", "wonderland").session_token
    bob = app.login("bob", "builder").session_token
    first = app.post(alice, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A3"}, str(uuid4()))
    second = app.post(bob, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A3"}, str(uuid4()))
    assert first.status == "OK"
    assert second.status == "ALREADY_BOOKED"
    assert payment.successful_charge_count() == 1


def test_multi_seat_is_atomic() -> None:
    app, token = _logged_in_app()
    app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A1"}, str(uuid4()))
    blocked = app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A1,A2"}, str(uuid4()))
    assert blocked.status == "ALREADY_BOOKED"
    seats = {s["seat_id"]: s["status"] for s in app.get(token, "SEATS", {"show_id": "show-1"})[1]}
    assert seats["A1"] == "BOOKED"
    assert seats["A2"] == "AVAILABLE"
    ok = app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A2,A3,A4"}, str(uuid4()))
    assert ok.status == "OK"
    too_many = app.post(token, "BOOK_SEAT", {"show_id": "show-1", "seat_id": "A10,A11,A12,A13,A14,A15"}, str(uuid4()))
    assert too_many.status == "INVALID_REQUEST"
    cancelled = app.post(token, "CANCEL_SEAT", {"booking_id": ok.booking_id}, str(uuid4()))
    assert cancelled.status == "OK"
    seats = {s["seat_id"]: s["status"] for s in app.get(token, "SEATS", {"show_id": "show-1"})[1]}
    assert seats["A2"] == "AVAILABLE"
    assert seats["A3"] == "AVAILABLE"


def test_parallel_seats_and_shows() -> None:
    app, token = _logged_in_app()
    bob = app.login("bob", "builder").session_token

    def grab(pair):
        tok, show, seat = pair
        return app.post(tok, "BOOK_SEAT", {"show_id": show, "seat_id": seat}, str(uuid4())).status

    jobs = [
        (token, "show-1", "A20"),
        (bob, "show-1", "A21"),
        (token, "show-2", "A20"),
        (bob, "show-2", "A21"),
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(grab, jobs))
    assert statuses.count("OK") == 4


def test_mixed_multiseat_race() -> None:
    app = BookingAppMain()
    alice = app.login("alice", "wonderland").session_token
    bob = app.login("bob", "builder").session_token

    def grab(pair):
        tok, seats = pair
        return app.post(tok, "BOOK_SEAT", {"show_id": "show-3", "seat_id": seats}, str(uuid4()))

    with ThreadPoolExecutor(max_workers=4) as pool:
        outs = list(pool.map(grab, [(alice, "A1,A2"), (bob, "A2,A3")]))
    stats = []
    for o in outs:
        stats.append(o.status)
    assert stats.count("OK") == 1
    assert stats.count("ALREADY_BOOKED") == 1
    seats = {}
    for s in app.get(alice, "SEATS", {"show_id": "show-3"})[1]:
        seats[s["seat_id"]] = s["status"]
    taken = 0
    for k in ("A1", "A2", "A3"):
        if seats[k] == "BOOKED":
            taken = taken + 1
    assert taken == 2
    assert seats["A2"] == "BOOKED"
