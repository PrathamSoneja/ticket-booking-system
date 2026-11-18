import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from ticket_booking.client import Client

DEFAULT_NODES = "node-1=127.0.0.1:50051,node-2=127.0.0.1:50052,node-3=127.0.0.1:50053"
RETRYABLE = {"NOT_LEADER", "UNAVAILABLE", "NO_QUORUM", "NO_LEADER", "REDIRECT_FAILED"}


def parse_map(raw):
    out = {}
    for bit in (raw or "").split(","):
        bit = bit.strip()
        if "=" in bit:
            k, v = bit.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


class Config:
    def __init__(self, nodes=None, redirect_map=None, services=None):
        self.nodes = nodes if nodes is not None else parse_map(os.environ.get("TB_NODES", DEFAULT_NODES))
        self.redirect_map = (
            redirect_map if redirect_map is not None else parse_map(os.environ.get("TB_REDIRECT_MAP", ""))
        )
        self.services = services if services is not None else parse_map(os.environ.get("TB_COMPOSE_SERVICES", ""))

    def client(self, token=None, timeout=3.0, max_retries=3):
        c = Client(list(self.nodes.values()), timeout=timeout, max_retries=max_retries, redirect_map=self.redirect_map)
        c.token = token
        return c

    def stop_hint(self, node_id):
        svc = self.services.get(node_id)
        if svc:
            return f"docker compose stop {svc}"
        return f"stop the process serving {node_id} ({self.nodes.get(node_id, '?')})"

    def start_hint(self, node_id):
        svc = self.services.get(node_id)
        if svc:
            return f"docker compose start {svc}"
        return f"restart {node_id} with the same --node-id/--peers/--data-dir"


def now_iso():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Timeline:
    """Timestamped, correlated event log for the Demo Lab."""

    def __init__(self):
        self.t0 = time.monotonic()
        self.events = []

    def add(self, kind, detail, node="", correlation=""):
        ev = {
            "ts": now_iso(),
            "t_ms": round((time.monotonic() - self.t0) * 1000),
            "kind": kind,
            "node": node,
            "correlation": correlation,
            "detail": detail,
        }
        self.events.append(ev)
        return ev


# ---------------------------------------------------------------- observatory


def probe_node(cfg, node_id, token, timeout=1.5):
    """One CLUSTER_STATUS RPC to exactly one node (no redirects, no retries)."""
    addr = cfg.nodes[node_id]
    c = Client(addr, timeout=timeout, max_retries=0)
    c.token = token
    start = time.monotonic()
    try:
        status, info, msg = c.get_cluster_status()
    finally:
        c.close()
    row = {
        "node": node_id,
        "address": addr,
        "rpc_ms": round((time.monotonic() - start) * 1000, 1),
        "reachable": status != "UNAVAILABLE",
        "rpc_status": status,
    }
    if status == "OK":
        row.update(info)
    else:
        row["error"] = msg
    return row


def observe(cfg, token):
    """Probe every node in parallel and derive leader view and replication lag."""
    with ThreadPoolExecutor(max_workers=len(cfg.nodes)) as pool:
        rows = list(pool.map(lambda n: probe_node(cfg, n, token), cfg.nodes))
    sampled = now_iso()
    ok = [r for r in rows if r["rpc_status"] == "OK"]
    leaders = [r for r in ok if r.get("role") == "leader"]
    top = max(leaders, key=lambda r: r["term"]) if leaders else None
    for r in rows:
        r["sampled_at"] = sampled
        if r["rpc_status"] != "OK":
            r["role"] = "UNREACHABLE" if not r["reachable"] else f"? ({r['rpc_status']})"
            continue
        if top is not None:
            r["commit_lag"] = top["commit_index"] - r["commit_index"]
            peer_match = top.get("match_index", {}).get(r["node_id"])
            r["leader_match_index"] = top["log_length"] if r is top else peer_match
    return {
        "sampled_at": sampled,
        "rows": rows,
        "leader": top["node_id"] if top else None,
        "term": top["term"] if top else None,
        "leaders_seen": [(r["node_id"], r["term"]) for r in leaders],
    }


def consistency_report(snapshot):
    """Compare reachable nodes that report the same commit index."""
    ok = [r for r in snapshot["rows"] if r["rpc_status"] == "OK"]
    by_commit = {}
    for r in ok:
        by_commit.setdefault((r["commit_index"], r["last_applied"]), []).append(r)
    groups = []
    for (commit, applied), rs in sorted(by_commit.items()):
        groups.append(
            {
                "commit_index": commit,
                "nodes": [r["node_id"] for r in rs],
                "log_digests_equal": len({r["log_digest"] for r in rs}) == 1,
                "state_digests_equal": applied == commit and len({r["state_digest"] for r in rs}) == 1,
            }
        )
    terms = {}
    for node_id, term in snapshot["leaders_seen"]:
        terms.setdefault(term, []).append(node_id)
    return {
        "groups": groups,
        "converged": len(by_commit) == 1 and len(ok) == len(snapshot["rows"]),
        "multiple_leaders_same_term": {t: n for t, n in terms.items() if len(n) > 1},
    }


# ---------------------------------------------------------------- demo lab


def _book_until_final(cfg, token, show, seat, rid, deadline_s=10.0, card="4242424242424242"):
    c = cfg.client(token, timeout=3.0, max_retries=2)
    start = time.monotonic()
    status, bid, msg = "UNAVAILABLE", "", ""
    attempts = 0
    try:
        while time.monotonic() - start < deadline_s:
            attempts += 1
            status, bid, msg = c.book_seat(show, seat, card=card, req_id=rid)
            if status not in RETRYABLE:
                break
            time.sleep(0.1)
    finally:
        c.close()
    return {
        "request_id": rid,
        "status": status,
        "booking_id": bid,
        "message": msg,
        "attempts": attempts,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
    }


def _booking_rows(cfg, token, booking_ids):
    c = cfg.client(token)
    try:
        status, rows, msg = c.get_my_bookings()
    finally:
        c.close()
    if status != "OK":
        return status, []
    return status, [r for r in rows if r["booking_id"] in booking_ids]


def _seat_state(cfg, token, show, seat):
    c = cfg.client(token)
    try:
        status, seats, _ = c.get_seats(show)
    finally:
        c.close()
    return status, next((s["status"] for s in seats if s["seat_id"] == seat), None)


def seat_race(cfg, token, show, seat, n, timeline):
    """n concurrent clients, distinct request IDs, same seat."""
    rids = [f"race-{uuid4().hex[:12]}" for _ in range(n)]
    for rid in rids:
        timeline.add("race.request", f"BOOK {show}/{seat}", correlation=rid)
    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(lambda rid: _book_until_final(cfg, token, show, seat, rid), rids))
    for r in results:
        timeline.add("race.response", f"{r['status']} {r['booking_id']}".strip(), correlation=r["request_id"])
    ok = [r for r in results if r["status"] == "OK"]
    read_status, seat_now = _seat_state(cfg, token, show, seat)
    bstatus, brows = _booking_rows(cfg, token, {r["booking_id"] for r in ok})
    checks = [
        ("at most one OK booking for the seat", len(ok) <= 1, f"{len(ok)} OK"),
        (
            "every other request rejected as ALREADY_BOOKED",
            all(r["status"] == "ALREADY_BOOKED" for r in results if r["status"] != "OK"),
            ", ".join(sorted({r["status"] for r in results if r["status"] != "OK"})) or "none",
        ),
        (
            "linearizable read shows seat BOOKED",
            read_status == "OK" and seat_now == "BOOKED",
            f"{read_status}/{seat_now}",
        ),
    ]
    if ok:
        checks.append(
            (
                "winning booking charged exactly once",
                bstatus == "OK" and len(brows) == 1 and brows[0]["charges"] == 1,
                f"{bstatus}: {[r.get('charges') for r in brows]}",
            )
        )
    for name, passed, detail in checks:
        timeline.add("race.check", f"{'PASS' if passed else 'FAIL'} {name} ({detail})")
    return results, checks


def same_key_retry(cfg, token, show, seat, n, timeline):
    """n concurrent submissions of ONE request ID (a client retrying after timeouts)."""
    rid = f"retry-{uuid4().hex[:12]}"
    timeline.add("retry.request", f"{n} concurrent copies of BOOK {show}/{seat}", correlation=rid)
    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(lambda _: _book_until_final(cfg, token, show, seat, rid), range(n)))
    statuses = {r["status"] for r in results}
    bids = {r["booking_id"] for r in results if r["booking_id"]}
    timeline.add("retry.response", f"statuses={sorted(statuses)} booking_ids={sorted(bids)}", correlation=rid)
    bstatus, brows = _booking_rows(cfg, token, bids)
    checks = [
        ("all copies get the same final status", len(statuses) == 1, ", ".join(sorted(statuses))),
        ("at most one booking ID", len(bids) <= 1, f"{len(bids)} id(s)"),
    ]
    if bids:
        checks.append(
            (
                "one booking record, charged exactly once",
                bstatus == "OK" and len(brows) == 1 and brows[0]["charges"] == 1,
                f"{bstatus}: {[r.get('charges') for r in brows]}",
            )
        )
    for name, passed, detail in checks:
        timeline.add("retry.check", f"{'PASS' if passed else 'FAIL'} {name} ({detail})", correlation=rid)
    return results, checks


def watch_failover(cfg, token, old_leader, old_term, timeline, timeout=30.0, interval=0.2):
    """Poll real node status until a different node is leader in a higher term.

    Failover time = first sample where the old leader was not serving as leader
    -> first sample showing a new leader in a higher term."""
    end = time.monotonic() + timeout
    lost_at = None
    while time.monotonic() < end:
        snap = observe(cfg, token)
        t = time.monotonic()
        old_row = next((r for r in snap["rows"] if r["node"] == old_leader), None)
        old_serving = old_row is not None and old_row.get("role") == "leader" and old_row.get("term") == old_term
        if not old_serving and lost_at is None:
            lost_at = t
            timeline.add(
                "failover.leader_lost",
                f"old leader {old_leader} role={old_row.get('role') if old_row else '?'}",
                node=old_leader,
            )
        if lost_at is not None and snap["leader"] and snap["leader"] != old_leader and snap["term"] > old_term:
            ms = round((t - lost_at) * 1000)
            timeline.add(
                "failover.new_leader", f"{snap['leader']} term {snap['term']} after {ms} ms", node=snap["leader"]
            )
            return {"new_leader": snap["leader"], "term": snap["term"], "failover_ms": ms, "snapshot": snap}
        time.sleep(interval)
    timeline.add("failover.timeout", f"no new leader observed within {timeout:.0f}s")
    return None


def wait_caught_up(cfg, token, node_id, timeline, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = observe(cfg, token)
        rows = {r["node"]: r for r in snap["rows"]}
        me, lead = rows.get(node_id), rows.get(snap["leader"]) if snap["leader"] else None
        if me and lead and me["rpc_status"] == "OK" and me["commit_index"] == lead["commit_index"]:
            same = me["log_digest"] == lead["log_digest"] and me["state_digest"] == lead["state_digest"]
            timeline.add(
                "recovery.caught_up",
                f"{node_id} commit={me['commit_index']} log/state digests {'match' if same else 'DIFFER'} leader {lead['node_id']}",
                node=node_id,
            )
            return same, snap
        time.sleep(0.3)
    timeline.add("recovery.timeout", f"{node_id} did not catch up within {timeout:.0f}s", node=node_id)
    return False, None


def attempt_booking(cfg, token, show, seat, rid, timeline, deadline_s=4.0):
    """Single booking attempt used by the no-quorum scenario (same rid on retry)."""
    timeline.add("noquorum.request", f"BOOK {show}/{seat}", correlation=rid)
    res = _book_until_final(cfg, token, show, seat, rid, deadline_s=deadline_s)
    timeline.add("noquorum.response", f"{res['status']} {res['booking_id']}".strip(), correlation=rid)
    return res


def booking_charge_check(cfg, token, booking_id):
    status, rows = _booking_rows(cfg, token, {booking_id})
    return status == "OK" and len(rows) == 1 and rows[0]["charges"] == 1, rows


def summary_markdown(timeline, sections):
    lines = ["# Demo Lab summary", "", f"Generated {now_iso()}", ""]
    for title, checks in sections:
        lines.append(f"## {title}")
        for name, passed, detail in checks:
            lines.append(f"- {'PASS' if passed else 'FAIL'}: {name} ({detail})")
        lines.append("")
    lines.append("## Timeline")
    lines.append("| t (ms) | UTC | kind | node | correlation | detail |")
    lines.append("|---|---|---|---|---|---|")
    for e in timeline.events:
        lines.append(f"| {e['t_ms']} | {e['ts']} | {e['kind']} | {e['node']} | {e['correlation']} | {e['detail']} |")
    return "\n".join(lines) + "\n"


def summary_json(timeline, sections):
    return json.dumps(
        {
            "generated": now_iso(),
            "checks": {t: [{"check": n, "passed": p, "detail": d} for n, p, d in c] for t, c in sections},
            "timeline": timeline.events,
        },
        indent=2,
    )
