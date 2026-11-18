import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ticket_booking.client import Client  # noqa: E402

NODES = {"node-1": "127.0.0.1:50051", "node-2": "127.0.0.1:50052", "node-3": "127.0.0.1:50053"}
SERVICE = {"node-1": "app-1", "node-2": "app-2", "node-3": "app-3"}
RETRYABLE = {"NOT_LEADER", "UNAVAILABLE", "NO_QUORUM", "NO_LEADER", "REDIRECT_FAILED"}
FAILED = []


def out(line=""):
    print(line, flush=True)


def check(name, ok, detail=""):
    out(f"- {'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)
    return ok


def compose(*args, timeout=180):
    cmd = ["docker", "compose", *args]
    t = time.monotonic()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out(f"  `{' '.join(cmd)}` -> exit {res.returncode} ({time.monotonic() - t:.1f}s)")
    return res


def health(service):
    res = subprocess.run(["docker", "compose", "ps", "--format", "json", service], capture_output=True, text=True)
    for line in res.stdout.splitlines():
        try:
            return json.loads(line).get("Health") or json.loads(line).get("State")
        except json.JSONDecodeError:
            continue
    return "absent"


def wait_healthy(services, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        states = {s: health(s) for s in services}
        if all(v == "healthy" for v in states.values()):
            return states
        time.sleep(2)
    return {s: health(s) for s in services}


def status_of(node, token):
    c = Client(NODES[node], timeout=1.5, max_retries=0)
    c.token = token
    try:
        st, info, msg = c.get_cluster_status()
    finally:
        c.close()
    return info if st == "OK" else {"rpc_status": st}


def snapshot(token, nodes=NODES):
    return {n: status_of(n, token) for n in nodes}


def leader_of(snap):
    leaders = [(i["term"], n) for n, i in snap.items() if i.get("role") == "leader"]
    return max(leaders)[1] if leaders else None


def wait_leader(token, exclude=(), timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = snapshot(token, [n for n in NODES if n not in exclude])
        lead = leader_of(snap)
        if lead:
            return lead, snap
        time.sleep(0.1)
    return None, snapshot(token)


def book(client, show, seat, rid=None, deadline=15):
    rid = rid or uuid4().hex
    end = time.monotonic() + deadline
    while True:
        st, bid, msg = client.book_seat(show, seat, req_id=rid)
        if st not in RETRYABLE or time.monotonic() > end:
            return st, bid, msg, rid
        time.sleep(0.3)


def read_seat(client, show, seat, deadline=15):
    end = time.monotonic() + deadline
    while True:
        st, seats, _ = client.get_seats(show)
        if st == "OK" or time.monotonic() > end:
            return st, next((s["status"] for s in seats if s["seat_id"] == seat), None)
        time.sleep(0.3)


def free_slot(client, prefer):
    for show in [prefer] + [
        x for x in ["show-1", "show-2", "show-3", "show-4", "show-5", "show-6", "show-7", "show-8"] if x != prefer
    ]:
        end = time.monotonic() + 15
        st, seats, _ = client.get_seats(show)
        while st != "OK" and time.monotonic() < end:
            time.sleep(0.3)
            st, seats, _ = client.get_seats(show)
        free = [s["seat_id"] for s in seats if s["status"] == "AVAILABLE"]
        if free:
            return show, free[0]
    return prefer, None


def wait_converged(token, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = snapshot(token)
        ok = [i for i in snap.values() if "commit_index" in i]
        if len(ok) == 3 and len({(i["commit_index"], i["log_digest"], i["state_digest"]) for i in ok}) == 1:
            return True, snap
        time.sleep(0.3)
    return False, snapshot(token)


def table(snap):
    out("  | node | role | term | leader | log | commit | applied | log digest | state digest |")
    out("  |---|---|---|---|---|---|---|---|---|")
    for n, i in snap.items():
        if "role" in i:
            out(
                f"  | {n} | {i['role']} | {i['term']} | {i['leader_id']} | {i['log_length']} | {i['commit_index']} | {i['last_applied']} | {i['log_digest']} | {i['state_digest']} |"
            )
        else:
            out(f"  | {n} | {i.get('rpc_status')} | | | | | | | |")


def main():
    out(f"# Docker verification run {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    out()
    out("## 1. Startup and health")
    states = wait_healthy(["llm", "app-1", "app-2", "app-3", "ui"])
    check("all five services healthy", all(v == "healthy" for v in states.values()), str(states))
    ui_ok = urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=5).read().decode().strip()
    check("UI health endpoint", ui_ok == "ok", ui_ok)
    mounts = subprocess.run(["docker", "compose", "ps", "-q", "ui"], capture_output=True, text=True).stdout.strip()
    insp = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", mounts], capture_output=True, text=True
    ).stdout
    check(
        "no Docker socket (or any volume) mounted into UI",
        "docker.sock" not in insp and json.loads(insp or "[]") == [],
        insp.strip(),
    )

    seeds = list(NODES.values())
    client = Client(seeds, timeout=5.0)
    end = time.monotonic() + 30
    while not client.login("alice", "wonderland")[0] and time.monotonic() < end:
        time.sleep(1)
    check("login via gRPC", bool(client.token))
    token = client.token

    out()
    out("## 2. Exactly one leader")
    lead, snap = wait_leader(token)
    table(snapshot(token))
    roles = [i.get("role") for i in snapshot(token).values()]
    check("exactly one leader and two followers", sorted(roles) == ["follower", "follower", "leader"], str(roles))

    out()
    out("## 3. Booking and replication")
    show1, seat1 = free_slot(client, "show-1")
    st, bid1, msg, _ = book(client, show1, seat1)
    check(f"book {show1}/{seat1}", st == "OK", f"{st} {bid1}")
    conv, snap = wait_converged(token)
    table(snap)
    check("all nodes converge to identical log and state digests", conv)

    out()
    out("## 4. Leader failure")
    lead, _ = wait_leader(token)
    out(f"  leader before failure: {lead}")
    t0 = time.monotonic()
    compose("stop", SERVICE[lead])
    new, snap = wait_leader(token, exclude=(lead,))
    failover = time.monotonic() - t0
    check(
        "new leader elected among survivors",
        new is not None and new != lead,
        f"{new} after {failover:.2f}s (includes container stop time)",
    )
    st, state = read_seat(client, show1, seat1)
    check(f"read-after-write: {seat1} still BOOKED via new leader", st == "OK" and state == "BOOKED", f"{st}/{state}")
    show2, seat2 = free_slot(client, show1)
    st, bid2, msg, _ = book(client, show2, seat2)
    check(f"book {show2}/{seat2} with old leader down", st == "OK", f"{st} {bid2}")

    out()
    out("## 5. Old-leader restart and catch-up")
    compose("start", SERVICE[lead])
    wait_healthy([SERVICE[lead]], timeout=60)
    conv, snap = wait_converged(token, timeout=45)
    table(snap)
    check(
        f"{lead} rejoined as follower with identical committed log and state",
        conv and snap[lead].get("role") == "follower",
    )

    out()
    out("## 6. No quorum")
    lead, _ = wait_leader(token)
    others = [n for n in NODES if n != lead]
    show3, seat3 = free_slot(client, "show-2")
    for n in others:
        compose("stop", SERVICE[n])
    single = Client(NODES[lead], timeout=3.0, max_retries=0)
    single.token = token
    rid = "docker-noquorum-" + uuid4().hex[:8]
    st, bid, msg = single.book_seat(show3, seat3, req_id=rid, hops=1)
    check("booking without quorum is not acknowledged", st != "OK", f"{st}: {msg}")
    st_r, _, msg_r = single.get_seats(show3)
    check("linearizable read refused without quorum", st_r != "OK", st_r)
    compose("start", SERVICE[others[0]])
    wait_healthy([SERVICE[others[0]]], timeout=60)
    st, bid3, msg, _ = book(client, show3, seat3, rid=rid, deadline=30)
    check("retry with same request ID succeeds once quorum returns", st == "OK", f"{st} {bid3}")
    _, rows, _ = client.get_my_bookings()
    mine = [r for r in rows if r["booking_id"] == bid3]
    check(
        "exactly one booking and one logical charge for that request ID",
        len(mine) == 1 and mine[0]["charges"] == 1,
        str(mine),
    )
    compose("start", SERVICE[others[1]])
    wait_healthy([SERVICE[others[1]]], timeout=60)
    conv, snap = wait_converged(token, timeout=45)
    check("cluster reconverges after quorum loss", conv)

    out()
    out("## 7. LLM node: answer, outage, recovery")
    faq = Client(seeds, timeout=60.0)
    faq.token = token
    st, text, sup = faq.ask_faq_detailed("How do I cancel a booking?")
    check(
        "FAQ answered (LLM via gRPC; Ollama runtime reachable) or honestly labelled fallback",
        st in ("OK", "LLM_UNAVAILABLE"),
        f"{st}; generated={sup.get('generated')} fallback={sup.get('fallback')} correlation={sup.get('correlation_id')}",
    )
    out(f"  LLM status: {st}; answer starts: {text[:120]!r}")
    st_ood, _, _ = faq.ask_faq_detailed("What is the capital of France?")
    check("out-of-domain query rejected", st_ood == "OUT_OF_DOMAIN", st_ood)
    compose("stop", "llm")
    st, text, sup = faq.ask_faq_detailed("Is payment refundable on cancellation?")
    check(
        "LLM outage -> labelled verbatim fallback",
        st == "LLM_UNAVAILABLE" and sup.get("fallback") is True,
        f"{st}: {text[:100]!r}",
    )
    show4, seat4 = free_slot(client, "show-3")
    st, bid4, msg, _ = book(client, show4, seat4)
    check("booking still works while LLM node is down", st == "OK", f"{st} {bid4}")
    compose("start", "llm")
    wait_healthy(["llm"], timeout=60)

    out()
    out("## 8. Full restart persistence (`down` keeps named volumes)")
    before = snapshot(token)
    compose("down", timeout=240)
    project = json.loads(
        subprocess.run(["docker", "compose", "config", "--format", "json"], capture_output=True, text=True).stdout
    )["name"]
    vols = subprocess.run(
        ["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
    ).stdout.split()
    kept = [v for v in vols if v.endswith(("node-1-data", "node-2-data", "node-3-data"))]
    check("named Raft volumes survive `docker compose down`", len(kept) == 3, str(kept))
    compose("up", "-d", timeout=240)
    wait_healthy(["llm", "app-1", "app-2", "app-3", "ui"])
    client2 = Client(seeds, timeout=5.0)
    end = time.monotonic() + 60
    while not client2.login("alice", "wonderland")[0] and time.monotonic() < end:
        time.sleep(1)
    token2 = client2.token
    wait_leader(token2)
    for show, seat in ((show1, seat1), (show2, seat2), (show3, seat3), (show4, seat4)):
        st, state = read_seat(client2, show, seat)
        check(f"{show}/{seat} still BOOKED after full restart", st == "OK" and state == "BOOKED", f"{st}/{state}")
    conv, after = wait_converged(token2, timeout=45)
    table(after)
    prev = max(i.get("commit_index", 0) for i in before.values())
    check(
        "committed log replayed (commit index did not go backwards)",
        conv and min(i["commit_index"] for i in after.values()) >= prev,
        f"before={prev}",
    )

    out()
    out(f"## Result: {'ALL CHECKS PASSED' if not FAILED else 'FAILED: ' + '; '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
