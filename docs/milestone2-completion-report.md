# Milestone 2 completion report

Date: 2026-11-14. Repository: `ADV OS/Project`, branch `main`. Work started from `867e3ee`;
the code changes were committed by the repository owner during the session as `915a084`
("Adding docker and UI and some fixes"). **Not in that commit:** `src/requirements-ui.txt`
(required by the committed `Dockerfile`) and everything under `docs/` (git-ignored).

## 1. Executive summary

**Readiness: READY WITH LIMITATIONS.**

Every task in the Milestone 2 brief was implemented and verified by running it: Raft gaps
closed (two real defects found by failing tests and fixed), Docker Compose topology verified end
to end on this machine, Streamlit UI with Booking / Cluster Observatory / Demo Lab built as a
pure gRPC client, stress test run with a leader kill, LLM boundary hardened, documentation
written. Final full suite: **76 passed** in two consecutive runs (192 s, 136 s). One pre-existing test in `test_raft.py` is intermittently flaky on the untouched baseline as well (section 4).

Limitations that remain (details in section 7): no log compaction (latency grows with log
length), static membership, no leader lease, single-machine fault injection, keyword-based
out-of-domain filter, and two git gaps: `src/requirements-ui.txt` is untracked although the
committed `Dockerfile` needs it, and `docs/` is listed in `.gitignore`, so none of the reports
are committed.

## 2. Files changed and why

Modified:

| file | change | reason |
|---|---|---|
| `src/ticket_booking/raft_node.py` | `read_barrier()` (ReadIndex); `_replicate_peer` returns leadership ack; follower commit = `min(leaderCommit, prev_index+len(entries))`; `status()` adds `last_log_term`, `match_index`; `log_digest()` | authoritative reads; Raft Fig. 2 commit rule; observability |
| `src/ticket_booking/server.py` | `Get`: followers forward (one hop, `x-tb-forwarded`), leader runs read barrier; `CLUSTER_STATUS` adds `log_digest`, `state_digest`; FAQ moved to `_faq()` with correlation ID, out-of-domain gate, labelled fallback, supporting context item; `Post` sets `redirect_to` on mid-proposal leadership loss; `TB_LLM_TIMEOUT` | linearizable reads; LLM boundary; UI data |
| `src/ticket_booking/application.py` | `NOOP` apply; `MY_BOOKINGS`, `QUOTE` reads; `state_digest()` | read barrier; UI My Bookings and server-side price; consistency evidence |
| `src/ticket_booking/state_machine.py` | `user_bookings()`, `digest_rows()` | My Bookings; state digest |
| `src/ticket_booking/payment.py` | `txn_summary()`, `digest_rows()` | per-booking charge count / refund; state digest |
| `src/ticket_booking/client.py` | `redirect_map`; retry `NO_LEADER`; `get_my_bookings`, `get_quote`, `ask_faq_detailed` | Docker UI redirects; election windows; UI |
| `docker-compose.yml` | one image, named volumes, health checks, UI service, no LLM dependency for Raft nodes, Ollama behind a profile, `OLLAMA_URL` override | reproducible topology |
| `src/Dockerfile` | installs `requirements-ui.txt` in a second step | Streamlit in the shared image |
| `README.md` | three outdated statements corrected; sections 11-18 added | final documentation |

Added: `src/ticket_booking/healthcheck.py`, `src/ui/app.py`, `src/ui/cluster.py`,
`src/ui/__init__.py`, `src/scripts/verify_docker.py`, `src/scripts/stress_m2.py`,
`src/requirements-ui.txt`, `src/.dockerignore`, `src/tests/raft_faults.py`,
`src/tests/test_raft_m2.py`, `src/tests/test_m2_features.py`, `src/tests/test_ui.py`,
`docs/docker-verification.md`, `docs/stress-test-report.md`, `docs/stress-results/*.json`,
`docs/demo-script.md`, `docs/reflection-template.md`, `docs/submission-checklist.md`, this
report. `docs/raft-design.md` updated (audit list re-labelled, verified changes and current
limitations added).

No proto change was needed (new read types use the existing `Get.type` string and `Item`s).
No existing test file was modified (`git diff 867e3ee --stat -- src/tests` lists only new files); no `skip`/`xfail` markers exist in `src/tests`.

## 3. Requirement coverage

| requirement | status | evidence |
|---|---|---|
| authoritative read policy (quorum-confirmed barrier) | PASSED | `test_partitioned_old_leader_does_not_serve_stale_seat_map` (failed before fix: deposed leader returned `OK`, A9 `AVAILABLE`), `test_single_node_read_barrier_commits_current_term_entry`, `test_follower_reads_are_forwarded_and_linearizable`; Docker §6 read refused `NO_QUORUM` |
| read-after-write across leader failover | PASSED | `test_read_after_write_survives_leader_failover`; Docker §4 |
| no-quorum booking, then same-ID retry | PASSED | `test_no_quorum_booking_has_no_side_effects_and_retry_is_idempotent`; Docker §6 |
| RPC fault injection (delay, drop, reorder, duplicate on AppendEntries/RequestVote) | PASSED | `test_fault_injected_raft_rpcs_keep_safety` (asserts every fault type fired) |
| concurrent booking during election churn | PASSED | `test_concurrent_booking_during_election_churn` |
| follower commit rule | PASSED | `test_follower_commit_is_bounded_by_last_new_entry` (failed before fix) |
| Docker topology (5 services, volumes, health, discovery, no socket in UI) | PASSED | `docs/docker-verification.md` §1 |
| Docker: one leader, booking, failure/recovery, catch-up, no quorum, `down` keeps volumes, full restart | PASSED | `docs/docker-verification.md` §2-8 |
| Raft nodes start before LLM; LLM retried safely | PASSED | compose has no `depends_on: llm` for app nodes; Docker §7 (LLM stopped, booking OK, LLM restarted) |
| UI Booking view | PASSED | `test_streamlit_views_render_against_live_cluster` (login, seat grid, quote, booking, My Bookings); live check against Docker UI |
| UI Cluster Observatory | PASSED | `test_observatory_distinguishes_unreachable_from_follower`, AppTest run |
| UI Demo Lab | PASSED | `test_demo_lab_race_and_retry_checks_are_real`, `test_demo_lab_failover_measurement_and_catch_up`, AppTest race |
| UI gRPC-only boundary | PASSED | `test_ui_is_grpc_only[app.py/cluster.py]`; Docker §1 (UI has no mounts) |
| stress: race, no overbooking, no duplicate charge, failover, metrics | PASSED | `docs/stress-test-report.md` |
| LLM: gRPC app-to-LLM, correlation IDs, out-of-domain, outage fallback, no state mutation, booking with LLM down | PASSED | `test_m2_features.py` (9 LLM-related cases); Docker §7 |
| payment decline / refund | PASSED | `test_payment_decline_creates_no_booking_or_charge`, `test_my_bookings_quote_and_refund` |
| full restart persistence | PASSED | Docker §8 |
| UI Demo Lab guided failover / no-quorum performed by hand through the browser | NOT RUN | the same helper functions were run by tests and the same scenarios by `verify_docker.py`; the click-through itself is for the demo recording |
| containerised Ollama (`--profile ollama`) | NOT RUN | verified with host Ollama instead; the profile pulls about 1.3 GB model plus image |

## 4. Commands and actual results

Baseline before any change (from `src`): `.venv/Scripts/python.exe -m pytest -q` -> `49 passed in 81.12s`.

| command (from `src` unless noted) | result |
|---|---|
| `python -m pytest tests/test_raft_m2.py` before fixes (subset without churn/fault) | 3 failed, 3 passed (the two defects + missing `read_barrier`) |
| same after fixes | 6 passed |
| `python -m pytest tests/test_raft_m2.py` x5 consecutively | 8 passed each time (28.9-31.1 s) |
| `python -m pytest tests/test_m2_features.py` | 13 passed |
| `python -m pytest tests/test_ui.py` | 6 passed |
| full suite after all changes (final, x2): `python -m pytest -q -rf` | `76 passed in 192.44s`, `76 passed in 136.27s` |
| earlier full-suite runs during the work | `57 passed` (after the Raft fixes); then `2 failed, 74 passed`, `1 failed, 75 passed`, `1 failed, 75 passed` - see below |
| repo root: `docker compose build` | image `ticket-booking:m2` built |
| repo root: `OLLAMA_URL=http://host.docker.internal:11434/api/generate docker compose up -d` | 5 services healthy |
| repo root: `python src/scripts/verify_docker.py` | ALL CHECKS PASSED (`docs/docker-verification.md`) |
| repo root: `python src/scripts/stress_m2.py --duration 30 --workers 12 --failover docker` | all 7 checks PASS |
| repo root: `python src/scripts/stress_m2.py --duration 20 --workers 12 --failover none --shows show-6,show-7 ...` | all 7 checks PASS |

`python` here is `src/.venv/Scripts/python.exe` (Python 3.13.14).

Failures seen during the work, and what they were:

- `test_raft.py::test_cluster_election_replication_failure_and_catchup` (existing test, not
  modified) failed intermittently at line 492: right after the restarted node's
  `commit_index` equals the leader's, the test reads the seat grid, and the last committed
  booking can still be in the apply step (commit index is updated just before entries are
  applied). Measured 20 isolated runs each: **untouched HEAD `867e3ee` 3/20 failures, with this
  work 4/20** - a pre-existing race in the test's wait condition, not a regression. Left as is
  (changing the test would weaken it); noted as a limitation.
- `test_raft_m2.py::test_concurrent_booking_during_election_churn` (new test) failed because
  its post-failover check was too strict: all 40 seats could be booked before the leader was
  killed. The test now switches workers to a fresh seat range after the leader change and
  requires a committed booking from that range; 5/5 isolated passes plus the two final suite
  runs. (One of the failures was caused by an external rewrite of the file that reverted this
  fix; see section 7.)

## 5. Docker, stress, UI and novelty evidence

- Docker: `docs/docker-verification.md` - verbatim script output; failover in that run
  3.76 s including the ~3.6 s `docker compose stop` itself.
- Stress: `docs/stress-test-report.md` and `docs/stress-results/` - with a leader kill:
  3264 committed booking writes in 30 s (108.7 req/s), error rate 0, p50/p95/p99 write latency
  66.7/143.3/211.1 ms, failover 574 ms from kill to new leader observed; 16-way race exactly one
  winner; 10 duplicate request IDs -> one booking; all invariants PASS. A later baseline run was
  slower (81.1 req/s) because the log had grown (documented limitation).
- UI: AppTest drove login -> seat selection -> quote -> booking -> Observatory -> Demo Lab race
  against a live in-process 3-node cluster. The Dockerised UI at http://127.0.0.1:8501 was
  opened in a browser; login and the Booking view (linearizable seat map, My Bookings with one
  charge per booking) worked against the Docker cluster.
- Novelty: Demo Lab (race, same-key retry, measured failover, catch-up digests, guided no-quorum,
  correlated timeline, downloadable summaries) and the fault-injection harness
  (`tests/raft_faults.py`).

## 6. Security review

- No secrets or credentials added; only the pre-existing seeded demo accounts are used.
- Mock cards only (`4242...` approves, `...0000` declines); UI labels them as mock.
- LLM request IDs are `faq-<uuid4>`; a test asserts the session token is not in the request ID,
  query or context sent to the LLM.
- Logs contain node IDs, terms, indices, correlation IDs and gRPC status codes only; no
  passwords, tokens, card numbers or payloads.
- UI container has no volumes and no Docker socket; UI code has no shell, DB or server imports
  (tested).

## 7. Known limitations

- No log compaction/snapshots; request-ID dedup scans the whole log (O(n)), so latency grows
  with log length.
- Static membership; no joint consensus.
- No leader lease / check-quorum: an isolated leader keeps its role (but cannot commit or
  serve reads).
- Each linearizable read costs a heartbeat round.
- Fault injection is client-side on real local sockets, not OS-level partitions.
- FAQ out-of-domain detection is keyword based (NLTK stopwords + FAQ keywords); questions that
  share a keyword with the FAQ pass the gate and are answered by the model with the FAQ context.
- `CLUSTER_STATUS` is node-local and requires a session.
- Streamlit forces `packaging` 25.0 instead of the 26.3 pin (pytest only; suite passes).
- Git: `src/requirements-ui.txt` is untracked (a clean clone cannot `docker compose build`
  until it is committed); `docs/` is git-ignored, so the reports are not in `915a084`.
- `test_raft.py::test_cluster_election_replication_failure_and_catchup` is flaky (about 15-20%
  of isolated runs, same on the baseline) because it checks applied state as soon as the
  commit index matches.
- During the session the new Python files were rewritten outside this work (module docstrings
  removed, 15:30:52-15:31:48). One rewrite used a stale copy of `test_raft_m2.py` and reverted
  the churn-test fix; it was re-applied, re-verified, and is present in `915a084`. No other
  edit was lost (checked).

## 8. Viva preparation

| likely question | accurate answer |
|---|---|
| How is a leader elected? | Follower times out (random 0.45-0.8 s), increments and persists its term and self-vote, sends `RequestVote` with last log index/term. A node grants at most one vote per term and only to a candidate whose log is at least as up to date. Majority (2 of 3) -> leader; heartbeats every 100 ms. |
| When is an entry committed? | When the leader has it on a majority and it is from the leader's current term (`_advance_commit` skips older-term indices); earlier entries commit with it. |
| How do you prevent double booking? | Every booking is a Raft log entry applied in the same order on every node; apply takes per-seat locks in sorted order and rejects already-booked seats; request IDs dedup retries in the log, state machine and payment ledger. |
| Are reads strongly consistent? | Yes for everything except `CLUSTER_STATUS`: followers forward to the leader, the leader runs a ReadIndex barrier (current-term commit, majority heartbeat after recording the index, apply up to it). Before this change a deposed leader could serve stale seat maps - a test reproduced it. |
| What happens without a majority? | Writes are appended but never committed, so the client gets `NO_QUORUM`/timeout and no charge is applied; reads return `NO_QUORUM`. When a majority returns the entry may commit; retrying with the same request ID returns that one outcome. |
| Why not a lease-based read? | Leases depend on bounded clock drift; ReadIndex needs only message exchange. Cost: one heartbeat round per read. |
| How does a restarted node catch up? | Term, vote, log and commit index are in SQLite; on start it replays committed entries, then the leader backs `next_index` off until logs match and sends the missing suffix. |
| Is everything gRPC? | All service-to-service calls are gRPC. Inside the LLM node, `llm_service.py` calls Ollama over its local HTTP API; Ollama is the model runtime, not a project service. |
| Can the LLM change bookings? | No. FAQ is a read; the answer is returned as text and never parsed or proposed to Raft. A test feeds booking/cancel commands as the LLM answer and checks the state digest is unchanged. |
| What did fault injection test? | A gRPC client interceptor on each node's Raft channels randomly delays, drops requests, drops responses after delivery, duplicates, and replays older requests, during bookings and a leader crash; asserts one leader per term, no lost/duplicated bookings, one charge each, identical committed logs. |
| Trade-offs? | Simplicity over throughput: SQLite with synchronous=FULL per entry, whole-log dedup scan, no batching or snapshots; heartbeat-per-read for correctness. |

## 9. Submission checklist

See `docs/submission-checklist.md`. Outstanding items for the team: commit
`src/requirements-ui.txt`, decide about the `docs/` line in `.gitignore` (or add the docs to the
zip separately), record the video from `docs/demo-script.md`,
fill in individual reflections from `docs/reflection-template.md`, build the zip without
`src/.venv`.
