"""Unit and functional tests for TicketClient and ClientServer gRPC interaction (TASK-CLIENT-1)."""

from __future__ import annotations

import sys
from concurrent import futures
from pathlib import Path
from uuid import uuid4

_GENERATED_DIR = Path(__file__).resolve().parents[1] / "ticket_booking" / "generated"
if str(_GENERATED_DIR) not in sys.path:
    sys.path.insert(0, str(_GENERATED_DIR))

import grpc
import pytest
import ticket_booking_pb2_grpc
from ticket_booking.application import TicketApplication
from ticket_booking.client import TicketClient
from ticket_booking.server import ClientServer


@pytest.fixture(scope="module")
def app_cluster():
    """Spin up two servers: a leader on port_a and a follower on port_b that redirects to port_a."""
    app = TicketApplication()

    # Leader server
    server_leader = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    servicer_leader = ClientServer(app=app, is_leader=True)
    ticket_booking_pb2_grpc.add_ClientServiceServicer_to_server(servicer_leader, server_leader)
    port_leader = server_leader.add_insecure_port("127.0.0.1:0")
    server_leader.start()

    # Follower server (redirects to leader)
    server_follower = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    servicer_follower = ClientServer(
        app=app,
        is_leader=False,
        leader_address=f"127.0.0.1:{port_leader}",
    )
    ticket_booking_pb2_grpc.add_ClientServiceServicer_to_server(servicer_follower, server_follower)
    port_follower = server_follower.add_insecure_port("127.0.0.1:0")
    server_follower.start()

    yield {
        "leader_addr": f"127.0.0.1:{port_leader}",
        "follower_addr": f"127.0.0.1:{port_follower}",
        "app": app,
    }

    server_leader.stop(grace=None)
    server_follower.stop(grace=None)


def test_client_auth_and_browse(app_cluster) -> None:
    client = TicketClient(server_address=app_cluster["leader_addr"])

    # Unauthenticated browse should fail
    status, shows, msg = client.get_shows()
    assert status == "AUTH_FAILED"

    # Login
    ok, msg = client.login("alice", "wonderland")
    assert ok is True
    assert client.token is not None

    # Browse shows
    status, shows, _ = client.get_shows()
    assert status == "OK"
    assert len(shows) >= 2
    show_ids = [s["show_id"] for s in shows]
    assert "show-1" in show_ids

    # Browse seats
    status, seats, _ = client.get_seats("show-1")
    assert status == "OK"
    assert len(seats) == 10
    assert seats[0]["status"] == "AVAILABLE"

    client.close()


def test_client_booking_and_cancellation(app_cluster) -> None:
    client = TicketClient(server_address=app_cluster["leader_addr"])
    client.login("bob", "builder")

    # Book seat A2
    status, booking_id, msg = client.book_seat("show-1", "A2")
    assert status == "OK"
    assert booking_id != ""

    # Re-booking same seat fails with ALREADY_BOOKED
    status2, _, _ = client.book_seat("show-1", "A2")
    assert status2 == "ALREADY_BOOKED"

    # Cancel seat
    status_c, _, msg_c = client.cancel_seat(booking_id)
    assert status_c == "OK"

    # Now seat A2 is bookable again
    status3, new_b_id, _ = client.book_seat("show-1", "A2")
    assert status3 == "OK"
    assert new_b_id != ""

    client.close()


def test_client_leader_redirection(app_cluster) -> None:
    """Client initiates request to a follower node; client automatically follows redirect to leader."""
    client = TicketClient(server_address=app_cluster["follower_addr"])
    client.login("alice", "wonderland")

    # Client tries to book through the follower; client automatically redirects to leader and succeeds!
    status, booking_id, msg = client.book_seat("show-2", "A1")
    assert status == "OK"
    assert booking_id != ""
    assert client.server_address == app_cluster["leader_addr"]

    client.close()
