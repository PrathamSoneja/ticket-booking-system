# Milestone 2 Audit

Scope audited:
- Assignment PDF: `AOS 2026_27 Assignment.pdf`
- Repository state: current `main` at commit `4f49383`
- Verification: full pytest run on the repository

## 1) Requirement-to-code-to-test matrix

| Requirement area | Evidence in code | Evidence in tests | Status | Notes |
| --- | --- | --- | --- | --- |
| gRPC project foundation | `src/proto/ticket_booking.proto`, `src/ticket_booking/server.py`, `src/ticket_booking/client.py`, `src/ticket_booking/llm_service.py` | `src/tests/test_proto.py`, `src/tests/test_integration.py`, `src/tests/test_client.py` | Partial/yes | gRPC RPCs exist and are used. |
| Multi-node Raft service | `src/ticket_booking/raft_node.py`, `src/ticket_booking/raft_service.py`, `src/ticket_booking/raft_storage.py` | `src/tests/test_raft.py` | Partial | The code implements a Raft-like node and election/replication logic, but the suite does not prove full cluster-level safety under real failure patterns. |
| Leader election | `src/ticket_booking/raft_node.py` contains `request_vote`, `_elect`, `_become_leader` | `test_vote_and_append_rules`, `test_cluster_election_replication_failure_and_catchup` | Partial | A single-leader outcome is exercised in-process and in a small cluster, but there is no exhaustive election loop/timeout stress coverage. |
| Log replication | `src/ticket_booking/raft_node.py` contains `append_entries`, `_replicate_peer`, `_advance_commit` | `test_vote_and_append_rules`, `test_cluster_election_replication_failure_and_catchup` | Partial | Replication is exercised for basic cases, not for churn, partial log divergence, or large-scale conflict scenarios. |
| Majority commitment | `src/ticket_booking/raft_node.py` sets `majority` and gates writes with quorum logic | `test_cluster_election_replication_failure_and_catchup` | Partial | There is some quorum logic, but no explicit no-quorum write failure validation under a real partitioned 3-node cluster or timeout-driven election split. |
| Strong consistency / no double booking | `src/ticket_booking/state_machine.py` includes request-id deduplication and per-seat locks | `src/tests/test_booking.py`, `src/tests/test_integration.py` | Partial | Booking paths are protected, but the repository does not prove linearizability across leader failover and follower reads under concurrent writes. |
| Failure recovery / rejoin | `src/ticket_booking/raft_storage.py`, `RaftNode` replay logic | `test_raft_storage_replays_committed_entries` | Partial | Restart replay exists, but a careful crash/rejoin scenario with live traffic is missing. |
| Stress testing | `src/tests/test_integration.py` and `src/tests/test_booking.py` include threaded workloads | `test_grpc_race`, `test_connection_overload`, `test_worker_limit` | Partial | Stress tests exist, but they do not simulate Raft network churn, election races, or node failures under load. |
| Docker topology | `docker-compose.yml` exists and includes app + llm services | No direct Docker smoke test under `src/tests` | Partial | The file exists, but there is no automated test proving the compose topology starts correctly and recovers under failure. |
| UI / frontend | No browser UI or dashboard observed in repo | No UI test present | Missing | The assignment does not mandate a UI for Milestone 2, but the project has no user-facing interface beyond CLI and gRPC endpoints. |
| Documentation | `README.md`, `src/README.md`, `tasks.md` | No assignment-level verification document | Partial | Good user docs exist, but not a milestone-specific audit or explicit verification checklist against the assignment PDF. |
| LLM server separation | `src/ticket_booking/llm_service.py`, `docker-compose.yml` | `src/tests/test_llm.py` | Yes | A separate LLM gRPC service is present. |

## 2) Exact gaps for Milestone 2

### 2.1 Raft correctness
- The repository contains a Raft-style implementation in `src/ticket_booking/raft_node.py`, but there is no proof of full Raft safety under adversarial timing, log divergence, and election churn.
- The assignment explicitly requires leader election, log replication, majority commit, and strong consistency. The current code and tests cover a subset of these, not the full protocol under failure.
- The project does not provide a formal or repeatable audit proving the log is identical across all replicas after a sequence of writes and leader changes.

### 2.2 Leader election
- The test suite checks an election outcome in a simple cluster, but it does not stress repeated elections across random timeouts or repeated leader loss/recovery cycles.
- There is no test for split-vote resolution or repeated timeout-driven election races under load.
- There is no test that asserts exactly one leader remains after several election rounds under active traffic.

### 2.3 Log replication
- The code has append-entry logic, but missing coverage for:
  - conflicting entries from stale leaders
  - partial log divergence and recovery
  - large multi-entry replication
  - heartbeat-only synchronization without writes
  - follower lag under leader churn
- No explicit test validates that all three replicas hold the same committed log sequence after a write burst.

### 2.4 Majority commitment
- The system has quorum logic (`majority = total // 2 + 1`), but the tests do not include a deterministic no-quorum write rejection under an actual partition or node outage.
- There is no explicit assertion that writes are rejected when a leader cannot reach a majority before timeout.
- There is no benchmark or test showing the write path fails in bounded time under quorum loss.

### 2.5 Strong consistency
- The assignment requires strong consistency for critical operations. The current suite does not prove linearizable reads or leader/follower read consistency under failover.
- The system has follower reads and leader redirects, but read-after-write guarantees across a change of leader and a restarted follower are not tested.
- The repository does not prove that a follower never observes a partially applied or stale state after a leader commit.

### 2.6 Failure recovery
- Restart replay exists (`test_raft_storage_replays_committed_entries`), but there is no end-to-end persisted-state recovery test covering leader crash, restart, and recovery of committed writes.
- There is no test that verifies a follower catches up from its local log to the current leader after downtime and rejoin.
- There is no test for crash during a write, commit ordering after restart, or rollback of uncommitted entries.

### 2.7 Stress testing
- The repository includes concurrency and overload tests, but not Raft-specific stress tests.
- No chaos or fault-injection harness exists to simulate:
  - node stop/start cycles
  - network latency
  - dropped RPCs
  - repeated leader elections
  - concurrent writes while election timers fire
- No benchmark asserts performance under time-bounded commit or no-double-booking during leader transitions.

### 2.8 Docker
- `docker-compose.yml` exists, but the repository does not include a replica/failure smoke test that starts the stack and verifies:
  - all three app nodes come up
  - exactly one leader is elected
  - writes succeed through the service mesh
  - a node stop/start cycle does not corrupt state
- There is no health-check or service-level readiness condition that the CI or user can run deterministically.

### 2.9 UI
- There is no web UI or any user-facing front end in the repository.
- This is not a direct Milestone 2 assignment requirement, but it is a visible gap for a polished project demo and for a more complete end-user experience.

### 2.10 Documentation
- `README.md` and `src/README.md` are helpful, but they do not provide a milestone-specific compliance checklist tied directly to the assignment PDF.
- There is no documented M2 verification protocol: exact cluster setup, expected leader election behavior, failure-recovery steps, and acceptance criteria.
- The repository does not include a proof artifact for the assignment's strong-consistency and fault-tolerance expectations.

## 3) Review of latest commit `4f49383`

### Commit summary
`4f49383` is titled `fixes` and changes these files:
- `src/ticket_booking/application.py`
- `src/ticket_booking/auth.py`
- `src/ticket_booking/client.py`
- `src/ticket_booking/payment.py`
- `src/ticket_booking/state_machine.py`

### Exactly what it changed
The commit strengthens application-level correctness in these areas:
- Payment validation: finite numeric checks, NaN/inf guard, stricter minimum-price logic.
- Auth handling: per-user salt generation and compatibility changes around credential replay semantics.
- Booking/cancel idempotency: request-id metadata tracking and conflict detection keyed by user and request payload.
- Client retry behavior: leader redirection, bounded retries, seed-address rotation, and more graceful RPC-failure handling.
- State-machine request deduplication: exact duplicate retry detection and conflict prevention when a request-id is reused with a different payload or user.

### What it proves
This commit proves the project has improved resilience in the following areas:
- invalid payment amounts are rejected earlier and more consistently
- repeated booking/cancel attempts with the same request key are treated more carefully
- client-side retries and leader redirect logic are more robust for transient gRPC failures
- application-layer idempotency is stricter and less likely to produce duplicate side effects from retries

### What it does not prove
This commit does not prove:
- full Raft correctness under election or leader-failure scenarios
- correctness of majority-commit semantics under actual quorum loss
- correct follower catch-up after crash/restart
- cluster-wide strong consistency under concurrent multi-node writes
- Docker Compose deployment correctness in an actual multi-node failure scenario
- UI behavior or assignment-level documentation completeness
- that the project satisfies all Milestone 2 requirements as specified by the assignment PDF

No test files were modified in this commit; the proofs are therefore code-level and integration-level, not a new M2 protocol proof set.

## 4) Required tests that are missing

The following tests are required by the assignment's M2 scope but are not present in the current repo as explicit, documented, end-to-end checks:

1. Exact single-leader election under repeated startup and timeout races.
2. Deterministic stale-leader rejection test for `AppendEntries` with conflicting or stale term history.
3. Deterministic no-quorum write rejection when a leader cannot contact a majority.
4. Leader failover while write traffic continues, with no double-booking and no lost commits.
5. Follower catch-up after downtime and rejoin with persisted Raft state.
6. Read-after-write consistency across leader/follower reads after failover.
7. Concurrent multi-client booking under leader election churn with no lost or duplicated booking records.
8. Cluster-wide log equality test across all replicas after a write burst and a leader change.
9. Docker-compose smoke test covering startup, leader election, and node failure recovery.
10. Explicit M2 acceptance test that checks the system against the assignment PDF criteria, not just a unit/integration subset.
11. Formal or semi-formal check for persisted `currentTerm` / `votedFor` / log replay across crashes.
12. Stress/fault-injection script for partition and delayed RPC conditions.

## 5) Deleted, skipped, or weakened tests

Evidence check:
- No test file in `src/tests` is marked with `skip` or `xfail`.
- A repository search for `skip|xfail` under `src/tests` returned no such markers.
- A review of the current tree and `git` history does not show a deleted test from the current branch state.

Conclusion:
- There is no evidence that an existing test was deleted, skipped, or intentionally weakened in the current repository snapshot.
- However, the repository still lacks several required Milestone 2 tests, so the coverage is incomplete rather than weakened in a deliberately removed sense.

## 6) Full test suite command and result

Command executed:

```powershell
cd src; .\.venv\Scripts\python.exe -m pytest -q
```

Result:

```text
..........................................                               [100%]
42 passed in 70.51s (0:01:10)
```

Exit code: `0`

## Bottom line

The current repository contains a solid M1-style distributed booking system and a partially implemented M2 Raft layer, and the current tests pass. However, the repository does not yet provide the evidence needed to claim full Milestone 2 compliance with the assignment PDF. The largest remaining gaps are in protocol-level Raft safety proofs, quorum/failure recovery validation, Docker deployment verification, and milestone-specific documentation tied directly to the assignment requirements.
