# UI verification against Docker

Status: **PASSED** (all checks below). Verbatim output of `src/scripts/verify_ui_docker.py`
(Streamlit/gRPC INFO and deprecation lines removed), run from the fresh clone with the stack
up (`OLLAMA_URL=http://host.docker.internal:11434/api/generate docker compose up -d`) at
commit `9d27d0a`, after rebuilding the image.

The script executes the real `src/ui/app.py` headlessly with `streamlit.testing.AppTest`; every
action reaches the cluster over gRPC (127.0.0.1:50051-50053). Topology changes use only
`docker compose stop|start` on `app-N` and `llm`. The containerised UI at
http://127.0.0.1:8501 was also opened in a browser and rendered the login page.

Not automatable here and checked by code inspection only: the loading spinners
(`st.spinner` around every RPC) and the `candidate` role (shown verbatim from
`CLUSTER_STATUS` when a node reports it; not captured in this run).

Two earlier runs of this script failed and led to fixes:
- run 1: the no-quorum retry after switching views returned `REQUEST_CONFLICT` - a real UI
  defect (see the completion report, section 10);
- run 2: the script re-used seats booked in run 1 (script bug; it now picks free seats).

# UI verification against Docker (2026-11-14 16:17:31 +0530)

## Auth and session
- PASS: logged-out landing page asks to log in
- PASS: invalid signup (weak password) rejected -- Signup failed: Password must be at least 8 characters. (status: WEAK_PASSWORD)
- PASS: signup succeeds and opens a session
- PASS: new user sees empty My Bookings


- PASS: session persists across views/reruns
- PASS: logout clears the session
- PASS: wrong password rejected -- Login failed: Invalid username or password. (status: AUTH_FAILED)
- PASS: login succeeds
- PASS: session token never rendered on the page

## Booking view
- PASS: show catalogue lists 8 shows -- The Raft Musical - Auditorium A - 2026-10-01T19:00:00Z
- PASS: seat map rendered as a linearizable read
- PASS: seat grid has 50 labelled seats
- PASS: selecting 6 seats is rejected (limit 5)
- PASS: server-authoritative quote shown (2 x 50.00 = 100.00) -- Server quote: 2 x 50.00 = **100.00** | No bookings yet. | Nodes: node-1 (127.0.0.1:50051), node-2 (127.0.0.1:50052), nod
- PASS: declining mock card -> PAYMENT_FAILED, no booking -- PAYMENT_FAILED: Card declined by mock payment issuer.
- PASS: approving mock card -> confirmation with booking ID -- Booking confirmed. Booking ID 75982a59-2467-5dee-8717-bac36cd64467. Request ID 2ccf683498734e21895c6bad7364be4a.
- PASS: My Bookings shows the booking with exactly one charge -- `75982a59` show-1 seats A31,A41 - CONFIRMED - charged 100.00 (1 charge)
- PASS: booked seats now labelled booked and disabled -- ['A31 - booked', 'A41 - booked']
- PASS: cancellation shows CANCELLED and refunded

## FAQ chat
- PASS: in-domain question answered and labelled LLM-generated --  via the CANCEL_SEAT request. | FAQ sources: | Nodes: node-1 (127.0.0.1:50051), node-2 (127.0.0.1:50052), node-3 (127.0.0.1:50053) | Signed in as **uid732fdbd**
- PASS: supporting context with correlation ID shown
- PASS: out-of-domain question refused
  `docker compose stop llm` -> exit 0
- PASS: LLM outage -> labelled verbatim fallback
- PASS: booking still works with the LLM node down
  `docker compose start llm` -> exit 0

## Cluster Observatory


- PASS: three nodes reachable, one leader + two followers -- ['follower', 'follower', 'leader']
- PASS: table has term/leader/log/commit/applied/lag/matchIndex/digests -- ['address', 'applied', 'commit', 'commit lag', 'leader', 'leader matchIndex', 'log digest', 'log length', 'node', 'reachability', 'role', 'rpc ms', 'state digest', 'term']
- PASS: leader banner names the leader
  `docker compose stop app-2` -> exit 0


- PASS: stopped node shown UNREACHABLE (not follower) -- UNREACHABLE/UNREACHABLE
  `docker compose start app-2` -> exit 0

## Demo Lab




- PASS: seat race: all invariant checks PASS -- PASS: at most one OK booking for the seat (1 OK) | PASS: every other request rejected as ALREADY_BOOKED (ALREADY_BOOKED) | PASS: linearizable read shows seat BOOKED (OK/BOOKED) | PASS: winning booking


- PASS: same-key retry: one booking, charged once (PASS)


- PASS: guided failover shows the stop command for the leader
  `docker compose stop app-2` -> exit 0


- PASS: measured failover to a new leader -- New leader node-1 in term 29; measured failover 3418 ms.
- PASS: restart command shown for the old leader
  `docker compose start app-2` -> exit 0


- PASS: old leader caught up with identical digests
  `docker compose stop app-1 app-3` -> exit 0


- PASS: no quorum: booking not committed -- Not committed (UNAVAILABLE). Restart a node and press the button again: request ID nq-3c55f0dcb1c0 for show-6/A1 is reused.


- PASS: Observatory: two UNREACHABLE nodes during quorum loss -- ['UNREACHABLE', 'leader', 'UNREACHABLE']
- PASS: Booking view: stale seat map banner without quorum -- STALE: showing the last loaded show list. | STALE: live read failed (NO_QUORUM). Showing the seat map last confirmed at 16:17:57; booking may fail. | LLM unavailable - fallback below is verbatim FAQ t


  `docker compose start app-1` -> exit 0


- PASS: same request ID after quorum returns: one booking, one charge (PASS) -- after 1 press(es)
  `docker compose start app-3` -> exit 0
- PASS: Markdown and JSON summary download buttons present -- ['Download summary (Markdown)', 'Download summary (JSON)']
- PASS: summary contains timeline and PASS results, no FAIL
- PASS: timeline has correlated request IDs

## Privacy
- PASS: another user's bookings not visible
- PASS: no password, token or full card number rendered

## Result: ALL CHECKS PASSED


## Final quality-pass run (used cluster)

Same script against the main `project` stack after the quality pass (image from commit
`c55199e`, volumes with ~8,800 entries); the script now falls back to any show with a free seat.

# UI verification against Docker (2026-11-14 17:07:20 +0530)

## Auth and session
- PASS: logged-out landing page asks to log in
- PASS: invalid signup (weak password) rejected -- Signup failed: Password must be at least 8 characters. (status: WEAK_PASSWORD)
- PASS: signup succeeds and opens a session
- PASS: new user sees empty My Bookings

- PASS: session persists across views/reruns
- PASS: logout clears the session
- PASS: wrong password rejected -- Login failed: Invalid username or password. (status: AUTH_FAILED)
- PASS: login succeeds
- PASS: session token never rendered on the page

## Booking view
- PASS: show catalogue lists 8 shows -- The Raft Musical - Auditorium A - 2026-10-01T19:00:00Z
- PASS: seat map rendered as a linearizable read
- PASS: seat grid has 50 labelled seats
- PASS: selecting 6 seats is rejected (limit 5)
- PASS: server-authoritative quote shown (2 x 50.00 = 100.00) -- Server quote: 2 x 50.00 = **100.00** | No bookings yet. | Nodes: node-1 (127.0.0.1:50051), node-2 (127.0.0.1:50052), nod
- PASS: declining mock card -> PAYMENT_FAILED, no booking -- PAYMENT_FAILED: Card declined by mock payment issuer.
- PASS: approving mock card -> confirmation with booking ID -- Booking confirmed. Booking ID b86580a7-58e9-5ef9-b40d-2023e51ae5a7. Request ID c38117798e694a4f93b0c86ad738bc8c.
- PASS: My Bookings shows the booking with exactly one charge -- `b86580a7` show-1 seats A21,A31 - CONFIRMED - charged 100.00 (1 charge)
- PASS: booked seats now labelled booked and disabled -- ['A21 - booked', 'A31 - booked']
- PASS: cancellation shows CANCELLED and refunded

## FAQ chat
- PASS: in-domain question answered and labelled LLM-generated --  via the CANCEL_SEAT request. | FAQ sources: | Nodes: node-1 (127.0.0.1:50051), node-2 (127.0.0.1:50052), node-3 (127.0.0.1:50053) | Signed in as **ui28942e8e**
- PASS: supporting context with correlation ID shown
- PASS: out-of-domain question refused
  `docker compose stop llm` -> exit 0
- PASS: LLM outage -> labelled verbatim fallback
- PASS: booking still works with the LLM node down
  `docker compose start llm` -> exit 0

## Cluster Observatory

- PASS: three nodes reachable, one leader + two followers -- ['follower', 'follower', 'leader']
- PASS: table has term/leader/log/commit/applied/lag/matchIndex/digests -- ['address', 'applied', 'commit', 'commit lag', 'leader', 'leader matchIndex', 'log digest', 'log length', 'node', 'reachability', 'role', 'rpc ms', 'state digest', 'term']
- PASS: leader banner names the leader
  `docker compose stop app-1` -> exit 0

- PASS: stopped node shown UNREACHABLE (not follower) -- UNREACHABLE/UNREACHABLE
  `docker compose start app-1` -> exit 0

## Demo Lab

- PASS: seat race: all invariant checks PASS -- PASS: at most one OK booking for the seat (1 OK) | PASS: every other request rejected as ALREADY_BOOKED (ALREADY_BOOKED) | PASS: linearizable read shows seat BOOKED (OK/BOOKED) | PASS: winning booking

- PASS: same-key retry: one booking, charged once (PASS)

- PASS: guided failover shows the stop command for the leader
  `docker compose stop app-1` -> exit 0

- PASS: measured failover to a new leader -- New leader node-3 in term 34; measured failover 3439 ms.
- PASS: restart command shown for the old leader
  `docker compose start app-1` -> exit 0

- PASS: old leader caught up with identical digests
  `docker compose stop app-1 app-2` -> exit 0

- PASS: no quorum: booking not committed -- Not committed (UNAVAILABLE). Restart a node and press the button again: request ID nq-27e555295815 for show-1/A10 is reused.

- PASS: Observatory: two UNREACHABLE nodes during quorum loss -- ['UNREACHABLE', 'UNREACHABLE', 'leader']
- PASS: Booking view: stale seat map banner without quorum -- STALE: showing the last loaded show list. | STALE: live read failed (NO_QUORUM). Showing the seat map last confirmed at 17:07:51; booking may fail. | LLM unavailable - fallback below is verbatim FAQ t

  `docker compose start app-1` -> exit 0

- PASS: same request ID after quorum returns: one booking, one charge (PASS) -- after 1 press(es)
  `docker compose start app-2` -> exit 0
- PASS: Markdown and JSON summary download buttons present -- ['Download summary (Markdown)', 'Download summary (JSON)']
- PASS: summary contains timeline and PASS results, no FAIL
- PASS: timeline has correlated request IDs

## Privacy
- PASS: another user's bookings not visible
- PASS: no password, token or full card number rendered

## Result: ALL CHECKS PASSED
