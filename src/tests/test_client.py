import sys
from concurrent import futures
from pathlib import Path

import grpc
import pytest

GEN = Path(__file__).resolve().parents[1] / "ticket_booking" / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.application import BookingApp
from ticket_booking.client import Client
from ticket_booking.server import AppServer


@pytest.fixture(scope="module")
def cluster():
    app = BookingApp()
    leader = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb_grpc.add_ClientServiceServicer_to_server(AppServer(app=app), leader)
    leader_port = leader.add_insecure_port("127.0.0.1:0")
    leader.start()
    follower = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb_grpc.add_ClientServiceServicer_to_server(
        AppServer(app=app, is_leader=False, leader_url=f"127.0.0.1:{leader_port}"),
        follower,
    )
    follower_port = follower.add_insecure_port("127.0.0.1:0")
    follower.start()
    yield f"127.0.0.1:{leader_port}", f"127.0.0.1:{follower_port}"
    leader.stop(grace=None)
    follower.stop(grace=None)


def test_browse_shows(cluster) -> None:
    leader, _ = cluster
    client = Client(leader)
    assert client.get_shows()[0] == "AUTH_FAILED"
    assert client.login("alice", "wonderland")[0]
    assert client.get_shows()[0] == "OK"
    status, seats, _ = client.get_seats("show-1")
    assert status == "OK"
    assert len(seats) == 50
    client.close()


def test_book_cancel(cluster) -> None:
    leader, _ = cluster
    client = Client(leader)
    client.login("bob", "builder")
    status, booking_id, _ = client.book_seat("show-1", "A2")
    assert status == "OK"
    assert client.book_seat("show-1", "A2")[0] == "ALREADY_BOOKED"
    assert client.cancel_seat(booking_id)[0] == "OK"
    assert client.book_seat("show-1", "A2")[0] == "OK"
    client.close()


def test_leader_redirect(cluster) -> None:
    leader, follower = cluster
    client = Client(follower)
    client.login("alice", "wonderland")
    assert client.book_seat("show-2", "A1")[0] == "OK"
    assert client.addr == leader
    client.close()
