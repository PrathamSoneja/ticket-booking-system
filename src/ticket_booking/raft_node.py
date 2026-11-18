import hashlib
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Condition, Lock, RLock, Thread

import grpc

from .generated import ticket_booking_pb2 as pb
from .generated import ticket_booking_pb2_grpc as pb_grpc
from .raft_storage import RaftStorage

log = logging.getLogger("ticket_booking.raft")


class RaftNode:
    def __init__(
        self,
        node_id,
        peers,
        db_path,
        apply_fn,
        election_min=0.45,
        election_max=0.8,
        heartbeat=0.1,
        client_peers=None,
    ):
        if node_id in peers:
            raise ValueError("peers must not contain this node")
        if election_min <= 0 or election_max < election_min or heartbeat <= 0:
            raise ValueError("Raft timing values must be positive and ordered")
        self.node_id = node_id
        self.peers = dict(peers)
        self.client_peers = dict(client_peers) if client_peers is not None else self.peers
        if self.client_peers.keys() != self.peers.keys():
            raise ValueError("client_peers must contain the same node IDs as peers")
        self.total = len(self.peers) + 1
        self.majority = self.total // 2 + 1
        self.store = RaftStorage(db_path)
        self.apply_fn = apply_fn
        self.election_min = election_min
        self.election_max = election_max
        self.heartbeat = heartbeat
        self.term = self.store.term
        self.voted_for = self.store.voted_for
        self.role = "follower"
        self.leader_id = None
        self.commit_index = self.store.commit_index
        self.last_applied = 0
        self.next_index = {}
        self.match_index = {}
        self.results = {}
        self.cv = Condition(RLock())
        self.apply_lock = Lock()
        self.peer_locks = {peer: Lock() for peer in self.peers}
        self.channels = {}
        self.stubs = {}
        for peer, addr in self.peers.items():
            ch = grpc.insecure_channel(addr)
            self.channels[peer] = ch
            self.stubs[peer] = pb_grpc.RaftServiceStub(ch)
        self.pool = ThreadPoolExecutor(max_workers=max(1, len(self.peers)))
        self.stop_flag = False
        self.thread = None
        self.last_beat = 0.0
        self.deadline = self._new_deadline()
        self.req_index = {}
        for row in self.store.entries_from(1):
            self.req_index.setdefault(row[4], row[0])
        for row in self.store.entries_to(self.commit_index):
            idx, term, cmd, payload, req_id = row
            result = self.apply_fn(cmd, payload, req_id)
            if req_id:
                self.results[req_id] = result
            self.last_applied = idx

    def _append(self, idx, term, cmd, payload, req_id):
        self.store.add_entry(idx, term, cmd, payload, req_id)
        self.req_index.setdefault(req_id, idx)

    def _truncate(self, idx):
        for row in self.store.entries_from(idx):
            if self.req_index.get(row[4], 0) >= idx:
                del self.req_index[row[4]]
        self.store.truncate(idx)

    def _new_deadline(self):
        return time.monotonic() + random.uniform(self.election_min, self.election_max)

    @property
    def leader_address(self):
        if self.leader_id == self.node_id:
            return None
        return self.client_peers.get(self.leader_id)

    @property
    def leader_rpc_address(self):
        if self.leader_id == self.node_id:
            return None
        return self.peers.get(self.leader_id)

    def status(self):
        with self.cv:
            return {
                "node_id": self.node_id,
                "role": self.role,
                "term": self.term,
                "leader_id": self.leader_id or "",
                "log_length": self.store.last_index,
                "commit_index": self.commit_index,
                "last_applied": self.last_applied,
                "last_log_term": self.store.term_at(self.store.last_index) or 0,
                "match_index": dict(self.match_index) if self.role == "leader" else {},
            }

    def log_digest(self, upto=None):
        """Short hash of the committed log prefix [1..upto] (default: commit_index)."""
        with self.cv:
            upto = self.commit_index if upto is None else upto
        h = hashlib.sha256()
        for row in self.store.entries_to(upto):
            h.update(repr((row[0], row[1], row[2], bytes(row[3]), row[4])).encode())
        return h.hexdigest()[:16]

    def start(self):
        with self.cv:
            if self.thread is not None:
                return
            self.stop_flag = False
            self.thread = Thread(target=self._run, name=f"raft-{self.node_id}", daemon=True)
            self.thread.start()

    def close(self):
        with self.cv:
            self.stop_flag = True
            self.cv.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.pool.shutdown(wait=True, cancel_futures=True)
        for ch in self.channels.values():
            ch.close()
        self.store.close()

    def wait_for_leader(self, timeout=2.0):
        end = time.monotonic() + timeout
        with self.cv:
            while self.leader_id is None and time.monotonic() < end:
                self.cv.wait(min(0.05, end - time.monotonic()))
            return self.leader_id

    def _vote_reply(self, to, granted):
        return pb.RequestVoteReply(**{"from": self.node_id, "to": to, "term": self.term, "vote_granted": granted})

    def _append_reply(self, to, ok, match):
        return pb.AppendEntriesReply(
            **{"from": self.node_id, "to": to, "term": self.term, "entry_appended": ok, "match_index": max(0, match)}
        )

    def request_vote(self, req):
        from_id = getattr(req, "from")
        with self.cv:
            if from_id not in self.peers or req.term < self.term:
                return self._vote_reply(from_id, False)
            if req.term > self.term:
                self._set_term(req.term, None)
            last_idx = self.store.last_index
            last_term = self.store.term_at(last_idx) or 0
            fresh = (req.last_log_term, req.last_log_index) >= (last_term, last_idx)
            can_vote = self.voted_for is None or self.voted_for == from_id
            granted = can_vote and fresh
            if granted:
                self.voted_for = from_id
                self.store.set_state(self.term, self.voted_for)
                self.role = "follower"
                self.leader_id = None
                self.deadline = self._new_deadline()
                log.info("%s voted for %s in term %s", self.node_id, from_id, self.term)
                self.cv.notify_all()
            return self._vote_reply(from_id, granted)

    def append_entries(self, req):
        from_id = getattr(req, "from")
        with self.cv:
            if from_id not in self.peers or req.term < self.term:
                return self._append_reply(from_id, False, self.store.last_index)
            if req.term > self.term:
                self._set_term(req.term, None)
            self.role = "follower"
            self.leader_id = from_id
            self.deadline = self._new_deadline()
            self.cv.notify_all()
            prev_term = self.store.term_at(req.prev_index)
            if prev_term is None or prev_term != req.prev_term:
                return self._append_reply(from_id, False, min(req.prev_index - 1, self.store.last_index))
            expect = req.prev_index + 1
            for ent in req.entries:
                if ent.index != expect:
                    return self._append_reply(from_id, False, expect - 1)
                own_term = self.store.term_at(ent.index)
                if own_term is not None and own_term != ent.term:
                    if ent.index <= self.commit_index:
                        return self._append_reply(from_id, False, ent.index - 1)
                    self._truncate(ent.index)
                    own_term = None
                if own_term is None:
                    self._append(ent.index, ent.term, ent.command_type, ent.payload, ent.request_id)
                expect += 1
            last_new = req.prev_index + len(req.entries)
            if req.commit_index > self.commit_index:
                self.commit_index = max(self.commit_index, min(req.commit_index, last_new))
                self.store.set_commit(self.commit_index)
        self._apply_committed()
        return self._append_reply(from_id, True, last_new)

    def propose(self, command, payload, req_id, timeout=5.0, return_metadata=False):
        end = time.monotonic() + timeout

        with self.cv:
            if self.role != "leader":
                result = ("NOT_LEADER", None)
                return result + (None,) if return_metadata else result

            found = self.req_index.get(req_id)
            if found is None:
                idx = self.store.last_index + 1
                self._append(idx, self.term, command, payload, req_id)
                self.match_index[self.node_id] = idx
            else:
                idx = found
                row = self.store.entry_at(found)
                if found > self.commit_index and row[1] != self.term:
                    idx = self.store.last_index + 1
                    self._append(idx, self.term, command, payload, req_id)
                    self.match_index[self.node_id] = idx

            cur_term = self.term

        def response(status, outcome):
            if not return_metadata:
                return status, outcome

            with self.cv:
                committed = (
                    status == "OK" and req_id in self.results and self.commit_index >= idx and self.last_applied >= idx
                )
                metadata = {
                    "leader": self.node_id,
                    "term": cur_term,
                    "log_index": idx,
                    "commit_index": self.commit_index,
                    "last_applied": self.last_applied,
                    "committed": committed,
                }

            return status, outcome, metadata

        self._advance_commit()
        self._replicate_all()

        while time.monotonic() < end:
            with self.cv:
                if req_id in self.results and self.commit_index >= idx and self.last_applied >= idx:
                    return response("OK", self.results[req_id])

                if self.role != "leader" or self.term != cur_term:
                    return response("NOT_LEADER", None)

                self.cv.wait(min(0.05, end - time.monotonic()))

            self._replicate_all()

        with self.cv:
            if req_id in self.results and self.commit_index >= idx and self.last_applied >= idx:
                return response("OK", self.results[req_id])

            return response("NO_QUORUM", None)

    def read_barrier(self, timeout=2.0):
        end = time.monotonic() + timeout
        with self.cv:
            if self.role != "leader":
                return "NOT_LEADER"
            term = self.term
            need_noop = self.store.term_at(self.commit_index) != term
        if need_noop:
            status, _ = self.propose(
                "NOOP", b"{}", f"noop:{self.node_id}:{term}", timeout=max(0.05, end - time.monotonic())
            )
            if status != "OK":
                return status
        with self.cv:
            if self.role != "leader" or self.term != term:
                return "NOT_LEADER"
            read_index = self.commit_index
        while True:
            acks = 1
            if self.peers:
                jobs = [self.pool.submit(self._replicate_peer, peer) for peer in self.peers]
                for job in jobs:
                    try:
                        if job.result(timeout=max(0.05, end - time.monotonic())):
                            acks = acks + 1
                    except TimeoutError:
                        continue
            with self.cv:
                if self.role != "leader" or self.term != term:
                    return "NOT_LEADER"
                if acks >= self.majority:
                    while self.last_applied < read_index and time.monotonic() < end:
                        self.cv.wait(min(0.05, max(0.001, end - time.monotonic())))
                    return "OK" if self.last_applied >= read_index else "NO_QUORUM"
            if time.monotonic() >= end:
                return "NO_QUORUM"
            time.sleep(0.02)

    def _set_term(self, term, vote):
        self.term = term
        self.voted_for = vote
        self.role = "follower"
        self.leader_id = None
        self.store.set_state(self.term, self.voted_for)
        self.deadline = self._new_deadline()
        log.info("%s became follower in term %s", self.node_id, self.term)

    def _run(self):
        while True:
            with self.cv:
                if self.stop_flag:
                    return
                role = self.role
                now = time.monotonic()
                election_due = role != "leader" and now >= self.deadline
                heartbeat_due = role == "leader" and now - self.last_beat >= self.heartbeat
                if election_due:
                    self.deadline = self._new_deadline()
                elif not heartbeat_due:
                    wait = 0.05
                    if role != "leader":
                        wait = min(wait, max(0.001, self.deadline - now))
                    self.cv.wait(wait)
                    continue
            if election_due:
                self._elect()
            elif heartbeat_due:
                with self.cv:
                    self.last_beat = time.monotonic()
                self._replicate_all()

    def _elect(self):
        with self.cv:
            self.term = self.term + 1
            self.voted_for = self.node_id
            self.role = "candidate"
            self.leader_id = None
            self.store.set_state(self.term, self.voted_for)
            self.deadline = self._new_deadline()
            cur_term = self.term
            last_idx = self.store.last_index
            last_term = self.store.term_at(last_idx) or 0
            log.info("%s started election in term %s", self.node_id, cur_term)
            if self.majority == 1:
                self._become_leader(cur_term)
                return
        req = pb.RequestVoteArgs(
            **{
                "from": self.node_id,
                "term": cur_term,
                "last_log_index": last_idx,
                "last_log_term": last_term,
            }
        )
        jobs = {}
        for peer, stub in self.stubs.items():
            jobs[peer] = self.pool.submit(stub.RequestVote, req, timeout=0.3)
        votes = 1
        for job in jobs.values():
            try:
                res = job.result(timeout=0.4)
            except (grpc.RpcError, TimeoutError):
                continue
            with self.cv:
                if res.term > self.term:
                    self._set_term(res.term, None)
                    return
                if self.term != cur_term or self.role != "candidate":
                    return
                if res.vote_granted:
                    votes = votes + 1
                if votes >= self.majority:
                    self._become_leader(cur_term)
                    break

    def _become_leader(self, cur_term):
        self.role = "leader"
        self.leader_id = self.node_id
        last_idx = self.store.last_index
        self.next_index = {peer: last_idx + 1 for peer in self.peers}
        self.match_index = {peer: 0 for peer in self.peers}
        self.match_index[self.node_id] = last_idx
        self.last_beat = 0.0
        log.info("%s became leader in term %s", self.node_id, cur_term)
        self.cv.notify_all()

    def _replicate_all(self):
        with self.cv:
            if self.role != "leader":
                return
        self._send_peers()
        if self._advance_commit():
            self._send_peers()

    def _send_peers(self):
        jobs = {}
        for peer in self.peers:
            jobs[peer] = self.pool.submit(self._replicate_peer, peer)
        for job in jobs.values():
            try:
                job.result(timeout=0.6)
            except TimeoutError:
                continue

    def _replicate_peer(self, peer):
        with self.peer_locks[peer]:
            with self.cv:
                if self.role != "leader":
                    return False
                next_idx = self.next_index.get(peer, self.store.last_index + 1)
                next_idx = max(1, next_idx)
                prev_idx = next_idx - 1
                prev_term = self.store.term_at(prev_idx)
                if prev_term is None:
                    self.next_index[peer] = max(1, next_idx - 1)
                    return False
                rows = self.store.entries_from(next_idx)
                ents = []
                for idx, term, cmd, payload, req_id in rows:
                    ents.append(
                        pb.LogEntry(
                            index=idx,
                            term=term,
                            command_type=cmd,
                            payload=payload,
                            request_id=req_id,
                        )
                    )
                req = pb.AppendEntriesArgs(
                    **{
                        "from": self.node_id,
                        "to": peer,
                        "term": self.term,
                        "prev_index": prev_idx,
                        "prev_term": prev_term,
                        "commit_index": self.commit_index,
                        "entries": ents,
                    }
                )
                cur_term = self.term
            try:
                res = self.stubs[peer].AppendEntries(req, timeout=0.3)
            except grpc.RpcError:
                return False
            with self.cv:
                if res.term > self.term:
                    self._set_term(res.term, None)
                    return False
                if self.role != "leader" or self.term != cur_term:
                    return False
                if res.entry_appended:
                    self.match_index[peer] = max(self.match_index.get(peer, 0), res.match_index)
                    self.next_index[peer] = self.match_index[peer] + 1
                else:
                    self.next_index[peer] = max(1, min(self.next_index.get(peer, 1) - 1, res.match_index + 1))
                # a same-term reply (success or log mismatch) proves the peer accepts us as leader
                return res.term == cur_term

    def _advance_commit(self):
        with self.cv:
            if self.role != "leader":
                return False
            last_idx = self.store.last_index
            new_commit = self.commit_index
            idx = last_idx
            while idx > self.commit_index:
                if self.store.term_at(idx) != self.term:
                    idx = idx - 1
                    continue
                count = 1
                for peer in self.peers:
                    if self.match_index.get(peer, 0) >= idx:
                        count = count + 1
                if count >= self.majority:
                    new_commit = idx
                    break
                idx = idx - 1
            if new_commit == self.commit_index:
                return False
            self.commit_index = new_commit
            self.store.set_commit(new_commit)
            log.info("%s committed log index %s in term %s", self.node_id, new_commit, self.term)
            self.cv.notify_all()
        self._apply_committed()
        return True

    def _apply_committed(self):
        with self.apply_lock:
            while True:
                with self.cv:
                    if self.last_applied >= self.commit_index:
                        return
                    idx = self.last_applied + 1
                    row = self.store.entry_at(idx)
                    if row is None:
                        return
                _, _, cmd, payload, req_id = row
                result = self.apply_fn(cmd, payload, req_id)
                with self.cv:
                    if req_id:
                        self.results[req_id] = result
                    self.last_applied = idx
                    self.cv.notify_all()
