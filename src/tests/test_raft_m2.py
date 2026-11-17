import random
import time
from threading import Event, Lock, Thread
from uuid import uuid4

import pytest

from tests.raft_faults import (
    FaultInjector,
    block_inbound,
    committed_logs,
    install_injectors,
    record_leaders,
    wait_converged,
)
from tests.test_raft import LocalNode, free_port, wait_leader
from ticket_booking.client import Client
from ticket_booking.generated import ticket_booking_pb2 as pb
from ticket_booking.raft_node import RaftNode
from ticket_booking.state_machine import Outcome

RETRYABLE = {"NOT_LEADER", "UNAVAILABLE", "NO_QUORUM", "NO_LEADER", "REDIRECT_FAILED"}


class Cluster:
    """3 LocalNodes with optional per-node fault injectors and leader-per-term recording."""

    def __init__(self, tmp_path, injector=None):
        self.ids = ["node-1", "node-2", "node-3"]
        self.addrs = {n: f"127.0.0.1:{free_port()}" for n in self.ids}
        self.tmp = tmp_path
        self.injector = injector
        self.injectors = {}
        self.leaders = []
        self.leaders_lock = Lock()
        self.nodes = {}
        for n in self.ids:
            self.nodes[n] = self._make(n, start=False)
        for node in self.nodes.values():
            node.raft.start()

    def _make(self, n, start=True):
        peers = {o: a for o, a in self.addrs.items() if o != n}
        node = LocalNode(n, self.addrs[n], peers, self.tmp / n / "raft.db", start=False)
        record_leaders(node.raft, self.leaders, self.leaders_lock)
        if self.injector is not None:
            self.injectors[n] = install_injectors(node.raft, lambda peer: self.injector(n, peer))
        if start:
            node.raft.start()
        return node

    def stop(self, n):
        self.nodes[n].stop()

    def restart(self, n):
        self.nodes[n] = self._make(n)
        return self.nodes[n]

    def alive(self):
        return {n: node for n, node in self.nodes.items() if not getattr(node, "stopped", False)}

    def leader(self, timeout=6.0):
        return wait_leader(self.alive(), timeout=timeout)

    def close(self):
        for node in self.nodes.values():
            node.stop()

    def one_leader_per_term(self):
        with self.leaders_lock:
            seen = {}
            for term, nid in self.leaders:
                seen.setdefault(term, set()).add(nid)
        return {t: s for t, s in seen.items() if len(s) > 1}


@pytest.fixture
def make_cluster(tmp_path):
    made = []

    def build(injector=None):
        c = Cluster(tmp_path, injector)
        made.append(c)
        return c

    yield build
    for c in made:
        c.close()


def seat_status(node, show, seat):
    return node.app.seats.grid[show][seat].status


def assert_cluster_invariants(nodes, ok_results):
    """No overbooking, every acknowledged booking present everywhere, one charge per booking,
    identical committed logs."""
    logs = committed_logs(nodes)
    assert len({tuple(v) for v in logs.values()}) == 1, "committed logs diverged"
    for nid, node in nodes.items():
        seats = node.app.seats
        holders = {}
        for b in seats.bookings.values():
            if b.status != "CONFIRMED":
                continue
            for s in b.seat_id.split(","):
                holders.setdefault((b.show_id, s), []).append(b.booking_id)
        over = {k: v for k, v in holders.items() if len(v) > 1}
        assert not over, f"{nid} overbooked: {over}"
        for (show, s), bids in holders.items():
            assert seats.grid[show][s].booking == bids[0]
        for _rid, show, s, bid in ok_results:
            assert bid in seats.bookings, f"{nid} lost acknowledged booking {bid}"
            assert seats.grid[show][s].booking == bid, f"{nid} seat {s} not held by {bid}"
        confirmed_or_cancelled = sum(1 for b in seats.bookings.values() if b.txn)
        assert node.app.pay.charge_count() == confirmed_or_cancelled, f"{nid} charge count mismatch"


# ---------------------------------------------------------------- unit-level protocol rules


def test_follower_commit_is_bounded_by_last_new_entry(tmp_path):
    """Raft Fig. 2, AppendEntries rule 5: commitIndex = min(leaderCommit, index of last NEW entry).
    A follower must not commit a stale suffix entry that the leader did not vouch for."""
    applied = []
    node = RaftNode(
        "n1",
        {"n2": "127.0.0.1:1"},
        tmp_path / "f.db",
        lambda c, p, r: applied.append(r) or Outcome("OK", ""),
    )
    node.store.add_entry(1, 1, "TEST", b"a", "r1")
    node.store.add_entry(2, 1, "TEST", b"stale-suffix", "r2-stale")
    hb = pb.AppendEntriesArgs(
        **{"from": "n2", "term": 2, "prev_index": 1, "prev_term": 1, "commit_index": 2, "entries": []}
    )
    assert node.append_entries(hb).entry_appended
    assert node.commit_index == 1
    assert applied == ["r1"]
    node.close()


def test_single_node_read_barrier_commits_current_term_entry(tmp_path):
    node = RaftNode("n1", {}, tmp_path / "s.db", lambda c, p, r: Outcome("OK", c))
    node.term = 3
    node.role = "leader"
    node.leader_id = "n1"
    assert node.read_barrier(timeout=1.0) == "OK"
    assert node.store.term_at(node.commit_index) == 3
    node.role = "follower"
    assert node.read_barrier(timeout=0.2) == "NOT_LEADER"
    node.close()


# ---------------------------------------------------------------- authoritative reads


def test_partitioned_old_leader_does_not_serve_stale_seat_map(make_cluster):
    """A leader cut off from the majority still believes it is leader. It must not answer a
    read with state older than a booking already committed by the new majority leader."""
    c = make_cluster(injector=lambda n, peer: FaultInjector(seed=hash((n, peer)) & 0xFFFF))
    old = c.leader()
    assert old
    client = Client(list(c.addrs.values()))
    assert client.login("alice", "wonderland")[0]
    token = client.token
    assert wait_converged(c.nodes)

    for inj in c.injectors[old].values():
        inj.isolated = True
    block_inbound(c.nodes[old].raft)
    others = {n: c.nodes[n] for n in c.ids if n != old}
    new = wait_leader(others, timeout=6.0)
    assert new and new != old
    assert c.nodes[old].raft.role == "leader"  # self-declared, stale

    writer = Client(c.addrs[new])
    writer.token = token
    status, bid, msg = writer.book_seat("show-1", "A9")
    assert status == "OK", (status, msg)

    reader = Client(c.addrs[old], timeout=4.0, max_retries=0)
    reader.token = token
    status, seats, msg = reader.get_seats("show-1")
    a9 = next((s["status"] for s in seats if s["seat_id"] == "A9"), None)
    assert status != "OK" or a9 == "BOOKED", f"stale read from deposed leader: {status} A9={a9}"
    assert status in {"NO_QUORUM", "NO_LEADER", "NOT_LEADER"}, (status, msg)
    for x in (client, writer, reader):
        x.close()


def test_follower_reads_are_forwarded_and_linearizable(make_cluster):
    c = make_cluster()
    leader = c.leader()
    follower = next(n for n in c.ids if n != leader)
    client = Client(c.addrs[follower])
    assert client.login("alice", "wonderland")[0]
    status, bid, _ = client.book_seat("show-1", "A11")
    assert status == "OK"
    reader = Client(c.addrs[follower])
    reader.token = client.token
    status, seats, _ = reader.get_seats("show-1")
    assert status == "OK"
    assert {s["seat_id"]: s["status"] for s in seats}["A11"] == "BOOKED"
    client.close()
    reader.close()


def test_read_after_write_survives_leader_failover(make_cluster):
    c = make_cluster()
    leader = c.leader()
    assert leader
    client = Client([c.addrs[leader]] + [c.addrs[n] for n in c.ids if n != leader])
    assert client.login("alice", "wonderland")[0]
    status, bid, msg = client.book_seat("show-1", "A7")
    assert status == "OK", (status, msg)
    c.stop(leader)
    new = c.leader()
    assert new and new != leader
    end = time.monotonic() + 8
    status, seats = None, []
    while time.monotonic() < end:
        status, seats, _ = client.get_seats("show-1")
        if status == "OK":
            break
        time.sleep(0.2)
    assert status == "OK", status
    assert {s["seat_id"]: s["status"] for s in seats}["A7"] == "BOOKED"
    client.close()


# ---------------------------------------------------------------- no quorum


def test_no_quorum_booking_has_no_side_effects_and_retry_is_idempotent(make_cluster):
    c = make_cluster()
    leader = c.leader()
    assert leader
    client = Client(c.addrs[leader], timeout=1.5, max_retries=0)
    assert client.login("alice", "wonderland")[0]
    assert wait_converged(c.nodes)
    followers = [n for n in c.ids if n != leader]
    for f in followers:
        c.stop(f)

    rid = "no-quorum-" + uuid4().hex
    status, bid, msg = client.book_seat("show-2", "A5", req_id=rid, hops=1)
    assert status != "OK", (status, msg)
    lnode = c.nodes[leader]
    assert seat_status(lnode, "show-2", "A5") == "AVAILABLE"
    assert lnode.app.pay.charge_count() == 0
    assert not lnode.app.seats.bookings

    c.restart(followers[0])
    end = time.monotonic() + 8
    status = None
    while time.monotonic() < end:
        status, bid, msg = client.book_seat("show-2", "A5", req_id=rid, hops=1)
        if status not in RETRYABLE:
            break
        time.sleep(0.2)
    assert status == "OK", (status, msg)
    alive = c.alive()
    assert wait_converged(alive)
    for nid, node in alive.items():
        assert len(node.app.seats.bookings) == 1, nid
        assert node.app.pay.charge_count() == 1, nid
        assert seat_status(node, "show-2", "A5") == "BOOKED"
    log = lnode.raft.store.entries_to(lnode.raft.commit_index)
    applied_books = {r[4] for r in log if r[2] == "BOOK_SEAT"}
    assert applied_books == {rid}
    client.close()


# ---------------------------------------------------------------- traffic helpers


def booking_worker(addrs, token, seats, stop, results, lock, seed, show="show-3"):
    rng = random.Random(seed)
    client = Client(addrs, timeout=2.0, max_retries=2, backoff=0.05)
    client.token = token
    while not stop.is_set():
        seat = rng.choice(seats)
        rid = uuid4().hex
        deadline = time.monotonic() + 10
        status = None
        bid = ""
        while time.monotonic() < deadline:
            status, bid, _ = client.book_seat(show, seat, req_id=rid, hops=3)
            if status not in RETRYABLE:
                break
            time.sleep(0.05)
        with lock:
            results.append((rid, show, seat, status, bid))
    client.close()


def run_traffic(addrs, token, seats, workers=6, show="show-3"):
    stop = Event()
    results = []
    lock = Lock()
    threads = [
        Thread(target=booking_worker, args=(addrs, token, seats, stop, results, lock, i, show)) for i in range(workers)
    ]
    for t in threads:
        t.start()
    return stop, threads, results


def finish_traffic(stop, threads):
    stop.set()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)


# ---------------------------------------------------------------- fault injection


def test_fault_injected_raft_rpcs_keep_safety(make_cluster):
    """Delayed, dropped (request and response), duplicated and reordered real gRPC
    AppendEntries/RequestVote traffic, plus a leader crash, under booking load."""
    c = make_cluster(
        injector=lambda n, peer: FaultInjector(
            seed=sum(map(ord, n + peer)),
            delay=0.04,
            drop_req=0.08,
            drop_resp=0.08,
            dup=0.15,
            reorder=0.15,
        )
    )
    leader = c.leader(timeout=10)
    assert leader
    addrs = list(c.addrs.values())
    login = Client(addrs, timeout=3.0)
    end = time.monotonic() + 10
    while not login.login("alice", "wonderland")[0] and time.monotonic() < end:
        time.sleep(0.2)
    assert login.token
    seats = [f"A{i}" for i in range(1, 31)]
    stop, threads, results = run_traffic(addrs, login.token, seats, workers=5, show="show-4")
    time.sleep(2.0)
    first = c.leader(timeout=10)
    c.stop(first)
    time.sleep(2.0)
    c.restart(first)
    time.sleep(2.0)
    finish_traffic(stop, threads)

    for inj in c.injectors.values():
        for i in inj.values():
            i.drop_req = i.drop_resp = i.dup = i.reorder = 0.0
            i.delay = 0.0
    nudge = Client(addrs, timeout=3.0)
    nudge.token = login.token
    end = time.monotonic() + 10
    while nudge.get_seats("show-4")[0] != "OK" and time.monotonic() < end:
        time.sleep(0.2)
    assert wait_converged(c.nodes, timeout=10)

    stats = {}
    for inj in c.injectors.values():
        for i in inj.values():
            for k, v in i.stats.items():
                stats[k] = stats.get(k, 0) + v
    for kind in ("drop_req", "drop_resp", "dup", "reorder"):
        assert stats.get(kind, 0) > 0, f"fault {kind} never fired: {stats}"

    assert c.one_leader_per_term() == {}, c.leaders
    ok = [(r, sh, s, b) for r, sh, s, st, b in results if st == "OK"]
    assert ok, results
    per_seat = {}
    for _, _sh, s, b in ok:
        per_seat.setdefault(s, set()).add(b)
    assert all(len(v) == 1 for v in per_seat.values()), per_seat
    assert_cluster_invariants(c.nodes, ok)
    login.close()
    nudge.close()


# ---------------------------------------------------------------- election churn


def test_concurrent_booking_during_election_churn(make_cluster):
    c = make_cluster()
    leader = c.leader()
    assert leader
    addrs = list(c.addrs.values())
    login = Client(addrs)
    assert login.login("alice", "wonderland")[0]
    assert wait_converged(c.nodes)
    # workers read this shared list every iteration; after the leader change they switch to
    # seats nobody has touched yet, so post-failover commits are guaranteed to be attempted
    seats = [f"A{i}" for i in range(1, 26)]
    stop, threads, results = run_traffic(addrs, login.token, seats, workers=8, show="show-3")
    time.sleep(1.0)
    c.stop(leader)
    new = c.leader()
    assert new and new != leader
    seats[:] = [f"A{i}" for i in range(26, 51)]
    time.sleep(1.5)
    c.restart(leader)
    time.sleep(1.5)
    finish_traffic(stop, threads)

    assert wait_converged(c.nodes, timeout=10)
    assert c.one_leader_per_term() == {}, c.leaders
    finals = [st for *_, st, _ in results]
    assert all(st in {"OK", "ALREADY_BOOKED"} for st in finals), sorted(set(finals))
    ok = [(r, sh, s, b) for r, sh, s, st, b in results if st == "OK"]
    assert ok
    per_seat = {}
    for _, _sh, s, b in ok:
        per_seat.setdefault(s, set()).add(b)
    assert all(len(v) == 1 for v in per_seat.values()), per_seat
    assert len({b for *_, b in ok}) == len(ok)
    assert any(int(s[1:]) >= 26 for _, _, s, _ in ok), "no booking committed after the leader change"
    assert_cluster_invariants(c.nodes, ok)
    for node in c.nodes.values():
        assert node.raft.commit_index == c.nodes[new].raft.commit_index
    login.close()
