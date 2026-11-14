# Raft design and correctness notes

## Raft safety rules implemented

The project’s Raft implementation enforces a narrow subset of the core Raft safety properties that matter for the Milestone 2 ticket-booking workload:

- Single leader per term: a node only becomes leader after receiving a majority of `RequestVote` grants for the same term.
- Term monotonicity: any higher-term vote or append request forces the receiving node to step down and adopt the newer term.
- Stale leader rejection: `AppendEntries` requests with an older term or a mismatched log prefix are rejected.
- Prefix matching: followers accept append requests only when the leader’s `prev_index` and `prev_term` match the follower’s log prefix.
- Majority-only commit: leader commit advancement requires a majority of replicas to match the candidate index before the leader advances its commit pointer.
- Durable persisted state: term, vote, and commit index are stored in SQLite so recovered nodes replay the committed log and resume safely.
- Idempotent replication: request IDs recorded in the log are used as the deduplication key for repeated RPCs and retried writes.

## Gaps identified by the Milestone 2 audit

The audit (`docs/milestone2-audit.md`) listed these as unproven or untested. Items 3-9 are now covered by tests (see "Milestone 2 changes" below); items 1-2 were not reproduced as defects - the existing election and term checks already held under the new fault-injection and churn tests.

1. Election churn could leave more than one node in a leader-like state if startup timing, term updates, and heartbeat interactions were not carefully managed.
2. Stale leaders could still attempt replication with an older term or a log prefix that did not match the follower state.
3. Duplicate or overlapping writes under leader changes were not explicitly proven to be rejected when quorum was not available.
4. Follower rejoin and crash recovery were not validated under active write traffic and leader handoff.
5. Read-after-write consistency across leader/follower reads after failover was not proven.
6. Concurrent client booking traffic during election churn had not been stress-tested for double booking or lost committed writes.
7. Cluster-wide log equality after a write burst and leader change was not enforced by the test suite.
8. Persistent recovery of `currentTerm`, `votedFor`, and log replay under restart had not been exercised end-to-end.
9. Delayed and dropped RPC conditions were not included in the validation matrix.

## Fixes made

The final implementation keeps the existing system architecture intact while hardening the Raft core and verification coverage:

- Added focused regression tests that exercise repeated startup/failover, stale-term rejection, quorum loss, follower rejoin, and cluster-level commit equality.
- Kept the leader election logic aligned with standard Raft semantics: each election increments term, records the node’s vote, and only elevates to leader after majority support.
- Preserved strict follower policy around stale or conflicting append requests.
- Kept commit advancement gated by majority replication and safe log prefix rules.
- Retained persisted Raft state in SQLite so crash recovery replays committed entries and lets a restarted follower reconcile with the current leader.
- Avoided any change to the existing CLI or non-Raft application behavior outside the targeted correctness and validation surfaces.

## Tests added

The Raft-focused regression coverage includes tests for:

- single-leader election stability under repeated startup and timeout races
- stale-leader rejection for stale `AppendEntries` requests
- no-quorum write rejection
- leader failover with continued write traffic
- follower rejoin and catch-up after downtime
- committed-log equality across replicas after a write burst
- leader/follower consistency checks during cluster churn
- persisted state replay across crashes
- RPC delay/fault handling in the Raft path

## Milestone 2 changes (verified)

Defects found by new failing tests, then fixed (test failed before the fix, passes after):

1. **Stale reads from a deposed leader** - `test_partitioned_old_leader_does_not_serve_stale_seat_map`.
   A leader cut off from the majority kept `role == "leader"` and answered `GET SEATS` from its
   local state: after the new majority leader committed a booking for A9, the old leader still
   returned `OK` with A9 `AVAILABLE`. Followers likewise answered reads from local, possibly
   lagging state.
   Fix: `RaftNode.read_barrier()` (ReadIndex, Raft paper section 8). The leader (a) commits a
   `NOOP` entry in its current term if its commit index is not from that term, (b) records the
   read index, (c) runs a heartbeat round started after (b) and requires same-term
   acknowledgements from a majority (`_replicate_peer` now returns whether the peer accepted
   this term), (d) waits until `last_applied >= read_index`. `AppServer.Get` runs the barrier on
   the leader for every type except the node-local `CLUSTER_STATUS`; followers forward reads to
   the leader (one hop, guarded by an `x-tb-forwarded` metadata header). Without a majority the
   read returns `NO_QUORUM`. `apply_raft` treats `NOOP` as a no-op.
2. **Follower commit index not bounded by the last new entry** -
   `test_follower_commit_is_bounded_by_last_new_entry`. `append_entries` set
   `commit_index = min(leaderCommit, last log index)`, so a follower holding a stale,
   unverified suffix could commit it. Fix: `min(leaderCommit, prev_index + len(entries))` (Raft
   Fig. 2, AppendEntries rule 5), never moving the commit index backwards. With this
   implementation's leader (which always sends everything from `next_index`) the case needs a
   crafted RPC to trigger, so it is a protocol-conformance fix rather than an observed outage.

Small supporting changes: `Post` now fills `redirect_to` when leadership changes mid-proposal;
the client retries `NO_LEADER` with backoff and seed rotation and can translate advertised
leader addresses (`redirect_map`); `status()` additionally reports `last_log_term` and the
leader's `match_index`, and `log_digest()` hashes the committed prefix.

New tests (`src/tests/test_raft_m2.py`, helper `src/tests/raft_faults.py`):

- read barrier on a single node; follower reads forwarded and linearizable;
- read-after-write: book, stop leader, elect new leader, same client reads seat `BOOKED`;
- no quorum: valid session, both followers stopped, booking not `OK`, seat not booked, zero
  charges; one follower restarted, retry with the same request ID gives exactly one booking,
  one charge and one `BOOK_SEAT` request ID in the committed log;
- real gRPC fault injection: a client interceptor on every node's outgoing Raft channel delays,
  drops requests, drops responses (after delivery), duplicates, and re-delivers older requests
  after newer ones for both `AppendEntries` and `RequestVote`, under booking traffic with a
  leader crash and restart. Asserts each fault type fired, at most one leader per term
  (recorded at `_become_leader`), no overbooking, every acknowledged booking present on every
  node, one charge per booking, identical committed logs;
- booking during election churn: 8 clients, leader killed and later restarted mid-traffic;
  same assertions plus at least one booking committed after the leader change.

All eight were run five times consecutively without failure (about 30 s per run). The churn test's post-failover check was later tightened to switch to a fresh seat range after the leader change, because on a fast run all original seats could be booked before the kill.

## Remaining limitations

- No log compaction/snapshots; `propose()` scans the whole log for request-ID dedup (O(n)).
- No cluster membership changes; peers are static.
- No leader lease or check-quorum: an isolated leader keeps its role until it sees a higher
  term. It cannot commit writes or pass the read barrier, so this affects availability
  messages only, not safety.
- Every linearizable read costs one heartbeat round to the followers.
- Fault injection is client-side (interceptor) on real sockets in one process; there is no
  OS-level network partitioning. The Docker run covers real process/container kills.
- Election timing (0.45-0.8 s, 100 ms heartbeat) is tuned for a LAN/localhost demo.
