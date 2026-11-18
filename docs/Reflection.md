# Reflection: Individual Contributions and Learnings

Advanced Operating Systems (CS G623), BITS Pilani, 2026-27
Project: Distributed Ticket Booking System with gRPC and Raft (Option A)
Team: Pratham Soneja, Rathul Krishnan, Mitali Narayan

All three members contributed equally across the whole project. Each member owned one area end
to end and reviewed, tested and debugged the others' work.

---

## Pratham Soneja

**Contributions**
- Seat-allocation state machine: per-seat locking, atomic multi-seat bookings, request-ID
  idempotency.
- Raft core: leader election, log replication, majority commit and the linearizable read
  barrier.
- gRPC client library and command-line client, including leader redirection and retries.
- Raft correctness tests, including RPC fault injection and election-churn tests.

**Learnings**
- A single lock is easy; several locks need a global order. Sorting seat keys before locking
  removed the possibility of deadlock in multi-seat bookings.
- Raft's safety comes from small rules working together: one vote per term, the log
  up-to-date check, and committing only entries from the current term.
- Reads are as hard as writes. A leader that has been cut off still believes it is leader;
  confirming leadership with a majority before answering (ReadIndex) was needed to avoid stale
  seat maps.
- Fault injection (delayed, dropped, duplicated and reordered RPCs) found problems that normal
  tests never exercised.

**Challenges**
- Designing tests that stay meaningful under random timing: tests check invariants (one leader
  per term, no overbooking, identical logs) rather than exact timings.

---

## Rathul Krishnan

**Contributions**
- gRPC application server: request routing, follower forwarding and session handling across
  nodes.
- Raft persistence and recovery: SQLite storage of term, vote and log, replay on restart,
  follower catch-up.
- Docker Compose deployment: one image, health checks, named volumes, 5-node topology.
- Integration tests, the stress-testing tool and the Docker verification workflow.

**Learnings**
- Durability has to come before acknowledgement: term, vote and log entries are written to
  disk before a node replies, otherwise a restarted node could vote twice or forget a commit.
- Keeping in-memory state as a cache that can always be rebuilt from the log makes crash
  recovery simple: replay the committed entries and let the leader send the rest.
- Distributed systems need retries, and retries need request IDs; routing a write through a
  redirect or a failover must not create a second booking.
- Health checks must test something real (a gRPC call that the node answers) rather than only
  "the process is running".

**Challenges**
- Making the Docker topology reproducible from a fresh clone, including network failures
  during image builds and keeping data across `docker compose down`.

---

## Mitali Narayan

**Contributions**
- Separate LLM server: gRPC `LLMService`, Ollama integration, FAQ retrieval with NLTK, domain
  check and safe fallback.
- SQLite catalogue and seed data for shows and FAQs; mock payment, pricing and refund
  handling.
- Streamlit web UI: Booking view, Cluster Observatory and Demo Lab.
- Raft consensus proof novelty: commit receipt returned by the leader, booking-lifecycle panel
  in the UI, and the end-to-end demo script `m2_demo.py`.
- LLM, payment and UI tests, and the UI verification workflow.

**Learnings**
- A small local model becomes useful and domain-specific when it is given the right context:
  retrieving FAQ text and the live seat map mattered more than the model size.
- The LLM must never be on the critical path: bookings keep working when it is down, and its
  output is treated as text only, never as a command.
- Payments in a replicated system must be deterministic: the leader decides, and the decision
  travels in the log so every replica records the same charge exactly once.
- A UI that only uses the public gRPC API cannot bypass consensus, and showing the cluster's
  state (roles, terms, log indices) makes the distributed behaviour understandable.

**Challenges**
- Showing consensus to a user in a way that is accurate: every value displayed comes from a
  real RPC response, and an unreachable node is never shown as a follower.

---

## What we learned as a team

- Agreeing on interfaces first (one `.proto` file) let three people work in parallel.
- Every important claim (no overbooking, no lost booking, one charge per booking) was turned
  into an automated check and verified under failures, not only described.
- The hardest problems were not in the happy path but in timeouts, retries and crashes.
