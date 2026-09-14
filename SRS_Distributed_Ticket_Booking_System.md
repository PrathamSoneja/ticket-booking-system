# Software Requirements Specification
## Distributed Ticket Booking System — Architecture Option A
**Course:** CS G623 — Advanced Operating Systems, First Semester 2026–27
**Assignment:** Distributed Systems with gRPC Communication and Raft Consensus

| | |
|---|---|
| Version | 1.0 |
| Team name | *fill in* |
| Team members | *fill in (3 recommended)* |
| Date | *fill in* |

---

## 1. Introduction

### 1.1 Purpose
This document specifies the requirements for a distributed online ticket booking system built to satisfy the CS G623 project assignment. It defines the functional and non-functional requirements, the system architecture, external interfaces, data model, and the plan for delivering the project across Milestone 1 (gRPC + LLM foundation) and Milestone 2 (Raft consensus + fault tolerance).

### 1.2 Scope
The system allows users to browse shows, book and cancel seats, and get FAQ assistance from a chatbot, while application state (seat availability, bookings) is kept strongly consistent across multiple application server replicas using the Raft consensus protocol. Payment processing is a mock/stub service — no real payment gateway integration is required.

Out of scope: real payment processing, production-grade authentication/security, horizontal auto-scaling, multi-datacenter deployment.

### 1.3 Definitions and Acronyms
- **HLD / LLD** — High-Level / Low-Level Design
- **RPC** — Remote Procedure Call
- **Raft** — the consensus algorithm from Ongaro & Ousterhout, *"In Search of an Understandable Consensus Algorithm"*
- **Leader / Follower / Candidate** — Raft server roles
- **Log entry** — a single replicated command in the Raft log
- **State machine** — the local data structure (seat map) each node builds by applying committed log entries in order
- **FAQ / RAG** — Frequently Asked Questions / Retrieval-Augmented Generation
- **SRS** — Software Requirements Specification

### 1.4 References
- Assignment: *Advanced Operating Systems (CS G623), Project Assignment — Distributed Systems with gRPC Communication and Raft Consensus*
- Ongaro, D., Ousterhout, J. — *In Search of an Understandable Consensus Algorithm (Extended Version)*
- gRPC documentation: https://grpc.io/docs/
- Protocol Buffers documentation: https://protobuf.dev/
- Ollama documentation: https://ollama.com

---

## 2. Overall Description

### 2.1 Product Perspective
The system is a set of independent processes communicating exclusively over gRPC:
- One or more **client** processes simulating concurrent users.
- Three **application server** replicas, one acting as Raft **leader**, two as **followers**. All business logic (booking, cancellation, availability lookups) is served here.
- One independent **LLM server** providing a domain-specific FAQ chatbot, reachable only from the application servers.

### 2.2 Product Functions (summary)
- User authentication (login/logout, session tokens)
- Browse shows and seat availability
- Book a seat with overbooking prevention
- Cancel a booking
- Mock payment confirmation
- FAQ chatbot for booking/cancellation/policy questions
- Strongly consistent replication of booking state via Raft
- Automatic leader election and recovery on node failure

### 2.3 User Classes
| User class | Description |
|---|---|
| End user / customer | Books, views, and cancels seat reservations; queries the FAQ chatbot |
| System administrator (optional/stretch) | Can view booking/seat analytics, force-inspect Raft cluster state |
| Test harness / chaos client | Automated script that kills nodes, floods concurrent bookings, and checks invariants |

### 2.4 Assumptions and Dependencies
- Implementation language is Python across all services (per assignment mandate).
- All inter-service calls use gRPC; no REST/HTTP between internal services.
- The team has access to a laptop/desktop for development and Docker for local multi-node simulation, plus an AWS account with free-tier eligibility and promotional credits (6 months) for the final deployment/demo.
- The LLM server runs a small, CPU-only, locally hosted model — no paid third-party LLM API is required or expected.

### 2.5 Constraints
- Must expose the RPC function signatures given in the assignment (`login`, `logout`, `post`, `get`, `processBusinessRequest`, `getLLMAnswer`, `requestVote`, `appendEntries`, and their reply types), with only minor modification allowed.
- Must run recognizably as 4–5 nodes for the demo (LLM server, leader, 2 followers, client(s)).
- Milestone 1 hard deadline: **Sept 30, 2026**. Milestone 2 hard deadline: **Nov 20, 2026**.

---

## 3. System Architecture

### 3.1 Node topology

| Node | Role | Responsibilities |
|---|---|---|
| Node 1 | LLM server | Runs the domain-specific FAQ model; stateless; independent of Raft |
| Node 2 | App server — Raft leader (at any given time) | Accepts writes, appends to Raft log, applies committed entries, calls LLM server for chat queries |
| Node 3 | App server — Raft follower | Replicates log entries, participates in elections |
| Node 4 | App server — Raft follower | Same as Node 3; provides quorum redundancy |
| Node 5 (×N) | Client(s) | Simulates concurrent end users: login, browse, book, cancel, chat |

Only one of Nodes 2–4 is leader at any time; role is dynamic and determined by Raft elections, not fixed at startup.

### 3.2 Key architectural decisions (see accompanying discussion for full trade-off analysis)

| Decision | Choice made | Rationale |
|---|---|---|
| Client-to-leader routing | Client tries any node; non-leader replies with leader's address; client retries | Matches how real Raft-based systems behave; simple, observable in demo |
| What Raft replicates | The command log (state machine replication), not full snapshots | Matches `appendEntries(entries)` signature; standard Raft pattern |
| Where business validation happens | Only on the leader, before appending | Keeps followers simple; avoids duplicated validation logic |
| Persistence | Raft log + `currentTerm`/`votedFor` persisted to disk; applied state kept in memory | Correctness across restarts without full DB engineering effort |
| Leader-side concurrency | Single-threaded apply loop / request queue on the leader | Avoids intra-leader races without extra locking machinery |
| LLM approach | Small local instruct model (e.g. Llama 3.2 1B/3B or Phi-3-mini) via Ollama, fed FAQ context by keyword lookup (no vector DB) | Satisfies "domain-specific, CPU-optimized, context-aware, separate server" without disproportionate effort |
| Deployment | Docker Compose for development; AWS EC2 (free tier + credits) for final demo, with a larger instance for the LLM node | Fast iteration during build, genuine multi-machine distribution for the graded demo |
| Client interface | CLI simulating multiple concurrent users (required); optional minimal web dashboard if time allows | Matches assignment's Node 5 description directly; UI polish is secondary |

### 3.3 Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Mandated by assignment |
| RPC | grpcio, grpcio-tools, protobuf | Free, standard |
| Concurrency | asyncio or threading + a request queue on the leader | Either is fine; pick one and be consistent |
| Raft implementation | Hand-written (do not use an off-the-shelf Raft library) | Assignment specifies exact RPC signatures — a library would fight this; also the point of the exercise is to build Raft yourself |
| Persistence | Local append-only JSON-lines file per node for the Raft log; in-memory dict for applied state | Cheap correctness, no external DB dependency |
| LLM runtime | Ollama or llama.cpp running a small quantized instruct model | Free, CPU-only, runs on a laptop or a small EC2 instance |
| Client simulation | Python `multiprocessing`/`asyncio` tasks issuing concurrent gRPC calls | Needed to demonstrate race-condition prevention |
| Local multi-node testing | Docker Compose | Fast, reproducible, free |
| Demo deployment | AWS EC2 free-tier instances (t3.micro) for app/client nodes; a larger instance (t3.medium, paid from credits) for the LLM node | Genuine distributed demo within budget |
| Testing | pytest, plus custom fault-injection scripts (kill leader process, partition a node) | Required for M2 grading criterion on failure/recovery |
| Version control / collaboration | Git + GitHub (or GitLab) | Free, also gives you a natural place to track individual contributions for the reflection document |

---

## 4. Functional Requirements

Priority: **M**ust have, **S**hould have, **C**ould have. Milestone: **M1** or **M2**.

### 4.1 Authentication & Session Management
| ID | Requirement | Priority | Milestone | Acceptance criteria |
|---|---|---|---|---|
| FR-AUTH-1 | System shall provide `login(username, password)` returning `loginResponse(status, token)` | M | M1 | Valid credentials return a token; invalid credentials return a failure status and no token |
| FR-AUTH-2 | System shall provide `logout(token)` invalidating the session | M | M1 | Subsequent calls with the invalidated token are rejected |
| FR-AUTH-3 | All `post`/`get` calls shall require a valid token | M | M1 | Requests with missing/expired/invalid token return an auth-error status |

### 4.2 Show & Seat Browsing
| ID | Requirement | Priority | Milestone | Acceptance criteria |
|---|---|---|---|---|
| FR-BROWSE-1 | System shall provide `get(token, "SHOWS")` returning the list of available shows | M | M1 | Returns show IDs and names |
| FR-BROWSE-2 | System shall provide `get(token, "SEATS", {showId})` returning per-seat status | M | M1 | Returns seat IDs with status (AVAILABLE/BOOKED) reflecting the latest committed state |

### 4.3 Booking & Concurrency Control
| ID | Requirement | Priority | Milestone | Acceptance criteria |
|---|---|---|---|---|
| FR-BOOK-1 | System shall provide `post(token, "BOOK_SEAT", {showId, seatId})` via `processBusinessRequest` on the leader | M | M1 (basic), M2 (Raft-backed) | A successful booking transitions the seat from AVAILABLE to BOOKED |
| FR-BOOK-2 | System shall reject a booking if the seat is already booked | M | M1 | Concurrent booking attempts on the same seat: exactly one succeeds, others receive a clear "already booked" status |
| FR-BOOK-3 | System shall prevent overbooking under concurrent load across the whole cluster, not just on one node | M | M2 | Load test: N clients hammer the same seat concurrently; exactly one booking is committed, verified against the replicated log on all nodes |
| FR-BOOK-4 | System shall provide `post(token, "CANCEL_SEAT", {bookingId})` to release a seat | M | M1/M2 | Seat returns to AVAILABLE and is bookable again |
| FR-BOOK-5 | System shall process mock payment confirmation as part of the booking flow | S | M1 | A stubbed payment step returns success/failure without contacting a real gateway |

### 4.4 Raft Consensus & Replication
| ID | Requirement | Priority | Milestone | Acceptance criteria |
|---|---|---|---|---|
| FR-RAFT-1 | Nodes shall implement `requestVote`/`requestVoteReply` for leader election | M | M2 | A cluster of 3 app-server nodes elects exactly one leader on startup |
| FR-RAFT-2 | Nodes shall implement `appendEntries`/`appendEntriesReply` for log replication and heartbeats | M | M2 | Followers' logs match the leader's log after a batch of commands |
| FR-RAFT-3 | Leader shall only report a write as successful after it is replicated to a majority of nodes | M | M2 | Kill a follower mid-write; booking still commits and is visible on the surviving majority |
| FR-RAFT-4 | System shall re-elect a new leader within a bounded time if the current leader fails | M | M2 | Kill the leader process; a new leader is elected within a few election-timeout intervals; no committed booking is lost |
| FR-RAFT-5 | A recovering/rejoining node shall catch up to the current committed log state | S | M2 | Restart a killed follower; it converges to the same seat-map state as the leader |

### 4.5 LLM Chatbot Assistant
| ID | Requirement | Priority | Milestone | Acceptance criteria |
|---|---|---|---|---|
| FR-LLM-1 | System shall provide `getLLMAnswer(requestId, query, context)` served by an independent LLM server | M | M1 | LLM server runs as a separate process/node, reachable only via gRPC |
| FR-LLM-2 | Chatbot shall answer domain FAQs (cancellation policy, seat availability, how to book) using ticket-booking-specific context | M | M1 | For a fixed FAQ test set, answers are on-topic and reference the correct policy |
| FR-LLM-3 | Chatbot responses shall be grounded in a small domain knowledge base (RAG-style context injection), not answered purely from the model's general knowledge | S | M1 | Swapping the FAQ doc content visibly changes chatbot answers |
| FR-LLM-4 | LLM server shall run CPU-only, without requiring a GPU or paid API | M | M1 | Demonstrated running on a laptop CPU or a free/low-cost cloud instance |

### 4.6 Fault Tolerance
| ID | Requirement | Priority | Milestone | Acceptance criteria |
|---|---|---|---|---|
| FR-FT-1 | Killing a follower node shall not interrupt booking availability | M | M2 | Bookings continue to succeed with 2 of 3 nodes alive |
| FR-FT-2 | Killing the leader node shall trigger automatic recovery via re-election | M | M2 | Demonstrated live in the recorded demo, per the assignment's own grading note |
| FR-FT-3 | System shall not double-book or lose a committed booking across any single-node failure | M | M2 | Verified via automated fault-injection test asserting log/state consistency post-recovery |

### 4.7 Administration / Analytics (stretch)
| ID | Requirement | Priority | Milestone | Acceptance criteria |
|---|---|---|---|---|
| FR-ADMIN-1 | Provide a way to inspect current Raft role and log length per node | C | M2 | A `get(token, "CLUSTER_STATUS")` call or a debug CLI shows role/term/log length |
| FR-ADMIN-2 | Provide summarized booking analytics (bookings per show, cancellation rate) | C | M2 | A report is generated on demand; can optionally be LLM-summarized |

---

## 5. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Consistency | All committed bookings must be strongly consistent across replicas — no two nodes may disagree on the status of a seat once a write is committed. |
| Availability | The system must remain able to serve writes as long as a majority (2 of 3) of app-server nodes are reachable. |
| Performance | A booking request should commit within a small, bounded number of seconds under normal conditions (no strict number mandated by the assignment — pick and justify a target, e.g. under 2 seconds on localhost/Docker). |
| Fault recovery time | Leader re-election should complete within a few election-timeout intervals (e.g. under 5 seconds for a demo-scale cluster). |
| Security | Passwords should not be stored or transmitted in plaintext logs; tokens should expire after a defined session period. Production-grade security (TLS, hashing algorithms, etc.) is a "should," not a hard requirement, for this academic project. |
| Scalability | The design should not hard-code the number of follower nodes; adding a 4th/5th app-server replica should require configuration changes only, not code changes. |
| Usability | The CLI client should give clear, human-readable status messages, especially on redirect-to-leader and booking-rejected paths. |
| Maintainability | Proto definitions, Raft logic, and business logic should live in separate modules; no RPC handler should directly manipulate Raft internals outside the defined propose/apply interface. |
| Observability | Every node should log its role, term, and key events (election started, leader elected, entry committed) with timestamps, to support debugging and the demo video narration. |

---

## 6. External Interface Requirements (gRPC)

### 6.1 Client-facing service (sketch)
```protobuf
service ClientService {
  rpc Login (LoginRequest) returns (LoginResponse);
  rpc Logout (LogoutRequest) returns (StatusResponse);
  rpc Post (PostRequest) returns (StatusResponse);
  rpc Get (GetRequest) returns (GetResponse);
}

message LoginRequest  { string username = 1; string password = 2; }
message LoginResponse { string status = 1; string token = 2; }
message PostRequest   { string token = 1; string type = 2; bytes data = 3; }
message GetRequest    { string token = 1; string type = 2; bytes params = 3; }
message GetResponse   { string status = 1; repeated Item items = 2; }
message Item          { string id = 1; bytes data = 2; }
message StatusResponse{ string status = 1; string message = 2; string redirect_to = 3; }
```

### 6.2 Raft service (sketch)
```protobuf
service RaftService {
  rpc RequestVote (RequestVoteArgs) returns (RequestVoteReply);
  rpc AppendEntries (AppendEntriesArgs) returns (AppendEntriesReply);
}

message RequestVoteArgs   { string from=1; string to=2; int64 term=3; int64 lastLogIndex=4; int64 lastLogTerm=5; }
message RequestVoteReply  { string from=1; string to=2; int64 term=3; bool voteGranted=4; }
message AppendEntriesArgs { string from=1; string to=2; int64 term=3; int64 prevIndex=4; int64 prevTerm=5; int64 commitIndex=6; repeated LogEntry entries=7; }
message AppendEntriesReply{ string from=1; string to=2; int64 term=3; bool entryAppended=4; int64 matchIndex=5; }
message LogEntry          { int64 term=1; int64 index=2; string commandType=3; bytes payload=4; string requestId=5; }
```

### 6.3 LLM service (sketch)
```protobuf
service LLMService {
  rpc GetLLMAnswer (LLMRequest) returns (LLMResponse);
}
message LLMRequest  { string requestId=1; string query=2; string context=3; }
message LLMResponse { string requestId=1; string answer=2; }
```

---

## 7. Data Requirements

| Entity | Fields |
|---|---|
| User | `userId`, `username`, `passwordHash`, `role` |
| Session | `token`, `userId`, `expiresAt` |
| Show | `showId`, `name`, `venue`, `startTime` |
| Seat | `seatId`, `showId`, `status` (AVAILABLE / BOOKED), `bookedBy` (nullable) |
| Booking | `bookingId`, `userId`, `showId`, `seatId`, `createdAt`, `status` (CONFIRMED / CANCELLED) |
| RaftLogEntry | `term`, `index`, `commandType`, `payload`, `requestId` |
| RaftNodeState | `currentTerm`, `votedFor`, `role`, `commitIndex`, `lastApplied` |

Note: `requestId` on every write command enables idempotent apply — if a client retries a timed-out request, the state machine can detect the duplicate and avoid double-processing it.

---

## 8. Milestone Deliverables Mapping

| Milestone | Deadline | Hard deadline | Key deliverables (from assignment) | Mapped requirements |
|---|---|---|---|---|
| M1 — Foundation, gRPC & LLM | Sept 28, 2026 | Sept 30, 2026 | gRPC service definitions; client-server communication; auth & sessions; LLM integration with sample queries; project structure & setup | FR-AUTH-*, FR-BROWSE-*, FR-BOOK-1/2/5 (single-node), FR-LLM-1/2/3/4 |
| M2 — Raft Consensus | Nov 18, 2026 | Nov 20, 2026 | Leader election; log replication across nodes; failure detection & recovery (demo consistency after leader kill) | FR-RAFT-*, FR-BOOK-3, FR-FT-* |

---

## 9. Testing Plan & Acceptance Criteria

### 9.1 Unit tests
- Auth: valid/invalid login, token expiry.
- Booking logic: double-booking rejection, cancellation, idempotent replay of the same `requestId`.
- Raft: log append, vote-granting rules, term comparison logic — tested against an in-process mock of peer nodes (no real network needed for these).

### 9.2 Integration tests
- Full 3-node cluster (Docker Compose) elects a leader on startup.
- A batch of concurrent booking requests against the same seat: exactly one commits.
- LLM server answers a fixed set of FAQ prompts correctly.

### 9.3 Fault-injection / chaos tests (required for M2 grading)
- Kill the leader process mid-load; verify a new leader is elected and no committed booking is lost or duplicated.
- Kill a follower process; verify writes continue to succeed via the remaining majority.
- Restart a previously-killed follower; verify it catches up to the current committed state.
- Partition a single follower (drop its network) and confirm the leader still commits via the remaining majority, while the partitioned node cannot.

### 9.4 Demo script (for the required 5–10 minute video)
1. Show 3 app-server nodes starting, one becoming leader (log output).
2. Show two clients concurrently trying to book the same seat — one succeeds, one is rejected.
3. Ask the chatbot 2–3 FAQ questions, show it querying the separate LLM node.
4. Kill the leader process live; show re-election in logs; show a new booking succeeding through the new leader.
5. Restart the killed node; show it rejoining and catching up.

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Raft implemented incorrectly (split votes, log divergence) | High — breaks M2 grading criterion directly | Start Raft early; test with the fault-injection scripts continuously, not just before the deadline |
| LLM node too heavy for the chosen AWS instance | Medium | Use a 1B–3B quantized model; test on the smallest instance type early; keep a Docker-only fallback for the demo if AWS proves too tight |
| Team coordination / uneven contribution | Medium | Use Git commit history + the required individual reflection document to track and evidence contributions |
| Scope creep on the LLM or UI side eating Raft time | High | Timebox the LLM/UI work in M1; Raft correctness is worth more of the grade than chatbot polish |
| AWS free-tier/credit limits exhausted before final demo | Low–Medium | Keep AWS usage mostly for final testing and the recorded demo, not for day-to-day development (use Docker locally instead) |

---

## 11. Team Roles & Responsibilities (template for 3 members)

| Role | Suggested owner | Responsibilities |
|---|---|---|
| Raft & consensus lead | Member A | `raft_node.py`, election/heartbeat timers, log persistence, fault-injection tests |
| Application & data lead | Member B | `booking_service.py`, `state_machine.py`, `auth_service.py`, proto definitions, client simulator |
| LLM & infra lead | Member C | LLM server, FAQ context pipeline, Docker Compose setup, AWS deployment, demo recording |

All members should contribute to code review and the integration/fault-injection tests — the reflection document should reflect real, evidenced contribution, not a rigid split.

---

## 12. Submission Checklist (mapped to assignment requirements)

- [ ] Code, zipped, with a README covering setup, deployment, and usage instructions
- [ ] Drive link + 5–10 minute demo video showing the scenarios in Section 9.4
- [ ] Documentation (this SRS, updated to reflect what was actually built)
- [ ] Individual reflection document — each member's contributions and learnings
- [ ] Plagiarism self-check — ensure all code is original or properly attributed

---

## Appendix A — Sample FAQ dataset for the chatbot (starter set)

- "How do I cancel a booking?" → refund/cancellation policy text
- "What seats are available for [show]?" → instructs the bot to call `get(token, "SEATS", {showId})` context, or answers generically about how availability works
- "How far in advance can I book?" → booking window policy
- "Can I change my seat after booking?" → seat-change policy (likely: cancel and rebook)
- "Is payment refundable on cancellation?" → refund policy

## Appendix B — Reference
Ongaro, D. and Ousterhout, J., *In Search of an Understandable Consensus Algorithm (Extended Version)*, as cited in the assignment brief. Consult this paper directly for the full Raft specification (leader election, log replication, safety proofs) — this SRS deliberately keeps the algorithmic detail to a summary level and defers to the paper for correctness rules.
