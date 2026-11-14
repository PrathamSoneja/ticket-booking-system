# Milestone 2 completion report

Date: 2026-11-14. Repository: `ADV OS/Project`, branch `main`. Work started from `867e3ee`;
the code changes were committed by the repository owner as `915a084`; the submission-readiness
pass (section 10) added commits `1735012` to `9d27d0a` plus a final docs commit. Everything,
including `docs/` and `src/ui/`, is committed; nothing has been pushed.

## 1. Executive summary

**Readiness: READY WITH LIMITATIONS.**

Every task in the Milestone 2 brief was implemented and verified by running it: Raft gaps
closed (two real defects found by failing tests and fixed), Docker Compose topology verified end
to end on this machine, Streamlit UI with Booking / Cluster Observatory / Demo Lab built as a
pure gRPC client, stress test run with a leader kill, LLM boundary hardened, documentation
written. Final full suite after the quality pass (section 11): **100 passed** in two
consecutive runs (273 s, 235 s). A fresh clone builds with `--no-cache` and passes the Docker
and UI verification scripts (section 10); both scripts also pass on a used cluster (section 11).

Limitations that remain (details in section 7): no log compaction (latency grows with log
length), static membership, no leader lease, single-machine fault injection, keyword-based
out-of-domain filter.

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
| `src/Dockerfile` | installs the single `requirements.txt`; retries the NLTK data download | Streamlit in the shared image; reliable builds |
| `README.md` | three outdated statements corrected; sections 11-18 added | final documentation |

Added: `src/ticket_booking/healthcheck.py`, `src/ui/app.py`, `src/ui/cluster.py`,
`src/ui/__init__.py`, `src/scripts/verify_docker.py`, `src/scripts/stress_m2.py`,
`src/.dockerignore`, `src/tests/raft_faults.py`,
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
| UI flows end to end against Docker (all three views, failover, no quorum, LLM outage, privacy) | PASSED | `docs/ui-verification.md` (`verify_ui_docker.py`, real `app.py` via AppTest over gRPC) |
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

- No log compaction/snapshots; the log and the request-ID index grow with every write.
- Static membership; no joint consensus.
- No leader lease / check-quorum: an isolated leader keeps its role (but cannot commit or
  serve reads).
- Each linearizable read costs a heartbeat round.
- Fault injection is client-side on real local sockets, not OS-level partitions.
- The FAQ domain gate is a fixed booking vocabulary plus FAQ retrieval; an off-topic question
  that uses a booking word (e.g. "show me a joke") still reaches the model.
- `CLUSTER_STATUS` is node-local and requires a session.
- Legacy (non-Raft, M1) mode: a `--follower` run as a separate process does not share sessions
  with the leader (pre-existing; Raft mode is the supported multi-node mode).
- `docker compose build` downloads packages and NLTK data; a network outage fails the build
  (the NLTK step retries, pip uses its own retries).

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

See `docs/submission-checklist.md`. Outstanding items for the team: push when ready, record the video from `docs/demo-script.md`,
fill in individual reflections from `docs/reflection-template.md`, build the zip without
`src/.venv`.


## 10. Submission-readiness pass (final)

### Changes

| commit | change | why |
|---|---|---|
| `1735012` | `src/requirements.txt` rewritten as UTF-8 with `streamlit==1.50.0` added and `packaging` pinned to `25.0` (streamlit 1.50 requires `<26`; pytest 8.4.2 accepts 25.0); `requirements-ui.txt` deleted; Dockerfile installs one file | one requirements file, no workaround |
| `1735012` | `docs/` removed from `.gitignore`; all reports committed; absolute local paths removed from `milestone2-audit.md` and from the `--json` argument recorded in `stress-results/*.json` (measured values untouched) | deliverables in git, no local paths |
| `1735012` | `test_raft.py::test_cluster_election_replication_failure_and_catchup`: both wait loops also wait for `last_applied == commit_index`; new assertions `last_applied == 2` and `restarted.last_applied == restarted.commit_index` | flaky test (read seat state before the last committed entry was applied); assertions only added |
| `97eab82` | Dockerfile retries the NLTK `stopwords` download (5 attempts) | a clean `--no-cache` build failed on one TLS hiccup |
| `c20b55a` | `verify_docker.py` counts only volumes of the current compose project | false failure when another compose project's volumes exist |
| `ae8c25c` | UI Booking view keeps the last loaded show list when the show read fails; test `test_booking_view_shows_stale_seat_map_when_quorum_is_lost` | the STALE seat-map state was unreachable without quorum (the show-list read failed first and the view returned) |
| `9d27d0a` | Demo Lab stores show/seat with the pending no-quorum request ID; test `test_demo_lab_no_quorum_retry_survives_view_switch`; new `scripts/verify_ui_docker.py` | after visiting another view the widgets reset, so the retry sent the same request ID with a different seat and got `REQUEST_CONFLICT` |

### Verification (actual results)

| check | command | result |
|---|---|---|
| clean dependency install | new venv, `python -m pip install -r src/requirements.txt`; `pip check` | exit 0; "No broken requirements found." |
| flaky test | `pytest tests/test_raft.py::test_cluster_election_replication_failure_and_catchup` x15 | 15 passed, 0 failed (before: 3/20 failed on HEAD `867e3ee`, 4/20 with M2 changes) |
| new UI regression tests | `pytest tests/test_ui.py` | each failed before its fix as described; `8 passed` after |
| M1 legacy mode (single server :50061, follower :50062, local LLM) | CLI `signup`, `shows`, `seats`, `book` (3 seats), `book` same seat, `status`, `analytics`, `faq`, `cancel` | all as expected (`OK`, `ALREADY_BOOKED`, cancel `OK`, FAQ answered by the LLM) |
| M1 stress workflow, legacy | `python -m ticket_booking.stress_test_clients --server 127.0.0.1:50061 --race-workers 15 --show-id show-5 --seat-id A9` | race `{'OK': 1, 'ALREADY_BOOKED': 14}` PASS; same request_id PASS |
| M1 stress workflow, Raft (fresh cluster) | same script, `--server 127.0.0.1:50052 --show-id show-6 --seat-id A30` | race `{'OK': 1, 'ALREADY_BOOKED': 14}` PASS; same request_id PASS |
| fresh clone build | `git clone`, then `docker compose build --no-cache` | built (after one TLS and one PyPI network failure, see `docker-verification.md`) |
| fresh clone topology | `python src/scripts/verify_docker.py` | ALL CHECKS PASSED (health, one leader, booking, leader kill, read-after-write, catch-up, no quorum, LLM outage, `down` keeps volumes, full restart) |
| UI against Docker | `python src/scripts/verify_ui_docker.py` | ALL CHECKS PASSED (`docs/ui-verification.md`) |
| containerised UI | browser at http://127.0.0.1:8501 | login page rendered |
| full suite | `cd src; python -m pytest -q -rf` x2 | `78 passed in 207.22s`, `78 passed in 215.25s` |

Milestone 1 regression audit: login/signup/logout/sessions, seat availability, reservation with
concurrency control, booking/cancellation, mock payment success/decline, FAQ via the separate
LLM gRPC service, CLI commands and the original stress script all work. The M1 test files
(`test_auth`, `test_booking`, `test_client`, `test_integration`, `test_llm`, `test_payment`,
`test_proto`) are unmodified since `867e3ee` and pass. No M1 regression was found. Observed but
pre-existing and unchanged: in legacy (non-Raft) mode a `--follower` started as a separate
process has its own in-memory state, so a session created on it is rejected by the leader
(`AUTH_FAILED`); the in-process legacy follower used by `test_client.py` and the Raft mode do not
have this problem.

Final readiness: **READY WITH LIMITATIONS**. The limitations are the design limits in section 7
and the containerised-Ollama profile not being exercised; no known defect is open.


## 11. Whole-project quality pass

Full audit: [`final-project-audit.md`](final-project-audit.md).

Fixed (each with a test that failed first): server-authoritative pricing on both write paths;
identical Raft retry with an unnormalized seat list returning `REQUEST_CONFLICT`; off-topic
FAQ questions reaching the LLM; CLI without `mybookings`/`--card`. Improved: request-ID index
instead of a full-log scan; `ollama-pull` waits for a healthy `ollama`; verification scripts
pick free seats. Refactors are listed in the audit.

| check | command | result |
|---|---|---|
| new tests | `pytest tests/test_final_quality.py` | 11 failed before their fixes (10 in the first run plus the retry-normalization test); the 2 index guards, 7 in-domain and 2 cross-user tests passed throughout; 22 passed after |
| full suite x2 | `cd src; python -m pytest -q -rf` | `100 passed in 273.33s`, `100 passed in 234.61s` |
| Docker on used volumes | `python src/scripts/verify_docker.py` | ALL CHECKS PASSED (26) |
| UI on used volumes | `python src/scripts/verify_ui_docker.py` | ALL CHECKS PASSED (43) |
| bounded stress | `stress_m2.py --duration 20 --workers 12 --failover none ...` | 7/7 invariants PASS; 182.3 write req/s, p50 47.2 ms (run 2: 81.1 req/s, p50 115.1 ms) |
| containerised Ollama | `docker compose --profile ollama up -d` | `ollama` healthy -> `ollama-pull` success; FAQ generated via `http://ollama:11434`; ollama log `POST /api/generate 200` |

Final readiness: **READY WITH LIMITATIONS** (design limits in section 7; no known open defect).
