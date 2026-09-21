

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
from ticket_booking.application import TicketApplication
from ticket_booking.auth import AuthService
from ticket_booking.client import TicketClient
from ticket_booking.llm_service import LLMService
from ticket_booking.payment import PaymentGateway
from ticket_booking.server import ClientServer
from ticket_booking.state_machine import BookingStateMachine


@pytest.fixture(scope="module")
def services():
    auth = AuthService({f"user_{i}": f"pass_{i}" for i in range(20)})
    payment = PaymentGateway(60.0)
    app = TicketApplication(auth, BookingStateMachine(seats_per_show=10), payment)
    llm = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
    pb_grpc.add_LLMServiceServicer_to_server(LLMService(ask=lambda prompt: prompt), llm)
    llm_port = llm.add_insecure_port("127.0.0.1:0")
    llm.start()
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    svc = ClientServer(app, payment, llm_address=f"127.0.0.1:{llm_port}")
    pb_grpc.add_ClientServiceServicer_to_server(svc, srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    srv.start()
    yield f"127.0.0.1:{port}", payment
    svc.close()
    srv.stop(grace=None)
    llm.stop(grace=None)


def test_concurrent_booking_over_grpc(services) -> None:
    address, payment = services
    before = payment.successful_charge_count()
    clients = [TicketClient(address) for _ in range(10)]
    for i, client in enumerate(clients):
        assert client.login(f"user_{i}", f"pass_{i}")[0]
    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(lambda client: client.book_seat("show-1", "A1")[0], clients))
    assert statuses.count("OK") == 1
    assert statuses.count("ALREADY_BOOKED") == 9
    assert payment.successful_charge_count() - before == 1
    for client in clients:
        client.close()


def test_payment_failure_prevents_booking(services) -> None:
    address, _ = services
    client = TicketClient(address)
    client.login("user_10", "pass_10")
    assert client.book_seat("show-1", "A3", card_number="0000000000000000")[0] == "PAYMENT_FAILED"
    assert next(seat for seat in client.get_seats("show-1")[1] if seat["seat_id"] == "A3")["status"] == "AVAILABLE"
    client.close()


def test_booking_cancellation_and_faq(services) -> None:
    address, payment = services
    client = TicketClient(address)
    client.login("user_11", "pass_11")
    status, booking_id, _ = client.book_seat("show-1", "A5")
    assert status == "OK"
    assert "2 hours" in client.ask_faq("How do I cancel my seat booking?")
    before = len(payment._refunds_by_tx)
    assert client.cancel_seat(booking_id)[0] == "OK"
    assert len(payment._refunds_by_tx) == before + 1
    assert next(seat for seat in client.get_seats("show-1")[1] if seat["seat_id"] == "A5")["status"] == "AVAILABLE"
    assert client.logout()[0]
    assert client.book_seat("show-1", "A5")[0] == "AUTH_FAILED"
    client.close()
