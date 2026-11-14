# Stress test report (Milestone 2)

All numbers below are copied from the JSON the script printed; the raw files are in
[`stress-results/run1-failover.json`](stress-results/run1-failover.json) and
[`stress-results/run2-baseline.json`](stress-results/run2-baseline.json). Nothing was edited
by hand except formatting into tables.

## Environment

| item | value |
|---|---|
| Date | 2026-11-14 (run 1 started 15:14:48 +0530) |
| Host | Windows 11 Home 10.0.26200, 16 logical CPUs; Docker Desktop (Engine 29.8.2), 7.6 GiB VM |
| Cluster | Docker Compose topology from `docker-compose.yml`: 3 Raft nodes (`app-1..3`), LLM node, UI; image `ticket-booking:m2` |
| Load generator | `src/scripts/stress_m2.py`, Python 3.13.14 on the Windows host, gRPC to `127.0.0.1:50051-50053` |
| Raft timing | election timeout 0.45-0.80 s, heartbeat 0.10 s (defaults in `RaftNode`) |

Client and cluster share one machine, so absolute latency/throughput reflect this laptop, not
a networked deployment.

## Workload (`stress_m2.py`)

1. **race** - 16 clients, each logged in as a different seeded user, start together (barrier)
   and book the same seat with distinct request IDs.
2. **dup** - 10 concurrent submissions of one request ID (simulated client retries).
3. **mixed** - 12 worker threads (one session each) for the run duration. Each loop picks a
   random show from the pool and a random seat `A1..A50`; every 4th operation is a
   linearizable seat-map read (`GET SEATS`, served after the leader's read barrier), the rest are
   `BOOK_SEAT` writes with a fresh request ID, retried **with the same ID** on retryable
   statuses (`UNAVAILABLE`, `NOT_LEADER`, `NO_LEADER`, `NO_QUORUM`, `REDIRECT_FAILED`) for up to
   20 s. Every write - including those answered `ALREADY_BOOKED` - is a Raft log entry that was
   committed by a majority before the reply.
4. **failover (run 1 only)** - at t = duration/3 the script stops the current leader container
   with `docker compose stop -t 0 app-N` (immediate kill), polls the survivors until one reports
   `leader`, and at t = 2*duration/3 runs `docker compose start app-N`.
5. **invariants** - after the run: wait until all three nodes report the same commit index,
   committed-log digest and state digest; read `MY_BOOKINGS` for every user; check no seat is
   held by two confirmed bookings, every booking acknowledged `OK` is present and `CONFIRMED`,
   every booking has exactly one successful charge, and the linearizable seat map agrees.

Throughput = completed logical requests / wall-clock duration. Latency for writes is
end-to-end per logical request (includes redirects and same-ID retries). Error rate = final
statuses other than `OK`/`ALREADY_BOOKED` / all write requests.

## Commands

```bash
# cluster
docker compose build
OLLAMA_URL=http://host.docker.internal:11434/api/generate docker compose up -d
# run 1: leader killed and restarted during the run
python src/scripts/stress_m2.py --duration 30 --workers 12 --failover docker --json run1-failover.json
# run 2: no failover, fresh seats
python src/scripts/stress_m2.py --duration 20 --workers 12 --failover none --shows show-6,show-7 --race-seat show-8/A48 --dup-seat show-8/A47 --json run2-baseline.json
```

(`python` = `src/.venv/Scripts/python.exe`, run from the repository root.)

## Results

### Correctness

| check | run 1 (failover) | run 2 (baseline) |
|---|---|---|
| race: exactly one `OK` for the contested seat | PASS (1 OK, 15 ALREADY_BOOKED) | PASS (1 OK, 15 ALREADY_BOOKED) |
| dup: 10 copies of one request ID -> one booking ID | PASS | PASS |
| all nodes identical committed-log and state digests | PASS (commit 3324 on all three, term 11) | PASS |
| no overbooking | PASS | PASS |
| no lost acknowledged booking | PASS | PASS |
| exactly one logical charge per booking | PASS | PASS |
| seat map agrees with confirmed bookings | PASS | PASS |

### Failover (run 1)

| t (s) | event |
|---|---|
| 10.30 | `docker compose stop -t 0 app-1` (leader `node-1`) |
| 11.52 | new leader `node-2` observed; **574 ms** from kill-command completion to a survivor reporting `leader` |
| 20.40 | `docker compose start app-1`; it rejoined as follower and matched the leader's digests in the final check |

Of 3264 writes in run 1, 9 needed at least one retry (the requests in flight around the
kill); all 9 reached a final answer, so the client-visible error rate stayed 0.

### Performance

| metric | run 1 (30 s, failover) | run 2 (20 s, no failover) |
|---|---|---|
| write requests | 3264 (100 OK, 3164 ALREADY_BOOKED) | 1625 (100 OK, 1525 ALREADY_BOOKED) |
| write throughput | 108.7 req/s | 81.1 req/s |
| write error rate | 0.0 | 0.0 |
| write latency p50 / p95 / p99 / max | 66.7 / 143.3 / 211.1 / 3832.8 ms | 115.1 / 213.0 / 243.1 / 259.7 ms |
| read requests (all OK) | 1080 | 536 |
| read throughput | 36.0 req/s | 26.8 req/s |
| read latency p50 / p95 / p99 / max | 47.3 / 80.0 / 449.7 / 4612.1 ms | 74.6 / 115.8 / 127.9 / 145.8 ms |
| race latency p50 / max | 77.8 / 85.2 ms | - |

The multi-second maxima in run 1 are the requests caught by the leader kill (client
timeout, seed rotation, election, retry). Run 2 has no such tail.

### Observation: run 2 is slower than run 1

Run 2 was executed after run 1 on the same cluster, so its Raft log started at ~3,300
entries and ended above 5,000. `RaftNode.propose()` scans the entire log for an existing
entry with the same request ID (idempotency) while holding the node lock, and seat reads
pay for the read-barrier heartbeat round. The higher p50 in run 2 is consistent with this
O(log length) cost. Run 3 below re-measures after replacing the scan with an index.

## Reproducing on a clean cluster

```bash
docker compose down -v      # DESTRUCTIVE: deletes the three Raft volumes
OLLAMA_URL=http://host.docker.internal:11434/api/generate docker compose up -d
python src/scripts/stress_m2.py --duration 30 --workers 12 --failover docker
```

The in-process equivalents (no Docker) are the pytest cases
`test_concurrent_booking_during_election_churn` and `test_fault_injected_raft_rpcs_keep_safety`
in `src/tests/test_raft_m2.py`.


## Run 3: after the request-ID index (quality pass, commit `c55199e`)

`RaftNode.propose()` no longer scans the log for an existing request ID; an index rebuilt at
startup answers the lookup. Same cluster and parameters as run 2, on a larger log
(5,115 entries at start, commit index 8,810 at the end), fresh seats:

```bash
python src/scripts/stress_m2.py --duration 20 --workers 12 --failover none --shows show-2,show-3 --race-seat show-8/A44 --dup-seat show-8/A43 --json docs/stress-results/run3-index.json
```

| metric | run 2 (log scan, log 3.3k -> 5k) | run 3 (index, log 5.1k -> 8.8k) |
|---|---|---|
| write requests | 1625 | 3648 (98 OK, 3550 ALREADY_BOOKED) |
| write throughput | 81.1 req/s | 182.3 req/s |
| write error rate | 0.0 | 0.0 |
| write latency p50 / p95 / p99 / max | 115.1 / 213.0 / 243.1 / 259.7 ms | 47.2 / 84.7 / 108.9 / 153.0 ms |
| read throughput | 26.8 req/s | 60.5 req/s |
| read latency p50 / p95 / p99 / max | 74.6 / 115.8 / 127.9 / 145.8 ms | 34.4 / 47.0 / 60.9 / 81.9 ms |
| invariant checks | 7/7 PASS | 7/7 PASS (race 1 OK / 15 ALREADY_BOOKED; 10 duplicates -> 1 booking) |

The image was rebuilt for run 3, so it also includes the other quality-pass changes; the
request-ID lookup is the change on the write hot path. Raw JSON:
[`stress-results/run3-index.json`](stress-results/run3-index.json).
