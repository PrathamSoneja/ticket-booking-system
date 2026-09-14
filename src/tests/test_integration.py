"""M1 End-to-End Integration Test Suite (TASK-M1-TEST-1).

Tests concurrent clients, auth, browsing, booking concurrency, mock payments,
cancellation, and LLM FAQ queries over gRPC.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_GENERATED_DIR = Path(__file__).resolve().parents[1] / "ticket_booking" / "generated"
if str(_GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(_GENERATED_DIR))

import grpc
import pytest
import ticket_booking_pb2_grpc
from concurrent import futures
from ticket_booking.application import TicketApplication
from ticket_booking.auth import AuthService
from ticket_booking.client import TicketClient
from ticket_booking.llm_service import LLMService
from ticket_booking.payment import PaymentGateway
from ticket_booking.server import ClientServer
from ticket_booking.state_machine import BookingStateMachine


@pytest.fixture(scope="module")
def running_services():
    auth = AuthService({f"user_{i}": f"pass_{i}" for i in range(20)})
    state = BookingStateMachine(seats_per_show=10)
    payment = PaymentGateway(default_ticket_price=60.0)
    app = TicketApplication(auth=auth, state=state, payment=payment)

    llm_server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
    ticket_booking_pb2_grpc.add_LLMServiceServicer_to_server(LLMService(), llm_server)
    llm_port = llm_server.add_insecure_port("127.0.0.1:0")
    llm_server.start()

    app_server = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    app_servicer = ClientServer(
        app=app,
        payment_gateway=payment,
        is_leader=True,
        llm_address=f"127.0.0.1:{llm_port}",
    )
    ticket_booking_pb2_grpc.add_ClientServiceServicer_to_server(app_servicer, app_server)
    app_port = app_server.add_insecure_port("127.0.0.1:0")
    app_server.start()

    yield {
        "app_addr": f"127.0.0.1:{app_port}",
        "llm_addr": f"127.0.0.1:{llm_port}",
        "app": app,
        "state": state,
        "payment": payment,
    }

    app_servicer.close()
    app_server.stop(grace=None)
    llm_server.stop(grace=None)


def test_concurrent_booking_over_grpc(running_services) -> None:
    """10 concurrent clients attempt to book the exact same seat simultaneously over gRPC."""
    app_addr = running_services["app_addr"]
    llm_addr = running_services["llm_addr"]
    payment: PaymentGateway = running_services["payment"]
    charged_before = payment.successful_charge_count()

    clients: list[TicketClient] = []
    for i in range(10):
        c = TicketClient(server_address=app_addr, llm_address=llm_addr)
        ok, _ = c.login(f"user_{i}", f"pass_{i}")
        assert ok is True
        clients.append(c)

    def do_book(idx: int) -> tuple[str, str]:
        c = clients[idx]
        status, b_id, _ = c.book_seat("show-1", "A1")
        return status, b_id

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(do_book, range(10)))

    statuses = [r[0] for r in results]
    booking_ids = [r[1] for r in results if r[1]]

    assert statuses.count("OK") == 1
    assert statuses.count("ALREADY_BOOKED") == 9
    assert len(booking_ids) == 1
    assert payment.successful_charge_count() - charged_before == 1

    status, seats, _ = clients[0].get_seats("show-1")
    assert status == "OK"
    seat_a1 = next(s for s in seats if s["seat_id"] == "A1")
    assert seat_a1["status"] == "BOOKED"

    for c in clients:
        c.close()


def test_payment_failure_prevents_booking(running_services) -> None:
    """A booking attempt with an invalid card fails and does NOT book the seat."""
    app_addr = running_services["app_addr"]
    client = TicketClient(server_address=app_addr)
    client.login("user_10", "pass_10")

    status, b_id, msg = client.book_seat("show-1", "A3", card_number="0000000000000000")
    assert status == "PAYMENT_FAILED"
    assert b_id == ""

    _, seats, _ = client.get_seats("show-1")
    seat_a3 = next(s for s in seats if s["seat_id"] == "A3")
    assert seat_a3["status"] == "AVAILABLE"

    client.close()


def test_e2e_booking_cancellation_and_faq(running_services) -> None:
    """Full lifecycle: browse -> book -> FAQ check -> cancel -> verify available."""
    app_addr = running_services["app_addr"]
    payment: PaymentGateway = running_services["payment"]
    client = TicketClient(server_address=app_addr)
    client.login("user_11", "pass_11")

    status, shows, _ = client.get_shows()
    assert status == "OK"
    assert len(shows) >= 2

    status, b_id, _ = client.book_seat("show-1", "A5")
    assert status == "OK"

    faq_ans = client.ask_faq("How do I cancel my seat booking?")
    assert "cancel" in faq_ans.lower()
    assert "2 hours" in faq_ans.lower()

    seat_faq = client.ask_faq("What seats are available for the show?", show_id="show-1")
    assert "a5" in seat_faq.lower() or "available" in seat_faq.lower()

    refunds_before = len(payment._refunds_by_tx)
    c_status, _, _ = client.cancel_seat(b_id)
    assert c_status == "OK"
    assert len(payment._refunds_by_tx) == refunds_before + 1

    _, seats, _ = client.get_seats("show-1")
    seat_a5 = next(s for s in seats if s["seat_id"] == "A5")
    assert seat_a5["status"] == "AVAILABLE"

    ok, _ = client.logout()
    assert ok is True

    b_status, _, _ = client.book_seat("show-1", "A5")
    assert b_status == "AUTH_FAILED"

    faq_after_logout = client.ask_faq("How do I cancel a booking?")
    assert "log in" in faq_after_logout.lower()

    client.close()
