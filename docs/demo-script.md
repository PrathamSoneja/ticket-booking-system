# Demo video script (target 8-9 minutes, limit 10)

Setup before recording (not shown): Docker Desktop running, host Ollama running with
`llama3.2:1b`, a clean cluster, and the README open.

```bash
docker compose down -v
docker compose build
OLLAMA_URL=http://host.docker.internal:11434/api/generate docker compose up -d
docker compose ps
```

Windows: one terminal at the repository root, one browser tab at http://127.0.0.1:8501.
Log in in the UI as `alice` / `wonderland` (seeded demo account).

| time | show | say |
|---|---|---|
| 0:00-0:45 | README section 11 architecture diagram | Option A, distributed ticket booking. Five nodes: LLM server, three Raft application servers, UI/client. All service-to-service calls are gRPC; the LLM server uses Ollama locally as its model runtime over HTTP. |
| 0:45-1:30 | `docker compose ps` (all healthy); UI Cluster Observatory | One leader, two followers, same term. Point at log length, commit/applied index, and identical log and state digests. Unreachable nodes are shown as UNREACHABLE, not as followers. |
| 1:30-2:45 | UI Booking view | Pick a show; the seat map caption says the read was confirmed by a Raft majority. Select two seats, show the server quote, pay with the approving mock card, show confirmation and request ID. Book again with the declining mock card: PAYMENT_FAILED, no booking. My Bookings shows one charge per booking; cancel one and show the refund. |
| 2:45-3:30 | Booking view FAQ | Ask "How do I cancel a booking?" (labelled LLM-generated, expand supporting context and correlation ID). Ask "Which seats are available?" (live seat map in the context). Ask "What is the capital of France?" (OUT_OF_DOMAIN, the LLM is not called). |
| 3:30-4:30 | Demo Lab steps 1-2 | Seat race with 8 clients: exactly one OK, the rest ALREADY_BOOKED, all checks PASS. Same-key retry with 5 copies: one booking ID, charged once. |
| 4:30-6:15 | Demo Lab step 3 + terminal | Note the leader. Click "Watch failover", then run the shown `docker compose stop app-N`. Show the measured failover time and the new term. Go to Booking: the seat booked earlier is still BOOKED (read-after-write after leader kill); book another seat. Run `docker compose start app-N`, click "Check old-leader catch-up": digests match. Observatory shows the old leader as follower. |
| 6:15-7:30 | Demo Lab step 4 + terminal | `docker compose stop` two app services. "Attempt booking now": not acknowledged; Observatory shows two UNREACHABLE nodes and the seat map read fails with NO_QUORUM. Start one service, press the button again (same request ID): OK, one booking, one charge (PASS). Start the third service. |
| 7:30-8:15 | terminal: `docker compose stop llm`; UI FAQ | Ask an FAQ question: LLM_UNAVAILABLE with verbatim FAQ text labelled "not AI-generated". Book a seat: still works. `docker compose start llm`. |
| 8:15-8:45 | Demo Lab timeline; download summary | Timestamped, correlated events; download the Markdown summary. |
| 8:45-9:30 | docs/stress-test-report.md, terminal running `python -m pytest -q` result | Stress run: 16-way race, 10 duplicate request IDs, 12 workers for 30 s with a leader kill; all invariants passed, numbers in the report. Test suite count from the completion report. |

Keep claims to what the screen shows. If a step fails on camera, say what happened instead
of cutting around it.
