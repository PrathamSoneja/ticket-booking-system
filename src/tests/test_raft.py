import socket
import sys
import time
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import pytest

GEN = Path(__file__).resolve().parents[1] / "ticket_booking" / "generated"
if str(GEN) not in sys.path:
    sys.path.insert(0, str(GEN))

import ticket_booking_pb2 as pb
import ticket_booking_pb2_grpc as pb_grpc
from ticket_booking.application import BookingApp
from ticket_booking.auth import Auth
from ticket_booking.client import Client
from ticket_booking.raft_node import RaftNode
from ticket_booking.raft_service import RaftService
from ticket_booking.server import AppServer
from ticket_booking.state_machine import Outcome


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class LocalNode:
    def __init__(self, node_id, addr, peers, path, start=True):
        self.app = BookingApp(auth=Auth({"alice": "wonderland"}))
        self.raft = RaftNode(node_id, peers, path, self.app.apply_raft)
        self.srv = grpc.server(futures.ThreadPoolExecutor(max_workers=12))
        pb_grpc.add_ClientServiceServicer_to_server(
            AppServer(app=self.app, raft=self.raft), self.srv
        )
        pb_grpc.add_RaftServiceServicer_to_server(RaftService(self.raft), self.srv)
        assert self.srv.add_insecure_port(addr) > 0
        self.srv.start()
        if start:
            self.raft.start()
        self.addr = addr
        self.path = path
        self.node_id = node_id
        self.peers = peers

    def stop(self):
        if getattr(self, "stopped", False):
            return
        self.stopped = True
        self.srv.stop(grace=None)
        self.raft.close()


@pytest.fixture
def cluster(tmp_path):
    ids = ["node-1", "node-2", "node-3"]
    addrs = {node: f"127.0.0.1:{free_port()}" for node in ids}
    nodes = {}
    for node in ids:
        peers = {other: addr for other, addr in addrs.items() if other != node}
        nodes[node] = LocalNode(
            node,
            addrs[node],
            peers,
            tmp_path / node / "raft.db",
            start=False,
        )
    for node in nodes.values():
        node.raft.start()
    yield nodes
    for node in list(nodes.values()):
        node.stop()


def wait_leader(nodes, alive=None, timeout=5.0):
    end = time.monotonic() + timeout
    allowed = set(alive if alive is not None else nodes)
    while time.monotonic() < end:
        leaders = []
        for node_id in allowed:
            node = nodes[node_id]
            if node.raft.role == "leader":
                leaders.append(node_id)
        if len(leaders) == 1:
            return leaders[0]
        time.sleep(0.03)
    return None


def test_vote_and_append_rules(tmp_path):
    applied = []

    def apply(cmd, payload, req_id):
        applied.append((cmd, payload.decode(), req_id))
        return Outcome("OK", "applied")

    node = RaftNode(
        "n1",
        {"n2": "127.0.0.1:1", "n3": "127.0.0.1:2"},
        tmp_path / "raft.db",
        apply,
    )
    stale = pb.RequestVoteArgs(**{"from": "n2", "term": 1, "last_log_index": 0, "last_log_term": 0})
    assert node.request_vote(stale).vote_granted
    other = pb.RequestVoteArgs(**{"from": "n3", "term": 1, "last_log_index": 0, "last_log_term": 0})
    assert not node.request_vote(other).vote_granted
    stale_node = RaftNode("n4", {"n2": "127.0.0.1:1"}, tmp_path / "stale.db", apply)
    stale_node.store.add_entry(1, 2, "TEST", b"latest", "log-1")
    stale_log = pb.RequestVoteArgs(
        **{"from": "n2", "term": 3, "last_log_index": 99, "last_log_term": 1}
    )
    assert not stale_node.request_vote(stale_log).vote_granted
    stale_node.close()

    first = pb.AppendEntriesArgs(
        **{
            "from": "n2",
            "term": 2,
            "prev_index": 0,
            "prev_term": 0,
            "commit_index": 1,
            "entries": [pb.LogEntry(index=1, term=2, command_type="TEST", payload=b"old", request_id="r1")],
        }
    )
    assert node.append_entries(first).entry_appended
    assert applied == [("TEST", "old", "r1")]
    conflict = pb.AppendEntriesArgs(
        **{
            "from": "n3",
            "term": 3,
            "prev_index": 0,
            "prev_term": 0,
            "commit_index": 1,
            "entries": [pb.LogEntry(index=1, term=3, command_type="TEST", payload=b"new", request_id="r2")],
        }
    )
    assert not node.append_entries(conflict).entry_appended
    assert node.store.term_at(1) == 2
    extra = pb.AppendEntriesArgs(
        **{
            "from": "n2",
            "term": 3,
            "prev_index": 1,
            "prev_term": 2,
            "commit_index": 1,
            "entries": [pb.LogEntry(index=2, term=3, command_type="TEST", payload=b"old", request_id="r3")],
        }
    )
    assert node.append_entries(extra).entry_appended
    replace = pb.AppendEntriesArgs(
        **{
            "from": "n3",
            "term": 4,
            "prev_index": 1,
            "prev_term": 2,
            "commit_index": 1,
            "entries": [pb.LogEntry(index=2, term=4, command_type="TEST", payload=b"new", request_id="r4")],
        }
    )
    assert node.append_entries(replace).entry_appended
    assert node.store.term_at(2) == 4
    node.close()


def test_raft_storage_replays_committed_entries(tmp_path):
    db = tmp_path / "node" / "raft.db"
    node = RaftNode("n1", {}, db, lambda cmd, data, rid: Outcome("OK", data.decode()))
    node.term = 1
    node.voted_for = "n1"
    node.store.set_state(1, "n1")
    node.role = "leader"
    status, out = node.propose("TEST", b"saved", "req-1")
    assert (status, out.message) == ("OK", "saved")
    assert node.commit_index == 1
    node.close()

    seen = []
    restarted = RaftNode(
        "n1",
        {},
        db,
        lambda cmd, data, rid: seen.append((cmd, data, rid)) or Outcome("OK", "replayed"),
    )
    assert restarted.term == 1
    assert restarted.voted_for == "n1"
    assert restarted.commit_index == 1
    assert seen == [("TEST", b"saved", "req-1")]
    restarted.close()


def test_cluster_election_replication_failure_and_catchup(cluster):
    leader = wait_leader(cluster)
    assert leader is not None
    followers = [node for node in cluster if node != leader]
    dead = followers[0]
    survivor = followers[1]

    client = Client(cluster[dead].addr)
    assert client.login("alice", "wonderland")[0]
    assert client.get_shows()[0] == "OK"
    status, booking_id, _ = client.book_seat("show-1", "A1")
    assert status == "OK"
    assert booking_id
    assert client.addr == cluster[leader].addr
    status, info, _ = client.get_cluster_status()
    assert status == "OK"
    assert info["role"] == "leader"
    assert info["log_length"] == 2
    end = time.monotonic() + 2
    while time.monotonic() < end:
        if all(node.raft.commit_index == 2 for node in cluster.values()):
            break
        time.sleep(0.03)
    for node in cluster.values():
        assert node.app.seats.grid["show-1"]["A1"].status == "BOOKED", node.raft.status()
        assert node.raft.commit_index == 2
        assert node.app.pay.charge_count() == 1
    assert client.book_seat("show-1", "A4", card="0000000000000000")[0] == "PAYMENT_FAILED"
    for node in cluster.values():
        assert node.app.seats.grid["show-1"]["A4"].status == "AVAILABLE"
        assert node.app.pay.charge_count() == 1

    signup = Client(cluster[dead].addr)
    assert signup.signup("grace", "GoodPass1")[0]
    for node in cluster.values():
        assert node.app.auth.check_credentials("grace", "GoodPass1")
    tok = signup.token
    assert signup.logout()[0]
    assert signup.stub.Get(pb.GetRequest(token=tok, type="SHOWS")).status == "AUTH_FAILED"
    signup.close()

    cluster[dead].stop()
    status, _, _ = client.book_seat("show-1", "A2")
    assert status == "OK"
    crowd = [Client(cluster[leader].addr) for _ in range(8)]
    for member in crowd:
        assert member.login("alice", "wonderland")[0]
    with ThreadPoolExecutor(max_workers=len(crowd)) as pool:
        statuses = list(pool.map(lambda member: member.book_seat("show-1", "A3")[0], crowd))
    assert statuses.count("OK") == 1
    assert statuses.count("ALREADY_BOOKED") == len(crowd) - 1
    for member in crowd:
        member.close()
    dead_addr = cluster[dead].addr
    dead_path = cluster[dead].path
    peers = {other: cluster[other].addr for other in cluster if other != dead}
    cluster[dead] = LocalNode(dead, dead_addr, peers, dead_path)
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if cluster[dead].raft.commit_index == cluster[leader].raft.commit_index:
            break
        time.sleep(0.05)
    assert cluster[dead].raft.commit_index == cluster[leader].raft.commit_index

    cluster[leader].stop()
    next_leader = wait_leader(cluster, alive=[dead, survivor], timeout=5.0)
    assert next_leader in (dead, survivor)
    start = time.monotonic()
    retry = Client(cluster[survivor].addr)
    assert retry.login("alice", "wonderland")[0]
    status, _, _ = retry.book_seat("show-2", "A1")
    assert status == "OK"
    assert time.monotonic() - start < 5.0

    old_addr = cluster[leader].addr
    old_path = cluster[leader].path
    peer_map = {other: cluster[other].addr for other in cluster if other != leader}
    cluster[leader] = LocalNode(leader, old_addr, peer_map, old_path)
    restarted = cluster[leader]

    end = time.monotonic() + 5
    while time.monotonic() < end:
        status = restarted.raft.status()
        if status["commit_index"] == cluster[survivor].raft.commit_index:
            break
        time.sleep(0.05)
    assert restarted.raft.commit_index == cluster[next_leader].raft.commit_index
    assert restarted.app.seats.grid["show-1"]["A1"].status == "BOOKED"
    assert restarted.app.seats.grid["show-1"]["A2"].status == "BOOKED"
    assert restarted.app.seats.grid["show-2"]["A1"].status == "BOOKED"
    assert restarted.app.auth.token_user(client.token) == "alice"
    assert restarted.raft.store.entries_to(restarted.raft.commit_index) == cluster[
        next_leader
    ].raft.store.entries_to(
        cluster[next_leader].raft.commit_index
    )
    client.close()
    retry.close()


def test_quorum_prevents_success_without_majority(cluster):
    leader = wait_leader(cluster)
    assert leader is not None
    followers = [node for node in cluster if node != leader]
    cluster[followers[0]].stop()
    cluster[followers[1]].stop()
    client = Client(cluster[leader].addr)
    assert client.login("alice", "wonderland")[0] is False
    cluster[leader].stop()
    client.close()
