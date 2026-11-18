import contextlib
import random
import time
from collections import Counter
from threading import Lock

import grpc

from ticket_booking.generated import ticket_booking_pb2_grpc as pb_grpc


class InjectedUnavailable(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "fault injected"


class FaultInjector(grpc.UnaryUnaryClientInterceptor):
    """Wraps one node's outgoing Raft channel to one peer.

    delay     -- max seconds of random latency added before the request and after the response
    drop_req  -- probability the request is never delivered
    drop_resp -- probability the request is delivered but the response is lost
    dup       -- probability the request is delivered twice
    reorder   -- probability a previously held (older) request is re-delivered after a newer one
    """

    def __init__(self, seed, delay=0.0, drop_req=0.0, drop_resp=0.0, dup=0.0, reorder=0.0):
        self.rng = random.Random(seed)
        self.lock = Lock()
        self.delay = delay
        self.drop_req = drop_req
        self.drop_resp = drop_resp
        self.dup = dup
        self.reorder = reorder
        self.isolated = False
        self.held = {}
        self.stats = Counter()

    def _roll(self):
        with self.lock:
            return {
                "pre": self.rng.uniform(0, self.delay),
                "post": self.rng.uniform(0, self.delay),
                "drop_req": self.rng.random() < self.drop_req,
                "drop_resp": self.rng.random() < self.drop_resp,
                "dup": self.rng.random() < self.dup,
                "hold": self.rng.random() < self.reorder,
                "replay": self.rng.random() < self.reorder,
            }

    def intercept_unary_unary(self, continuation, details, request):
        if self.isolated:
            self.stats["isolated"] += 1
            raise InjectedUnavailable()
        r = self._roll()
        method = details.method
        if r["pre"]:
            time.sleep(r["pre"])
        if r["drop_req"]:
            self.stats["drop_req"] += 1
            raise InjectedUnavailable()
        if r["dup"]:
            self.stats["dup"] += 1
            with contextlib.suppress(grpc.RpcError):
                continuation(details, request).result()
        out = continuation(details, request)
        with self.lock:
            stale = self.held.pop(method, None) if r["replay"] else None
            if r["hold"]:
                self.held[method] = request
        if stale is not None:
            # deliver an older request after a newer one (receiver-side reordering)
            self.stats["reorder"] += 1
            with contextlib.suppress(grpc.RpcError):
                continuation(details, stale).result()
        if r["post"]:
            time.sleep(r["post"])
        if r["drop_resp"]:
            self.stats["drop_resp"] += 1
            with contextlib.suppress(grpc.RpcError):
                out.result()
            raise InjectedUnavailable()
        return out


def install_injectors(raft, factory):
    """Route every outgoing Raft RPC of ``raft`` through an interceptor built by ``factory(peer)``."""
    made = {}
    for peer, ch in raft.channels.items():
        inj = factory(peer)
        made[peer] = inj
        raft.stubs[peer] = pb_grpc.RaftServiceStub(grpc.intercept_channel(ch, inj))
    return made


def record_leaders(raft, sink, lock):
    """Record every (term, node_id) at which ``raft`` becomes leader."""
    original = raft._become_leader

    def wrapped(cur_term):
        with lock:
            sink.append((cur_term, raft.node_id))
        return original(cur_term)

    raft._become_leader = wrapped


def block_inbound(raft):
    """Make this node's RaftService reject every incoming Raft RPC (inbound half of a partition)."""

    def refuse(_req):
        raise RuntimeError("partitioned")

    raft.append_entries = refuse
    raft.request_vote = refuse


def committed_logs(nodes):
    return {nid: n.raft.store.entries_to(n.raft.commit_index) for nid, n in nodes.items()}


def wait_converged(nodes, timeout=8.0):
    """Wait until every node has the same commit index and has applied it."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = [n.raft.status() for n in nodes.values()]
        commits = {s["commit_index"] for s in st}
        if len(commits) == 1 and all(s["last_applied"] == s["commit_index"] for s in st):
            return True
        time.sleep(0.05)
    return False
