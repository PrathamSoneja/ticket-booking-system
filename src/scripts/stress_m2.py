import argparse
import json
import platform
import random
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ticket_booking.client import Client  # noqa: E402

RETRYABLE = {"NOT_LEADER", "UNAVAILABLE", "NO_QUORUM", "NO_LEADER", "REDIRECT_FAILED"}
FINAL_OK = {"OK", "ALREADY_BOOKED"}
USERS = [(f"user{i}", f"pass{i}") for i in range(1, 33)]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return round(xs[k], 1)


def lat_summary(xs):
    return {"n": len(xs), "p50_ms": pct(xs, 50), "p95_ms": pct(xs, 95), "p99_ms": pct(xs, 99),
            "max_ms": round(max(xs), 1) if xs else None, "mean_ms": round(statistics.mean(xs), 1) if xs else None}


def login(nodes, user, pw, timeout=30):
    c = Client(nodes, timeout=5.0)
    end = time.monotonic() + timeout
    while not c.login(user, pw)[0]:
        if time.monotonic() > end:
            raise SystemExit(f"login failed for {user}")
        time.sleep(0.5)
    return c


def book_final(c, show, seat, rid, deadline=20.0):
    t0 = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        st, bid, msg = c.book_seat(show, seat, req_id=rid)
        if st not in RETRYABLE or time.monotonic() - t0 > deadline:
            return st, bid, attempts, (time.monotonic() - t0) * 1000


def status_all(nodes, token):
    out = {}
    for addr in nodes:
        c = Client(addr, timeout=1.5, max_retries=0)
        c.token = token
        st, info, _ = c.get_cluster_status()
        c.close()
        out[addr] = info if st == "OK" else {"rpc_status": st}
    return out


def leader_addr(nodes, token):
    snap = status_all(nodes, token)
    leaders = [(i["term"], a, i["node_id"]) for a, i in snap.items() if i.get("role") == "leader"]
    return max(leaders)[1:] if leaders else (None, None)


def wait_converged(nodes, token, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = status_all(nodes, token)
        ok = [i for i in snap.values() if "commit_index" in i]
        if len(ok) == len(nodes) and len({(i["commit_index"], i["log_digest"], i["state_digest"]) for i in ok}) == 1:
            return True, snap
        time.sleep(0.3)
    return False, status_all(nodes, token)


def phase_race(nodes, show, seat, n):
    clients = [login(nodes, *USERS[i % len(USERS)]) for i in range(n)]
    go = Event()
    res = [None] * n

    def run(i):
        go.wait()
        res[i] = book_final(clients[i], show, seat, f"race-{uuid4().hex}")

    ts = [Thread(target=run, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    go.set()
    for t in ts:
        t.join()
    for c in clients:
        c.close()
    statuses = [r[0] for r in res]
    return {"seat": f"{show}/{seat}", "clients": n, "statuses": {s: statuses.count(s) for s in set(statuses)},
            "ok_booking_ids": [r[1] for r in res if r[0] == "OK"], "latency": lat_summary([r[3] for r in res])}


def phase_dup(nodes, show, seat, n):
    c = login(nodes, "alice", "wonderland")
    rid = f"dup-{uuid4().hex}"
    def one(_):
        x = Client(nodes, timeout=5.0)
        x.token = c.token
        try:
            return book_final(x, show, seat, rid)
        finally:
            x.close()

    with ThreadPoolExecutor(max_workers=n) as pool:
        res = list(pool.map(one, range(n)))
    c.close()
    return {"seat": f"{show}/{seat}", "copies": n, "request_id": rid, "statuses": sorted({r[0] for r in res}),
            "booking_ids": sorted({r[1] for r in res if r[1]})}


def phase_mixed(nodes, workers, duration, shows, read_every, failover, seed):
    stop = Event()
    lock = Lock()
    writes, reads, events = [], [], []
    t_start = time.monotonic()

    def worker(i):
        rng = random.Random(seed + i)
        user, pw = USERS[i % len(USERS)]
        c = login(nodes, user, pw)
        n = 0
        while not stop.is_set():
            n += 1
            show = rng.choice(shows)
            if read_every and n % read_every == 0:
                t0 = time.monotonic()
                st, _, _ = c.get_seats(show)
                with lock:
                    reads.append((time.monotonic() - t_start, st, (time.monotonic() - t0) * 1000))
                continue
            seat = f"A{rng.randint(1, 50)}"
            rid = uuid4().hex
            t0 = time.monotonic() - t_start
            st, bid, attempts, ms = book_final(c, show, seat, rid)
            with lock:
                writes.append({"t": t0, "user": user, "show": show, "seat": seat, "rid": rid,
                               "status": st, "booking_id": bid, "attempts": attempts, "ms": ms})
        c.close()

    ts = [Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in ts:
        t.start()
    if failover == "docker":
        admin = login(nodes, "alice", "wonderland")
        time.sleep(duration / 3)
        addr, node_id = leader_addr(nodes, admin.token)
        svc = {"node-1": "app-1", "node-2": "app-2", "node-3": "app-3"}[node_id]
        t_stop = time.monotonic() - t_start
        subprocess.run(["docker", "compose", "stop", "-t", "0", svc], capture_output=True)
        events.append({"t": round(t_stop, 2), "event": f"docker compose stop -t 0 {svc} (leader {node_id})"})
        t_kill_done = time.monotonic()
        new = None
        while time.monotonic() - t_kill_done < 30:
            a, nid = leader_addr([x for x in nodes if x != addr], admin.token)
            if nid:
                new = nid
                break
            time.sleep(0.05)
        events.append({"t": round(time.monotonic() - t_start, 2), "event": f"new leader observed: {new}",
                       "failover_ms": round((time.monotonic() - t_kill_done) * 1000)})
        time.sleep(max(0, duration * 2 / 3 - (time.monotonic() - t_start)))
        subprocess.run(["docker", "compose", "start", svc], capture_output=True)
        events.append({"t": round(time.monotonic() - t_start, 2), "event": f"docker compose start {svc}"})
        admin.close()
    time.sleep(max(0, duration - (time.monotonic() - t_start)))
    stop.set()
    for t in ts:
        t.join(timeout=60)
    elapsed = time.monotonic() - t_start
    return writes, reads, events, elapsed


def invariants(nodes, all_ok):
    """all_ok: list of (user, show, seat(s), booking_id) acknowledged OK by the cluster."""
    admin = login(nodes, "alice", "wonderland")
    conv, snap = wait_converged(nodes, admin.token)
    checks = [("all nodes converge to identical committed-log and state digests", conv,
               {a: {k: i.get(k) for k in ("node_id", "role", "term", "commit_index", "log_digest", "state_digest")} for a, i in snap.items()})]
    bookings = {}
    for user, pw in USERS + [("alice", "wonderland")]:
        c = login(nodes, user, pw)
        st, rows, _ = c.get_my_bookings()
        c.close()
        if st != "OK":
            checks.append((f"MY_BOOKINGS readable for {user}", False, st))
            continue
        for r in rows:
            bookings[r["booking_id"]] = r
    holders = {}
    for b in bookings.values():
        if b["status"] == "CONFIRMED":
            for s in b["seat_id"].split(","):
                holders.setdefault((b["show_id"], s), []).append(b["booking_id"])
    over = {f"{k[0]}/{k[1]}": v for k, v in holders.items() if len(v) > 1}
    checks.append(("no seat held by two confirmed bookings (no overbooking)", not over, over or f"{len(holders)} seats held"))
    missing = [b for *_, b in all_ok if b not in bookings or bookings[b]["status"] != "CONFIRMED"]
    checks.append(("every OK-acknowledged booking is present and CONFIRMED (no lost commit)", not missing, missing or f"{len(all_ok)} acknowledged"))
    bad_charge = {b: r["charges"] for b, r in bookings.items() if r["charges"] != 1}
    checks.append(("exactly one logical charge per booking (no duplicate charge)", not bad_charge, bad_charge or f"{len(bookings)} bookings"))
    seat_maps = {}
    for show in sorted({b["show_id"] for b in bookings.values()}):
        st, seats, _ = admin.get_seats(show)
        seat_maps[show] = {s["seat_id"]: s["status"] for s in seats} if st == "OK" else None
    mismatch = [k for k in holders if seat_maps.get(k[0]) and seat_maps[k[0]].get(k[1]) != "BOOKED"]
    checks.append(("linearizable seat map agrees with confirmed bookings", not mismatch, mismatch or "agree"))
    admin.close()
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", default="127.0.0.1:50051,127.0.0.1:50052,127.0.0.1:50053")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--race-clients", type=int, default=16)
    ap.add_argument("--dup-copies", type=int, default=10)
    ap.add_argument("--shows", default="show-4,show-5")
    ap.add_argument("--race-seat", default="show-8/A50")
    ap.add_argument("--dup-seat", default="show-8/A49")
    ap.add_argument("--read-every", type=int, default=4, help="every Nth op per worker is a seat read (0 = writes only)")
    ap.add_argument("--failover", choices=["none", "docker"], default="none")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    nodes = [x.strip() for x in a.nodes.split(",") if x.strip()]
    report = {"command": " ".join(sys.argv), "started": time.strftime("%Y-%m-%d %H:%M:%S %z"),
              "env": {"python": platform.python_version(), "platform": platform.platform(), "nodes": nodes}}

    rs, rseat = a.race_seat.split("/")
    report["race"] = phase_race(nodes, rs, rseat, a.race_clients)
    ds, dseat = a.dup_seat.split("/")
    report["dup"] = phase_dup(nodes, ds, dseat, a.dup_copies)
    writes, reads, events, elapsed = phase_mixed(nodes, a.workers, a.duration, a.shows.split(","), a.read_every, a.failover, a.seed)
    statuses = {}
    for w in writes:
        statuses[w["status"]] = statuses.get(w["status"], 0) + 1
    rstat = {}
    for r in reads:
        rstat[r[1]] = rstat.get(r[1], 0) + 1
    errors = sum(v for k, v in statuses.items() if k not in FINAL_OK)
    report["mixed"] = {
        "workers": a.workers, "duration_s": round(elapsed, 2), "failover": a.failover, "events": events,
        "write_requests": len(writes), "write_statuses": statuses,
        "write_throughput_rps": round(len(writes) / elapsed, 1),
        "write_error_rate": round(errors / len(writes), 4) if writes else None,
        "write_latency_end_to_end": lat_summary([w["ms"] for w in writes]),
        "writes_needing_retry": sum(1 for w in writes if w["attempts"] > 1),
        "read_requests": len(reads), "read_statuses": rstat,
        "read_throughput_rps": round(len(reads) / elapsed, 1),
        "read_latency": lat_summary([r[2] for r in reads if r[1] == "OK"]),
    }
    ok = [(w["user"], w["show"], w["seat"], w["booking_id"]) for w in writes if w["status"] == "OK"]
    ok += [("race", rs, rseat, b) for b in report["race"]["ok_booking_ids"]]
    ok += [("alice", ds, dseat, b) for b in report["dup"]["booking_ids"]]
    checks = invariants(nodes, ok)
    checks.insert(0, ("race: exactly one OK booking for the contested seat", len(report["race"]["ok_booking_ids"]) == 1, report["race"]["statuses"]))
    checks.insert(1, ("dup: all copies of one request ID return one booking ID", len(report["dup"]["booking_ids"]) <= 1 and len(report["dup"]["statuses"]) == 1, report["dup"]))
    report["checks"] = [{"check": n, "passed": bool(p), "detail": d} for n, p, d in checks]
    report["passed"] = all(c["passed"] for c in report["checks"])
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if a.json:
        Path(a.json).write_text(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
