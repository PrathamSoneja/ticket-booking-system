import sys
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import pytest

GEN = Path(__file__).resolve().parents[1] / "ticket_booking" / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.application import BookingApp
from ticket_booking.auth import Auth
from ticket_booking.client import Client
from ticket_booking.llm_service import LLMService
from ticket_booking.payment import Payments
from ticket_booking.server import AppServer
from ticket_booking.state_machine import SeatMap


@pytest.fixture(scope="module")
def services():
    auth = Auth({f"user_{i}": f"pass_{i}" for i in range(20)})
    payment = Payments(60.0)
    app = BookingApp(auth, SeatMap(capacity=10), payment)
    llm = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
    pb_grpc.add_LLMServiceServicer_to_server(LLMService(), llm)
    llm_port = llm.add_insecure_port("127.0.0.1:0")
    llm.start()
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    svc = AppServer(app, payment, llm_url=f"127.0.0.1:{llm_port}")
    pb_grpc.add_ClientServiceServicer_to_server(svc, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    yield f"127.0.0.1:{port}", payment
    svc.close()
    srv.stop(grace=None)
    llm.stop(grace=None)


def test_grpc_race(services) -> None:
    address, payment = services
    before = payment.charge_count()
    clients = [Client(address) for _ in range(10)]
    for i, client in enumerate(clients):
        assert client.login(f"user_{i}", f"pass_{i}")[0]
    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(lambda client: client.book_seat("show-1", "A1")[0], clients))
    assert statuses.count("OK") == 1
    assert statuses.count("ALREADY_BOOKED") == 9
    assert payment.charge_count() - before == 1
    for client in clients:
        client.close()


def test_declined_book(services) -> None:
    address, _ = services
    client = Client(address)
    client.login("user_10", "pass_10")
    assert client.book_seat("show-1", "A3", card="0000000000000000")[0] == "PAYMENT_FAILED"
    assert next(seat for seat in client.get_seats("show-1")[1] if seat["seat_id"] == "A3")["status"] == "AVAILABLE"
    client.close()


def test_cancel_faq(services) -> None:
    address, payment = services
    client = Client(address)
    client.login("user_11", "pass_11")
    status, booking_id, _ = client.book_seat("show-1", "A5")
    assert status == "OK"
    faq = client.ask_faq("How do I cancel my seat booking?")
    assert not faq.startswith("FAQ failed")
    assert faq.strip() != ""
    before = len(payment.refunds)
    assert client.cancel_seat(booking_id)[0] == "OK"
    assert len(payment.refunds) == before + 1
    assert next(seat for seat in client.get_seats("show-1")[1] if seat["seat_id"] == "A5")["status"] == "AVAILABLE"
    assert client.logout()[0]
    assert client.book_seat("show-1", "A5")[0] == "AUTH_FAILED"
    client.close()


def test_houseful_show(services) -> None:
    address, _ = services
    client = Client(address)
    client.login("user_12", "pass_12")
    n = 1
    while n <= 10:
        assert client.book_seat("show-2", "A" + str(n))[0] == "OK"
        n = n + 1
    seats = client.get_seats("show-2")[1]
    for s in seats:
        assert s["status"] == "BOOKED"
    assert client.book_seat("show-2", "A1")[0] == "ALREADY_BOOKED"
    client.close()


def test_connection_overload(services) -> None:
    address, _ = services
    crowd = []
    i = 0
    while i < 40:
        crowd.append(Client(address))
        i = i + 1

    def ping(idx):
        cl = crowd[idx]
        n = str(idx % 20)
        cl.login("user_" + n, "pass_" + n)
        return cl.get_shows()[0]

    with ThreadPoolExecutor(max_workers=40) as pool:
        got = list(pool.map(ping, range(40)))
    assert got.count("OK") == 40
    for cl in crowd:
        cl.close()


def test_worker_limit() -> None:
    auth = Auth({f"user_{i}": f"pass_{i}" for i in range(16)})
    app = BookingApp(auth, SeatMap(capacity=10))
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    svc = AppServer(app)
    pb_grpc.add_ClientServiceServicer_to_server(svc, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    addr = f"127.0.0.1:{port}"
    crowd = []
    i = 0
    while i < 16:
        crowd.append(Client(addr))
        i = i + 1

    def ping(idx):
        cl = crowd[idx]
        cl.login("user_" + str(idx), "pass_" + str(idx))
        return cl.get_seats("show-1")[0]

    with ThreadPoolExecutor(max_workers=16) as pool:
        got = list(pool.map(ping, range(16)))
    assert got.count("OK") == 16
    for cl in crowd:
        cl.close()
    svc.close()
    srv.stop(grace=None)
