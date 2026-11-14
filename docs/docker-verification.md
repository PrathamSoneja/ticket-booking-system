# Docker Compose verification

Status: **PASSED** (all checks in the run below passed). Nothing in this file is hand-edited
output: section "Recorded run" is the verbatim stdout of `src/scripts/verify_docker.py`
(gRPC-transport INFO lines from the grpc C core removed).

## Environment

- Host: Windows 11 Home (10.0.26200), Docker Desktop, Docker Engine 29.8.2, 16 CPUs, 7.6 GiB RAM for the Docker VM
- Image: `ticket-booking:m2` (python:3.13-slim, grpcio 1.83.1, protobuf 6.33.6, streamlit 1.50.0), built from `src/Dockerfile`
- Model runtime: host Ollama with `llama3.2:1b`, reached through `OLLAMA_URL=http://host.docker.internal:11434/api/generate`

## Topology

| service | role | ports (host) | volume |
|---|---|---|---|
| `llm` | LLM gRPC server (`LLMService.GetLLMAnswer`) | none (internal :50055) | none |
| `app-1` / `app-2` / `app-3` | Raft application nodes `node-1..3` | 127.0.0.1:50051 / 50052 / 50053 | `node-1-data` / `node-2-data` / `node-3-data` |
| `ui` | Streamlit gRPC client | 127.0.0.1:8501 | none (no Docker socket) |
| `ollama`, `ollama-pull` | optional containerised model runtime (`--profile ollama`) | none | `ollama-data` |

Peer discovery is static: every node gets the same `--peers node-1=app-1:50051,...` list
(compose service DNS names). Leaders advertise host-facing addresses (`127.0.0.1:5005x`) to
clients; the UI container translates them with `TB_REDIRECT_MAP`.

Health checks: Raft nodes run `python -m ticket_booking.healthcheck raft 127.0.0.1:50051`
(a real `RequestVote` RPC from a non-member ID, which the node rejects before touching any
state); the LLM node runs a gRPC channel-readiness probe; the UI probes `/_stcore/health`.
Raft nodes do not depend on the LLM service; FAQ calls are retried per request and fall
back to labelled verbatim FAQ text while the LLM is down.

## Commands used

```bash
docker compose build
OLLAMA_URL=http://host.docker.internal:11434/api/generate docker compose up -d
OLLAMA_URL=http://host.docker.internal:11434/api/generate src/.venv/Scripts/python.exe src/scripts/verify_docker.py
```

The script itself runs only these topology commands: `docker compose stop|start app-N`,
`docker compose stop|start llm`, `docker compose down` (without `-v`), `docker compose up -d`.

## Recorded run

# Docker verification run 2026-11-14 15:11:43 +0530

## 1. Startup and health
- PASS: all five services healthy -- {'llm': 'healthy', 'app-1': 'healthy', 'app-2': 'healthy', 'app-3': 'healthy', 'ui': 'healthy'}
- PASS: UI health endpoint -- ok
- PASS: no Docker socket (or any volume) mounted into UI -- []
- PASS: login via gRPC

## 2. Exactly one leader
  | node | role | term | leader | log | commit | applied | log digest | state digest |
  |---|---|---|---|---|---|---|---|---|
  | node-1 | follower | 1 | node-2 | 1 | 1 | 1 | 2cd95996e186f440 | 91019854b656df44 |
  | node-2 | leader | 1 | node-2 | 1 | 1 | 1 | 2cd95996e186f440 | 91019854b656df44 |
  | node-3 | follower | 1 | node-2 | 1 | 1 | 1 | 2cd95996e186f440 | 91019854b656df44 |
- PASS: exactly one leader and two followers -- ['follower', 'leader', 'follower']

## 3. Booking and replication
- PASS: book show-1/A1 -- OK 79742bc1-7579-50a2-9434-cd15fe963eae
  | node | role | term | leader | log | commit | applied | log digest | state digest |
  |---|---|---|---|---|---|---|---|---|
  | node-1 | follower | 1 | node-2 | 2 | 2 | 2 | e19a10a0d829eb39 | ffca94f618e2d157 |
  | node-2 | leader | 1 | node-2 | 2 | 2 | 2 | e19a10a0d829eb39 | ffca94f618e2d157 |
  | node-3 | follower | 1 | node-2 | 2 | 2 | 2 | e19a10a0d829eb39 | ffca94f618e2d157 |
- PASS: all nodes converge to identical log and state digests

## 4. Leader failure
  leader before failure: node-2
  `docker compose stop app-2` -> exit 0 (3.6s)
- PASS: new leader elected among survivors -- node-3 after 3.76s (includes container stop time)
- PASS: read-after-write: A1 still BOOKED via new leader -- OK/BOOKED
- PASS: book show-1/A2 with old leader down -- OK 57cb0af0-3249-5b25-a292-c4a0a2518ab1

## 5. Old-leader restart and catch-up
  `docker compose start app-2` -> exit 0 (0.6s)
  | node | role | term | leader | log | commit | applied | log digest | state digest |
  |---|---|---|---|---|---|---|---|---|
  | node-1 | leader | 3 | node-1 | 4 | 4 | 4 | 2b1c02160597e85b | 9763bfcba17dedef |
  | node-2 | follower | 3 | node-1 | 4 | 4 | 4 | 2b1c02160597e85b | 9763bfcba17dedef |
  | node-3 | follower | 3 | node-1 | 4 | 4 | 4 | 2b1c02160597e85b | 9763bfcba17dedef |
- PASS: node-2 rejoined as follower with identical committed log and state

## 6. No quorum
  `docker compose stop app-2` -> exit 0 (3.6s)
  `docker compose stop app-3` -> exit 0 (3.6s)
- PASS: booking without quorum is not acknowledged -- UNAVAILABLE: The booking service is unavailable.
- PASS: linearizable read refused without quorum -- NO_QUORUM
  `docker compose start app-2` -> exit 0 (0.5s)
- PASS: retry with same request ID succeeds once quorum returns -- OK 5f6268d5-e9e9-53b8-8006-010288841907
- PASS: exactly one booking and one logical charge for that request ID -- [{'booking_id': '5f6268d5-e9e9-53b8-8006-010288841907', 'show_id': 'show-2', 'seat_id': 'A49', 'status': 'CONFIRMED', 'txn': 'tx_5f6268d5e9e9', 'charges': 1, 'amount': 50.0, 'refunded': False}]
  `docker compose start app-3` -> exit 0 (0.5s)
- PASS: cluster reconverges after quorum loss

## 7. LLM node: answer, outage, recovery
- PASS: FAQ answered (LLM via gRPC; Ollama runtime reachable) or honestly labelled fallback -- OK; generated=True fallback=False correlation=faq-84f083051a4d4d2196a3563897685025
  LLM status: OK; answer starts: 'Bookings can be cancelled up to 2 hours prior to showtime via the CANCEL_SEAT request.'
- PASS: out-of-domain query rejected -- OUT_OF_DOMAIN
  `docker compose stop llm` -> exit 0 (3.8s)
- PASS: LLM outage -> labelled verbatim fallback -- LLM_UNAVAILABLE: 'The FAQ service could not be reached. Fallback - matching FAQ text shown verbatim, not AI-generated:'
- PASS: booking still works while LLM node is down -- OK d7bdc040-ba23-5b7d-9e97-caa89695916a
  `docker compose start llm` -> exit 0 (0.7s)

## 8. Full restart persistence (`down` keeps named volumes)
  `docker compose down` -> exit 0 (4.5s)
- PASS: named Raft volumes survive `docker compose down` -- ['project_node-1-data', 'project_node-2-data', 'project_node-3-data']
  `docker compose up -d` -> exit 0 (1.6s)
- PASS: show-1/A1 still BOOKED after full restart -- OK/BOOKED
- PASS: show-1/A2 still BOOKED after full restart -- OK/BOOKED
- PASS: show-2/A49 still BOOKED after full restart -- OK/BOOKED
- PASS: show-3/A1 still BOOKED after full restart -- OK/BOOKED
  | node | role | term | leader | log | commit | applied | log digest | state digest |
  |---|---|---|---|---|---|---|---|---|
  | node-1 | leader | 10 | node-1 | 10 | 10 | 10 | d1e583317bb37e14 | 363f4e34baef2d6f |
  | node-2 | follower | 10 | node-1 | 10 | 10 | 10 | d1e583317bb37e14 | 363f4e34baef2d6f |
  | node-3 | follower | 10 | node-1 | 10 | 10 | 10 | d1e583317bb37e14 | 363f4e34baef2d6f |
- PASS: committed log replayed (commit index did not go backwards) -- before=9

## Result: ALL CHECKS PASSED
