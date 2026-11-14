# Final project audit (Milestones 1 and 2)

Baseline evidence: `milestone2-audit.md`, `raft-design.md`, `docker-verification.md`,
`ui-verification.md`, `stress-test-report.md`, `milestone2-completion-report.md`. Items proven
there are marked VERIFIED and were not re-run unless code on their path changed in this pass.
Items re-run in this pass are marked RE-VERIFIED with the new result.

Final full suite: `cd src; python -m pytest -q -rf` -> `100 passed in 273.33s`,
`100 passed in 234.61s` (two consecutive runs).

## Milestone 1

| requirement | implementation | tests | UI / demo evidence | status | command -> actual result |
|---|---|---|---|---|---|
| gRPC client-server architecture | `proto/ticket_booking.proto`, `server.py` (`AppServer`), `client.py` | `test_proto`, `test_client`, `test_integration` | every CLI/UI action | VERIFIED | full suite -> 100 passed |
| login / signup / logout / sessions | `auth.py` (salted PBKDF2, hashed tokens, expiry); Raft-replicated in `server.py` | `test_auth`, `test_raft::...catchup` (signup/logout across nodes), `test_ui` | `ui-verification.md` Auth section (signup, weak password, wrong password, logout, session across views) | VERIFIED; RE-VERIFIED | `verify_ui_docker.py` -> ALL CHECKS PASSED |
| seat availability | `SeatMap.seats`, linearizable `Get SEATS` | `test_client::test_browse_shows`, `test_raft_m2` read tests | Booking view caption "confirmed by a Raft majority" | VERIFIED | - |
| booking, cancellation, multi-seat atomicity | `SeatMap.book/cancel` (sorted per-seat locks, all-or-nothing) | `test_booking::test_atomic_seats`, `test_overlap_seats`, `test_client::test_book_cancel` | My Bookings + Cancel | VERIFIED | - |
| concurrency control | per-seat locks + Raft-ordered apply + request-ID dedup | `test_booking::test_seat_race`, `test_integration::test_grpc_race`, churn/fault tests | Demo Lab race | VERIFIED; RE-VERIFIED | stress run 3 race -> 1 OK / 15 ALREADY_BOOKED |
| server-authoritative pricing | `BookingApp._price` charges `price x seats`; `Get QUOTE` | `test_final_quality::test_booking_charges_server_price_not_client_amount`, `test_raft_booking_charges_server_price_not_client_amount` | Booking view quote | FIXED in this pass | failed before (client `amount=500` was charged 500.00); passes after |
| mock payment success / decline / refund | `payment.py`, `apply_raft` | `test_payment`, `test_integration::test_declined_book`, `test_m2_features::test_payment_decline...`, `test_my_bookings_quote_and_refund` | Booking view; CLI `--card` | VERIFIED; CLI gap FIXED | `test_cli_lists_own_bookings_and_can_use_a_declining_mock_card` failed before, passes |
| FAQ chatbot on a separate LLM service | `llm_service.py` (gRPC), `AppServer._faq` | `test_llm`, `test_integration::test_cancel_faq`, `test_m2_features` | FAQ chat | VERIFIED; containerised runtime RE-VERIFIED | `--profile ollama` -> FAQ `OK`, ollama log `POST /api/generate 200` |
| domain awareness / off-topic handling | `faq_data.in_domain` + FAQ retrieval gate | `test_final_quality::test_off_topic_...` (6), `test_in_domain_queries_still_reach_the_llm` (7) | FAQ "out of domain" info | FIXED in this pass | 6 off-topic queries reached the LLM before; now `OUT_OF_DOMAIN` without an LLM call |
| LLM outage fallback | labelled verbatim FAQ text | `test_m2_features::test_llm_outage_...` (3) | FAQ warning | VERIFIED; RE-VERIFIED | `verify_docker.py` §7 PASS |
| no cross-user data exposure | `MY_BOOKINGS` filtered by session user; `cancel` checks owner | `test_final_quality::test_user_cannot_cancel_or_see_another_users_booking`, `test_raft_user_cannot_cancel_another_users_booking`, `test_m2_features::test_my_bookings_quote_and_refund` | UI Privacy section | VERIFIED (coverage added) | new tests pass (behaviour was already correct) |
| CLI workflows | `client.main` | `test_final_quality::test_cli_...` | README section 6 | FIXED (added `mybookings`, `--card`) | see above |
| stress-test workflow (M1 script) | `stress_test_clients.py` | - | - | VERIFIED | readiness pass: race 1 OK / 14 ALREADY_BOOKED, same request ID PASS (legacy and Raft) |
| project structure / setup | `src/requirements.txt` (single file), `scripts/generate_proto.py`, README sections 4-9 | fresh venv install | - | VERIFIED | readiness pass: `pip install -r requirements.txt` exit 0, `pip check` clean |
| input validation, clear errors | `_json_object`, `_price`, `parse_seats`, `prepare_signup` | `test_auth::test_signup_rules`, `test_booking::test_atomic_seats` (max 5), `test_m2_features` (quote limits) | UI invalid input (6 seats, weak password) | VERIFIED | - |

## Milestone 2

| requirement | implementation | tests | UI / demo evidence | status | command -> actual result |
|---|---|---|---|---|---|
| leader election | `RaftNode._elect`, `request_vote` | `test_raft` election tests, `test_raft_m2` one-leader-per-term under faults | Observatory, Demo Lab failover | VERIFIED; RE-VERIFIED | `verify_ui_docker.py`: failover measured 3439 ms |
| log replication, majority commit | `append_entries`, `_replicate_peer`, `_advance_commit` | `test_raft`, `test_raft_m2::test_follower_commit_is_bounded_by_last_new_entry` | log/state digests | VERIFIED | - |
| persistence and recovery | SQLite `raft_storage.py`, replay in `RaftNode.__init__` (now also rebuilds the request index) | `test_raft::test_raft_storage_replays_committed_entries`, `test_final_quality::test_request_index_is_rebuilt_on_restart` | Docker §8 full restart | VERIFIED; RE-VERIFIED | `verify_docker.py` -> ALL CHECKS PASSED (26 checks) |
| idempotent writes / retries | request-ID index + `SeatMap` + `Payments` dedup | `test_final_quality::test_propose_does_not_scan_the_log_for_request_ids`, `test_request_index_forgets_truncated_entries`, `test_raft_retry_with_unnormalized_seat_list_returns_same_booking` | Demo Lab same-key retry | FIXED (identical retry with "A1; A2" returned `REQUEST_CONFLICT`); IMPROVED (index) | stress run 3: 10 duplicates -> 1 booking |
| strong consistency (critical operations) | majority commit; `read_barrier()` | `test_raft_m2` read tests, no-quorum test | Booking STALE banner, Demo Lab no quorum | VERIFIED; RE-VERIFIED | Docker §6 PASS |
| overbooking prevention | as above | churn, fault injection, stress | Demo Lab race | VERIFIED; RE-VERIFIED | stress run 3: 7/7 invariants PASS |
| fault tolerance, leader-kill recovery | election + catch-up | `test_raft_m2` churn/fault, `test_raft::...catchup` | Demo Lab steps 3-4 | VERIFIED; RE-VERIFIED | Docker §4-6 PASS |
| stress testing | `scripts/stress_m2.py` | - | `stress-test-report.md` runs 1-3 | VERIFIED; RE-VERIFIED | run 3: 182.3 write req/s, error rate 0 |
| 4-5 node topology | compose: `llm`, `app-1..3`, `ui` (+ optional `ollama`) | `verify_docker.py` | - | VERIFIED | - |
| Python only, gRPC between services | all services Python; gRPC for client/app/Raft/LLM; HTTP only to the Ollama model runtime inside the LLM node | `test_ui::test_ui_is_grpc_only` | README section 11 | VERIFIED | - |

## Gaps found in this pass and their resolution

| # | finding | kind | resolution | evidence |
|---|---|---|---|---|
| 1 | client-supplied `amount` above the price was charged (legacy and Raft paths) | M1 defect | charge `price x seats`; `amount` only has to cover it | 2 new tests failed before, pass after |
| 2 | Raft pre-check compared the raw seat string, the state machine the normalized one: identical retry of "A1; A2" -> `REQUEST_CONFLICT` | M2 defect | pre-check uses the normalized seat list | new test failed before (`REQUEST_CONFLICT`), passes after |
| 3 | off-topic questions sharing a substring with FAQ trigger words reached the LLM | M1 quality defect | whole-word booking vocabulary gate in the app server (LLM retrieval unchanged) | 6 new tests failed before, pass after; 7 in-domain tests pass |
| 4 | CLI could not list bookings (needed for cancel) or use a declining card | M1 gap | `--action mybookings`, `--card` | new test failed before, passes after |
| 5 | `propose()` scanned the whole log per write | M2 limitation | request-ID index rebuilt on replay, pruned on truncation | spy test; stress run 3 vs run 2 |
| 6 | `ollama-pull` could start before the Ollama server was ready | Docker defect risk | `ollama` health check + `service_healthy` dependency | profile run: healthy then pull `success` |
| 7 | verification scripts assumed empty volumes (fixed seats) | script defect | pick free seats in any show | both scripts pass on a used cluster |
| 8 | no test for cross-user cancellation | missing evidence | 2 tests (legacy and Raft) | pass |

Not changed, with reason:

- Fault injection already sits on each node's real gRPC Raft channel (client interceptor on
  real sockets). Moving it into `RaftNode` would add a test-only hook to production code.
- `SeatMap` records some rejection outcomes outside `self.lock` (pre-existing). The two dict
  writes are idempotent per request ID and each is atomic under the GIL; no failing case was
  found, so it was left unchanged.
- Legacy (non-Raft) `--follower` processes do not share sessions with the leader
  (pre-existing; documented).

## Refactors (behaviour-preserving, covered by the existing suite)

- `state_machine.parse_seats` replaces four copies of seat-list parsing; `SeatMap.book/cancel`
  build the request metadata once.
- `BookingApp._price` replaces two copies of amount validation; `raft_post` builds the Raft
  body once.
- `Client._post` replaces the duplicated redirect/retry loops of `book_seat`/`cancel_seat`.
- `server._json_object` replaces two copies of payload parsing in `Get`/`Post`;
  `_forward_auth` renamed `_forward` (it also forwards reads).
- Removed unused imports in two test modules.
