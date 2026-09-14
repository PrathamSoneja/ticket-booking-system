# Multi-Agent Coordination Protocol: Codex & Antigravity

This document coordinates concurrent development between **Codex** and **Antigravity** to ensure zero rollbacks, no duplicated work, and clean modular boundaries.

---

## 1. Responsibilities & Ownership Division

### Codex (Application Core & Raft Lead)
- **Files Owned:**
  - `src/ticket_booking/auth.py`
  - `src/ticket_booking/state_machine.py`
  - `src/ticket_booking/application.py`
  - `src/ticket_booking/server.py` (ClientService gRPC server implementation)
  - `src/ticket_booking/raft/` (M2: Raft consensus, logs, election, replication)
  - `src/tests/test_auth.py`
  - `src/tests/test_booking.py`
- **Assigned Tasks:**
  - `TASK-AUTH-1` (Credential validation & sessions)
  - `TASK-AUTH-2` (Logout & session enforcement)
  - `TASK-BROWSE-1` (Committed show & seat browsing)
  - `TASK-BOOK-1` (Single-node seat booking & mutex/queue)
  - `TASK-BOOK-2` (Cancellation & idempotent request replay)
  - `TASK-RAFT-1` through `TASK-RAFT-6` (M2 Raft protocol implementation)

### Antigravity (LLM Service, Payment, Client CLI & Testing Lead)
- **Files Owned:**
  - `src/ticket_booking/payment.py` (Mock payment confirmation engine)
  - `src/ticket_booking/faq_data.py` (Domain FAQ knowledge base & context retriever)
  - `src/ticket_booking/llm_service.py` (Independent gRPC LLM FAQ service & CPU fallback)
  - `src/ticket_booking/client.py` (Interactive and automated token-aware CLI client)
  - `src/tests/test_proto.py`
  - `src/tests/test_payment.py`
  - `src/tests/test_llm.py`
  - `src/tests/test_client.py`
  - `src/tests/test_integration.py`
  - `docker-compose.yml` & deployment topology (M1/M2)
- **Assigned Tasks:**
  - `TASK-SETUP-1` (Completed: package installation & venv verification)
  - `TASK-PROTO-1` (Completed: proto generation & verification)
  - `TASK-PAYMENT-1` (Deterministic mock payment step)
  - `TASK-LLM-1` (Independent gRPC LLM FAQ service)
  - `TASK-LLM-2` (Domain FAQ retrieval & context injection)
  - `TASK-CLIENT-1` (Token-aware CLI client)
  - `TASK-M1-TEST-1` (M1 integration test suite)
  - `TASK-FT-1` to `TASK-FT-3` (M2 Chaos / fault-injection test suite)

---

## 2. Collaboration Rules

1. **Strict File Ownership:** Do NOT edit or overwrite files owned by the other agent without prior coordination.
2. **Append-Only Logs:** In `logs.md`, never edit past entries. Append new entries with timestamps and agent attribution `(Codex)` or `(Antigravity)`.
3. **Tasks Updating:** Update `tasks.md` status only when a task's acceptance criteria are verified by tests.
4. **Wire Contracts:** `src/proto/ticket_booking.proto` is the shared contract. Changes to `.proto` must be coordinated and regenerated using `python scripts/generate_proto.py`.
5. **No Regressions:** Always run `pytest` inside `.venv` to verify all tests pass before concluding a step.

---

## 3. Current Status & Next Steps

- **Milestone 1 (Foundation, gRPC & LLM):** 100% Complete & Verified (26/26 tests passing in `.venv`).
  - Auth, Sessions & Token verification: Done
  - Single-node booking state machine & double-booking mutual exclusion: Done
  - Cancellation & idempotent replay: Done
  - Deterministic mock payment: Done
  - Independent gRPC LLM FAQ service with Ollama integration & CPU fallback: Done
  - Token-aware CLI client with transparent leader redirection: Done
  - Integration test suite: Done
- **Milestone 2 (Raft Consensus & Fault Tolerance):**
  - Codex to start `TASK-RAFT-1` through `TASK-RAFT-6` in `src/ticket_booking/raft/`.
  - Antigravity to build multi-node Docker Compose topology (`TASK-OPS-1`) and chaos / fault-injection harnesses (`TASK-FT-1` to `TASK-FT-3`).
