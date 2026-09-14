# Work Log

## 2026-09-11 — TASK-SETUP-1 (started)
Fresh project reconstructed: `src` contains only the supplied `.venv`, with no application source or prior task history. The environment targets Python 3.13.14 but cannot start because its WindowsApps base interpreter is unavailable; no existing code was adopted or removed.

## 2026-09-11 — TASK-PROTO-1 (started)
Reviewed the current gRPC and Protocol Buffers Python generation guidance. Adding a single proto3 contract and a repeatable `grpc_tools.protoc` generation command; verification is deferred to the user's working virtual environment.

## 2026-09-11 — TASK-AUTH-1, TASK-BOOK-1 (started)
Implementing the M1 core behind RPC handlers as standard-library services. A mutex protects the local state-machine check-and-apply path so concurrent attempts for one seat cannot both succeed before Raft is introduced.

## 2026-09-11 — TASK-AUTH-1, TASK-AUTH-2, TASK-BROWSE-1, TASK-BOOK-1, TASK-BOOK-2, TASK-M1-TEST-1 (in progress)
Added session auth, token-gated browse/post facade, atomic in-memory booking/cancellation state, idempotent request handling, and pytest coverage for their primary acceptance paths. These tasks remain unchecked because the supplied environment cannot be run from this session; execution is deferred to the user's Python 3.13 setup.

## 2026-09-11 — Multi-Agent Coordination (Antigravity)
Established `COORDINATION.md` to split responsibilities cleanly with Codex. Codex leads App Core & Raft (`auth.py`, `state_machine.py`, `application.py`, `server.py`, Raft protocol). Antigravity leads LLM FAQ service, deterministic mock payment, client CLI, and end-to-end testing.

## 2026-09-11 — TASK-SETUP-1, TASK-PROTO-1 (Antigravity)
Installed project dependencies (`grpcio`, `grpcio-tools`, `protobuf`, `pytest`) into `.venv`. Executed `scripts/generate_proto.py` to compile `ticket_booking.proto` into `ticket_booking/generated/`. Added `src/tests/test_proto.py` and verified clean import and structure via pytest.

## 2026-09-11 — TASK-PAYMENT-1, TASK-LLM-1, TASK-LLM-2 (started, Antigravity)
Starting work on deterministic mock payment (`payment.py`), domain FAQ context knowledge base (`faq_data.py`), and independent gRPC LLM FAQ service with CPU local fallback (`llm_service.py`).

## 2026-09-11 — TASK-AUTH-1, TASK-AUTH-2, TASK-BROWSE-1, TASK-BOOK-1, TASK-BOOK-2 (verified, Antigravity)
Executed the test suite in `.venv` validating Codex's implementations: `test_auth.py` and `test_booking.py` passed all 5 tests under Python 3.13.14 (concurrent booking mutual exclusion, idempotency, session expiry, token validation).

## 2026-09-11 — TASK-PAYMENT-1, TASK-LLM-1, TASK-LLM-2 (completed, Antigravity)
Implemented `payment.py` with deterministic mock payment/refund rules and verified with 5 tests in `test_payment.py`. Implemented `faq_data.py` (RAG context retriever) and `llm_service.py` (gRPC LLM service with Ollama integration and CPU-only rule fallback engine). Verified over gRPC channel with 9 acceptance tests in `test_llm.py` covering all Appendix A FAQs and knowledge-base swapping.

## 2026-09-11 — TASK-CLIENT-1, TASK-M1-TEST-1 (completed, Antigravity)
Implemented `server.py` bridging gRPC ClientService with TicketApplication and PaymentGateway, and `client.py` featuring token-aware authentication, browsing, booking, cancellation, FAQ interaction, and transparent leader-redirection handling. Created client tests in `test_client.py` and cluster concurrency tests in `test_integration.py` (10 concurrent clients, double-booking prevention, mock payment verification, full booking/cancellation lifecycle). Entire test suite (26 tests) passed with 100% success in `.venv`.

## 2026-09-11 00:55 — M1 review against SRS (Cursor)
Reviewed M1 code against `SRS_Distributed_Ticket_Booking_System.md`. Corrected three logic gaps: (1) mock payment ran before the seat lock, so concurrent losers were charged; payment now runs only after the seat is known available, inside the state-machine lock, via leader `process_business_request`. (2) Clients called the LLM node directly; FAQ now goes through authenticated `get(token, "FAQ")` on the app server, which is the only caller of `GetLLMAnswer`, and live seat maps are injected for availability questions. (3) Successful cancel now issues an idempotent mock refund; declined cards no longer invent refunds for unknown `tx_` ids. Verified 27 tests in `.venv`.
