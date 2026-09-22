# Tasks

## M1 — Foundation, gRPC & LLM
- [x] TASK-SETUP-1 — Restore the Python 3.13 virtual environment; create the package, dependency, test, and generated-protobuf build layout (M1 project structure & setup) [Owner: Joint / Antigravity]
- [x] TASK-PROTO-1 — Define and generate the ClientService, RaftService, and LLMService protobuf interfaces (FR-AUTH-1, FR-RAFT-1, FR-LLM-1) [Owner: Codex / Antigravity]
- [x] TASK-AUTH-1 — Implement credential validation and expiring, opaque in-memory sessions (FR-AUTH-1) [Owner: Codex]
- [x] TASK-AUTH-2 — Implement logout and enforce session validation on all post/get RPCs (FR-AUTH-2, FR-AUTH-3) [Owner: Codex]
- [x] TASK-BROWSE-1 — Implement committed show and per-seat browsing (FR-BROWSE-1, FR-BROWSE-2) [Owner: Codex]
- [x] TASK-BOOK-1 — Implement leader-side single-node seat booking with a serialized apply queue (FR-BOOK-1, FR-BOOK-2) [Owner: Codex]
- [x] TASK-BOOK-2 — Implement cancellation and idempotent request replay in the booking state machine (FR-BOOK-4) [Owner: Codex]
- [x] TASK-PAYMENT-1 — Implement the deterministic mock payment confirmation step (FR-BOOK-5) [Owner: Antigravity]
- [x] TASK-LLM-1 — Implement an independent gRPC LLM FAQ service with a CPU-only local fallback (FR-LLM-1, FR-LLM-4) [Owner: Antigravity]
- [x] TASK-LLM-2 — Add domain FAQ retrieval/context injection and FAQ acceptance tests (FR-LLM-2, FR-LLM-3) [Owner: Antigravity]
- [x] TASK-CLIENT-1 — Implement a token-aware CLI client with leader redirects and clear status output (FR-AUTH-1, FR-AUTH-3) [Owner: Antigravity]
- [x] TASK-M1-TEST-1 — Add M1 unit and integration coverage for auth, browsing, booking concurrency, cancellation, and LLM FAQs (M1 testing deliverable) [Owner: Joint / Antigravity]
- [x] TASK-BOOK-3 — Multi-seat booking (max 5), atomic all-or-nothing, per-seat locks for parallel different seats/shows (FR-BOOK-1, FR-BOOK-2 extension)
- [x] TASK-CATALOG-1 — Index shows and FAQ into SQLite on booking-server process start; load catalog from that DB

## M2 — Raft Consensus & Fault Tolerance
- [ ] TASK-RAFT-1 — Persist Raft currentTerm, votedFor, and append-only log state per node (FR-RAFT-1, FR-RAFT-2) [Owner: Codex]
- [ ] TASK-RAFT-2 — Implement RequestVote term, vote, and log-freshness rules (FR-RAFT-1) [Owner: Codex]
- [ ] TASK-RAFT-3 — Implement randomized election timeouts and leader election (FR-RAFT-1, FR-RAFT-4) [Owner: Codex]
- [ ] TASK-RAFT-4 — Implement AppendEntries consistency checks, conflict truncation, and heartbeats (FR-RAFT-2) [Owner: Codex]
- [ ] TASK-RAFT-5 — Implement majority commit tracking and the Raft propose/apply boundary (FR-RAFT-3, FR-BOOK-3) [Owner: Codex]
- [ ] TASK-RAFT-6 — Implement rejoining follower catch-up and committed-state replay (FR-RAFT-5) [Owner: Codex]
- [ ] TASK-FT-1 — Verify follower loss preserves booking availability with a quorum (FR-FT-1) [Owner: Antigravity]
- [ ] TASK-FT-2 — Verify leader loss triggers bounded re-election and continued writes (FR-RAFT-4, FR-FT-2) [Owner: Antigravity]
- [ ] TASK-FT-3 — Add fault-injection tests for no loss or double-booking across one-node failures (FR-FT-3) [Owner: Antigravity]
- [ ] TASK-ADMIN-1 — Expose cluster role, term, and log-length inspection (FR-ADMIN-1) [Owner: Codex]
- [ ] TASK-ADMIN-2 — Generate booking and cancellation analytics (FR-ADMIN-2) [Owner: Antigravity]
- [ ] TASK-OPS-1 — Add node event logging, Docker Compose topology, and reproducible local deployment documentation (NFR observability, scalability, M1/M2 deliverables) [Owner: Joint / Antigravity]
- [ ] TASK-FINAL-1 — Produce the required setup/usage README and validate the end-to-end demo script (Submission checklist) [Owner: Joint]

