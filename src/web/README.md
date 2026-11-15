# The Night Programme

A custom web interface for the ticket-booking cluster: programme, seat booking, tickets, FAQ,
Cluster Observatory and Demo Lab. It sits beside the Streamlit UI (`ui/app.py`), which is
untouched and remains the fallback.

```
browser (web/static)  --HTTP/JSON, cookie + CSRF-->  web/gateway.py  --gRPC-->  node-1..3 (50051-50053)
                                                     reuses ticket_booking.client.Client          |
                                                     and ui/cluster.py                             +--> FAQ service (50055) --> Ollama (11434)
```

## Run (Windows / PowerShell)

Everything runs from `src` with the existing virtual environment. No new packages, no global
installs, no Docker, no Node.js.

1. Start the backend exactly as before (root README, section 5): the FAQ service and the three
   Raft nodes, each in its own terminal. Ollama is only needed for LLM-generated FAQ answers.

   ```powershell
   cd src
   .\.venv\Scripts\python.exe -m ticket_booking.llm_service
   ```

   ```powershell
   .\.venv\Scripts\python.exe -m ticket_booking.server --node-id node-1 --peers node-1=127.0.0.1:50051,node-2=127.0.0.1:50052,node-3=127.0.0.1:50053 --data-dir data\node-1
   ```

   (and the same line for `node-2` / `node-3` with their own `--data-dir`).

2. Start the gateway in one more terminal, also from `src`:

   ```powershell
   .\.venv\Scripts\python.exe -m web.gateway
   ```

3. Open **http://localhost:8600**. Demo accounts from this repository: `alice` / `wonderland`,
   `bob` / `builder` (the sign-in page lists them and labels them as demo credentials).

Fallback UI, unchanged: `.\.venv\Scripts\python.exe -m streamlit run ui/app.py` (port 8501).
The CLI (`python -m ticket_booking.client ...`) is unchanged too.

### Options

| flag / env | default | meaning |
|---|---|---|
| `--port` | `8600` | HTTP port |
| `--host` | `127.0.0.1` | bind address; anything else prints a warning (plain HTTP, demo tool) |
| `--nodes` / `TB_NODES` | `node-1=127.0.0.1:50051,node-2=127.0.0.1:50052,node-3=127.0.0.1:50053` | gRPC nodes |
| `--redirect-map` / `TB_REDIRECT_MAP` | empty | advertised → reachable leader addresses (same as the Streamlit UI) |
| `TB_COMPOSE_SERVICES` | empty | `node-1=app-1,...` makes the Demo Lab show `docker compose stop app-1` instead of a prose instruction |

With the Docker Compose stack, the nodes are published on 50051-50053, so the gateway runs on
the host with its defaults.

## What it does

- **Sign in / create account / sign out.** The session survives a page reload (it lives in the
  gateway). When the cluster stops accepting the token, the page says so and returns to sign-in.
- **Programme.** Real show list with a seat count per show (one linearizable `SEATS` read each),
  search, ordering, "only shows with free seats". Dates are shown exactly as seeded, in UTC;
  nothing is hidden or blocked because a date has passed.
- **Seats and checkout.** Real seat IDs, keyboard grid (arrows, Home/End, Space/Enter), a legend
  that does not rely on colour, five-seat limit, the server's `QUOTE` (no currency is invented),
  two clearly simulated payment outcomes, a typographic ticket after a confirmed booking.
- **My Tickets.** Real booking history, full copyable booking IDs, charges, refunds, cancellation
  behind an inline confirmation.
- **Help.** FAQ conversation. Each answer is labelled by what the backend returned: LLM-generated,
  verbatim FAQ fallback, out of scope, or failed (with retry). Sources and correlation ID expand.
- **Cluster Observatory.** One `CLUSTER_STATUS` RPC per node: reachability, role, term, leader,
  log/commit/applied indexes, lag, matchIndex, digests, RPC time, sample timestamp; consistency
  groups, sampled lag, booking analytics. Unreachable nodes show blanks, never remembered values.
- **Demo Lab.** Seat race, same-request retry, guided leader failover and catch-up, no-quorum
  workflow, timeline, Markdown/JSON export. Stopping or starting a node is always an instruction
  you run yourself; the gateway imports no process, shell or Docker API (enforced by a test).

## Design decisions worth knowing

- **Idempotency.** For a booking, the gateway keeps one request ID per unresolved
  (show, seats, payment choice) in the user's session. An unanswered or retryable outcome
  (`UNAVAILABLE`, `NO_QUORUM`, `NO_LEADER`, `NOT_LEADER`, `REDIRECT_FAILED`, gRPC error) keeps it,
  so "Retry same request" cannot double-charge; a final outcome or a different selection gets a
  new ID. Cancellation works the same way per booking ID. The pending request survives a reload.
- **Sessions.** The gRPC token is stored only in the gateway, keyed by an `HttpOnly`,
  `SameSite=Strict` cookie whose value is rotated on sign-in, sign-out and expiry. Every POST needs
  the per-session CSRF token (`X-CSRF-Token`) and a same-origin `Origin`. Requests with a foreign
  `Host` header are refused. Each request builds its own gRPC client; nothing mutable is shared
  between users. Passwords and tokens are never logged or placed in browser storage.
- **Evidence-based lab results.** In the no-quorum scenario a refusal counts as a pass only if the
  gateway also observed fewer than a majority of nodes answering; with a majority present it is
  recorded as INCONCLUSIVE, and an unexpected final status is a FAIL. (The Streamlit version
  counts any non-OK status as a pass.)
- **FAQ never blocks booking.** Each question runs on its own thread with its own client; the
  page polls for the result. The gateway waits up to 75 s per attempt (the app server's own LLM
  timeout is 60 s, the FAQ service's Ollama timeout is 30 s), so after a cold Ollama start the
  first reply may be the verbatim fallback; "Ask the model again" retries.
- **Frontend.** Plain ES modules and one stylesheet, no build step, no framework, no CDN. Server
  data is inserted as text nodes only. A strict Content-Security-Policy allows same-origin
  resources only.

## Files

| path | role |
|---|---|
| `web/gateway.py` | HTTP server, sessions, CSRF, JSON API, static files |
| `web/static/index.html`, `app.css` | shell and the design system (tokens, layout, motion) |
| `web/static/js/app.js`, `api.js`, `dom.js`, `ui.js`, `art.js` | router, gateway client, DOM builder, shared components, generated abstract plates |
| `web/static/js/views/*.js` | sign-in, programme, seats/checkout, tickets, help, observatory, lab |
| `web/static/fonts/` | Fraunces, Barlow Condensed, IBM Plex Sans, IBM Plex Mono (SIL OFL 1.1, see `LICENSES.md`) |
| `tests/test_web_gateway.py` | gateway tests against an in-process three-node cluster |

## Limitations

- Sessions live in the gateway's memory: restarting the gateway signs everyone out and forgets
  pending request IDs (check My Tickets after such a restart).
- Plain HTTP on localhost; not hardened for exposure to a network. The cookie has no `Secure`
  flag for that reason.
- Booking records carry no timestamp, so My Tickets is ordered by commit order, newest first.
- The backend defines no currency; amounts are shown as the numbers it returns.
- The show artwork is generated geometry keyed by show ID; the catalogue has no images.
