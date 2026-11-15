"""The Night Programme - local HTTP gateway.

    browser (web/static)  --HTTP/JSON-->  this gateway  --gRPC-->  Raft app nodes

Browsers cannot speak this project's plain gRPC, so this process translates. It holds no
booking, payment or auth rules of its own: every operation is one of the existing
`ticket_booking.client.Client` calls or a `ui.cluster` helper (the same code the Streamlit
UI uses). It never opens the SQLite files and never starts or stops a process.

Security model (local demo tool):
  * binds to 127.0.0.1 unless told otherwise; requests with a foreign Host header are refused
  * the gRPC session token stays in this process, keyed by an HttpOnly SameSite=Strict cookie
  * every POST needs the per-session CSRF token in X-CSRF-Token and a same-origin Origin
  * one fresh gRPC client per request - nothing mutable is shared between users
  * passwords and tokens are never logged or echoed back

Run from `src`:   .\\.venv\\Scripts\\python.exe -m web.gateway
"""

import argparse
import json
import logging
import re
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from hmac import compare_digest
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import grpc  # noqa: E402

from ui import cluster  # noqa: E402

log = logging.getLogger("night_programme.gateway")

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "np_session"
MAX_BODY = 64 * 1024
MAX_SEATS = 5  # mirrors the server rule so the UI can stop early; the server still enforces it
SESSION_IDLE_S = 2 * 60 * 60
MAX_SESSIONS = 500
FAQ_TIMEOUT_S = 75.0  # > server TB_LLM_TIMEOUT (60 s) + follower forwarding margin
CHAT_KEEP = 30

# Simulated payment choices -> the repository's mock card numbers (see ticket_booking/payment.py:
# numbers starting or ending in 0000 are declined). The browser never sends a card number.
CARDS = {"approve": "4242424242424242", "decline": "4000000000000000"}

# Seeded demo accounts documented in the repository README (ticket_booking/auth.py).
DEMO_ACCOUNTS = [
    {"username": "alice", "password": "wonderland"},
    {"username": "bob", "password": "builder"},
]

ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".ico": "image/x-icon",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
)


class SessionExpired(Exception):
    """The cluster no longer accepts this session's gRPC token."""


class BadRequest(Exception):
    def __init__(self, message, status="INVALID_REQUEST", code=400):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


class Session:
    def __init__(self):
        self.sid = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.token = None
        self.user = None
        self.seen = time.monotonic()
        self.lock = threading.Lock()
        self.lab_lock = threading.Lock()
        self.reset_user_state()

    def reset_user_state(self):
        self.pending_booking = None
        self.pending_cancel = {}
        self.chat = []
        self.timeline = cluster.Timeline()
        self.sections = []
        self.failover = {}
        self.nq_pending = None


class SessionStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def get(self, sid):
        now = time.monotonic()
        with self.lock:
            sess = self.items.get(sid) if sid else None
            if sess is not None and now - sess.seen > SESSION_IDLE_S:
                del self.items[sid]
                sess = None
            if sess is not None:
                sess.seen = now
            return sess

    def create(self):
        sess = Session()
        with self.lock:
            if len(self.items) >= MAX_SESSIONS:
                oldest = min(self.items.values(), key=lambda s: s.seen)
                del self.items[oldest.sid]
            self.items[sess.sid] = sess
        return sess

    def rotate(self, sess):
        """New cookie value + CSRF token + empty per-user state (login, logout, expiry)."""
        with self.lock:
            self.items.pop(sess.sid, None)
            sess.sid = secrets.token_urlsafe(32)
            sess.csrf = secrets.token_urlsafe(32)
            sess.token, sess.user = None, None
            sess.reset_user_state()
            self.items[sess.sid] = sess
        return sess


def _clean_id(value, what):
    value = str(value or "").strip()
    if not ID_RE.match(value):
        raise BadRequest(f"{what} is missing or malformed.")
    return value


def _seat_list(raw):
    if isinstance(raw, str):
        raw = [bit for bit in raw.split(",")]
    if not isinstance(raw, list):
        raise BadRequest("seat_ids must be a list.")
    seats = []
    for bit in raw:
        seat = _clean_id(bit, "Seat ID")
        if seat not in seats:
            seats.append(seat)
    if not 1 <= len(seats) <= MAX_SEATS:
        raise BadRequest(f"Select between 1 and {MAX_SEATS} seats.")
    return seats


class Gateway:
    def __init__(self, cfg=None):
        self.cfg = cfg or cluster.Config()
        self.store = SessionStore()
        self.majority = len(self.cfg.nodes) // 2 + 1

    # ------------------------------------------------------------ helpers

    def client(self, sess, timeout=3.0, max_retries=3):
        """A fresh gRPC client carrying only this session's token; the caller closes it."""
        return self.cfg.client(sess.token, timeout=timeout, max_retries=max_retries)

    @staticmethod
    def _live(status):
        if status == "AUTH_FAILED":
            raise SessionExpired()
        return status

    # ------------------------------------------------------------ session / auth

    def session_info(self, sess, **_):
        pending = sess.pending_booking
        return 200, {
            "authenticated": bool(sess.token),
            "user": sess.user,
            "csrf": sess.csrf,
            "max_seats": MAX_SEATS,
            "nodes": self.cfg.nodes,
            "demo_accounts": DEMO_ACCOUNTS,
            "faq_timeout_s": FAQ_TIMEOUT_S,
            "pending_booking": {k: v for k, v in pending.items() if k != "key"} if pending else None,
        }

    def _auth(self, sess, body, mode):
        uname = str(body.get("username", "")).strip()
        pw = body.get("password", "")
        if not isinstance(pw, str) or not uname or not pw:
            raise BadRequest("Enter a username and a password.")
        if len(uname) > 64 or len(pw) > 256:
            raise BadRequest("Username or password is too long.")
        c = self.cfg.client()
        try:
            ok, msg = c.login(uname, pw) if mode == "login" else c.signup(uname, pw)
            token = c.token
        finally:
            c.close()
        if ok:
            self.store.rotate(sess)
            sess.token, sess.user = token, uname
            return 200, {"status": "OK", "user": uname, "csrf": sess.csrf}
        m = re.search(r"failed: (.*) \(status: ([A-Z_]+)\)\s*$", msg, re.S)
        status, text = (m.group(2), m.group(1)) if m else ("ERROR", msg)
        out = {"status": status, "message": text}
        if status in cluster.RETRYABLE:
            # an unanswered signup may still have been committed; say so instead of guessing
            out["uncertain"] = mode == "signup"
        return 200, out

    def login(self, sess, body, **_):
        return self._auth(sess, body, "login")

    def signup(self, sess, body, **_):
        return self._auth(sess, body, "signup")

    def logout(self, sess, **_):
        revoked, detail = False, ""
        if sess.token:
            c = self.client(sess)
            try:
                revoked, detail = c.logout()
            finally:
                c.close()
        self.store.rotate(sess)
        return 200, {"status": "OK", "revoked_on_cluster": bool(revoked), "message": detail, "csrf": sess.csrf}

    # ------------------------------------------------------------ programme

    def shows(self, sess, **_):
        c = self.client(sess)
        try:
            status, shows, msg = c.get_shows()
        finally:
            c.close()
        self._live(status)
        if status != "OK":
            return 200, {"status": status, "message": msg, "shows": []}

        def count(show):
            c2 = self.client(sess, max_retries=1)
            try:
                st, seats, _ = c2.get_seats(show["show_id"])
            finally:
                c2.close()
            if st == "OK":
                show["seats_total"] = len(seats)
                show["seats_available"] = sum(1 for s in seats if s["status"] == "AVAILABLE")
            show["seats_status"] = st
            return show

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(shows)))) as pool:
            shows = list(pool.map(count, shows))
        return 200, {"status": "OK", "shows": shows, "read_at": cluster.now_iso()}

    def seats(self, sess, match, **_):
        show_id = _clean_id(match.group(1), "Show ID")
        c = self.client(sess)
        try:
            status, seats, msg = c.get_seats(show_id)
        finally:
            c.close()
        self._live(status)
        return 200, {"status": status, "message": msg, "show_id": show_id, "seats": seats, "read_at": cluster.now_iso()}

    def quote(self, sess, query, **_):
        show_id = _clean_id(query.get("show_id"), "Show ID")
        seats = _seat_list(query.get("seats", ""))
        c = self.client(sess)
        try:
            status, quote, msg = c.get_quote(show_id, seats)
        finally:
            c.close()
        self._live(status)
        return 200, {"status": status, "message": msg, "quote": quote}

    # ------------------------------------------------------------ bookings

    def book(self, sess, body, **_):
        show_id = _clean_id(body.get("show_id"), "Show ID")
        seats = _seat_list(body.get("seat_ids"))
        payment = body.get("payment")
        if payment not in CARDS:
            raise BadRequest("Choose one of the simulated payment outcomes.")
        key = (show_id, tuple(seats), payment)
        with sess.lock:
            pending = sess.pending_booking
            if pending and pending["key"] == key:
                # same unresolved action -> same request ID (idempotent retry, no double charge)
                pending["attempts"] += 1
            else:
                # a deliberate new action -> a new request ID
                pending = sess.pending_booking = {
                    "key": key,
                    "request_id": uuid4().hex,
                    "show_id": show_id,
                    "seat_ids": seats,
                    "payment": payment,
                    "attempts": 1,
                    "last_status": "",
                }
            rid, attempts = pending["request_id"], pending["attempts"]
        c = self.client(sess)
        booking = None
        try:
            try:
                status, bid, msg = c.book_seat(show_id, ",".join(seats), card=CARDS[payment], req_id=rid)
            except grpc.RpcError as err:
                status, bid, msg = "RPC_ERROR", "", f"gRPC call failed ({err.code().name}); the outcome is unknown."
            if status == "OK":
                bstatus, rows, _ = c.get_my_bookings()
                if bstatus == "OK":
                    booking = next((r for r in rows if r["booking_id"] == bid), None)
        finally:
            c.close()
        uncertain = status in cluster.RETRYABLE or status == "RPC_ERROR"
        with sess.lock:
            if sess.pending_booking is pending:
                if uncertain:
                    pending["last_status"] = status
                else:
                    sess.pending_booking = None
        self._live(status)
        return 200, {
            "status": status,
            "message": msg,
            "booking_id": bid,
            "request_id": rid,
            "attempts": attempts,
            "uncertain": uncertain,
            "booking": booking,
        }

    def discard_pending(self, sess, **_):
        with sess.lock:
            sess.pending_booking = None
        return 200, {"status": "OK"}

    def my_bookings(self, sess, **_):
        c = self.client(sess)
        try:
            status, rows, msg = c.get_my_bookings()
        finally:
            c.close()
        self._live(status)
        return 200, {"status": status, "message": msg, "bookings": rows, "read_at": cluster.now_iso()}

    def cancel(self, sess, match, **_):
        bid = _clean_id(match.group(1), "Booking ID")
        with sess.lock:
            rid = sess.pending_cancel.setdefault(bid, uuid4().hex)
        c = self.client(sess)
        try:
            try:
                status, _, msg = c.cancel_seat(bid, req_id=rid)
            except grpc.RpcError as err:
                status, msg = "RPC_ERROR", f"gRPC call failed ({err.code().name}); the outcome is unknown."
        finally:
            c.close()
        uncertain = status in cluster.RETRYABLE or status == "RPC_ERROR"
        if not uncertain:
            with sess.lock:
                sess.pending_cancel.pop(bid, None)
        self._live(status)
        return 200, {"status": status, "message": msg, "booking_id": bid, "request_id": rid, "uncertain": uncertain}

    # ------------------------------------------------------------ FAQ

    @staticmethod
    def _chat_view(entry):
        out = {k: v for k, v in entry.items() if k != "started"}
        if entry["state"] == "pending":
            out["elapsed_ms"] = round((time.monotonic() - entry["started"]) * 1000)
        return out

    def faq_list(self, sess, **_):
        with sess.lock:
            return 200, {"entries": [self._chat_view(e) for e in sess.chat]}

    def faq_ask(self, sess, body, **_):
        q = str(body.get("query", "")).strip()
        if not q or len(q) > 500:
            raise BadRequest("Ask a question of 1 to 500 characters.")
        show_id = str(body.get("show_id", "") or "").strip()
        if show_id:
            show_id = _clean_id(show_id, "Show ID")
        with sess.lock:
            if any(e["state"] == "pending" for e in sess.chat):
                raise BadRequest("A question is still waiting for its answer.", "FAQ_BUSY", 409)
            entry = {
                "id": uuid4().hex[:12],
                "query": q,
                "show_id": show_id,
                "state": "pending",
                "asked_at": cluster.now_iso(),
                "started": time.monotonic(),
            }
            sess.chat.append(entry)
            del sess.chat[:-CHAT_KEEP]
        token = sess.token

        def work():
            # own client, own thread: a slow or dead LLM never blocks booking requests
            c = self.cfg.client(token, timeout=FAQ_TIMEOUT_S, max_retries=1)
            try:
                status, text, support = c.ask_faq_detailed(q, show_id=show_id)
            except Exception as err:  # noqa: BLE001 - report, never crash the worker silently
                log.warning("FAQ call failed: %s", type(err).__name__)
                status, text, support = "GATEWAY_ERROR", "The gateway could not complete the FAQ call.", {}
            finally:
                c.close()
            with sess.lock:
                entry.update(
                    state="done",
                    status=status,
                    text=text,
                    support=support,
                    elapsed_ms=round((time.monotonic() - entry["started"]) * 1000),
                )

        threading.Thread(target=work, name="faq", daemon=True).start()
        return 200, {"status": "PENDING", "id": entry["id"]}

    def faq_clear(self, sess, **_):
        with sess.lock:
            sess.chat = [e for e in sess.chat if e["state"] == "pending"]
        return 200, {"status": "OK"}

    # ------------------------------------------------------------ observatory

    def _observe(self, sess):
        snap = cluster.observe(self.cfg, sess.token)
        answered = [r for r in snap["rows"] if r["reachable"]]
        if answered and all(r["rpc_status"] == "AUTH_FAILED" for r in answered):
            raise SessionExpired()
        return snap

    def _hints(self):
        return {n: {"stop": self.cfg.stop_hint(n), "start": self.cfg.start_hint(n)} for n in self.cfg.nodes}

    def cluster_status(self, sess, **_):
        snap = self._observe(sess)
        rep = cluster.consistency_report(snap)
        ok = sum(1 for r in snap["rows"] if r["rpc_status"] == "OK")
        return 200, {
            "status": "OK",
            "snapshot": snap,
            "consistency": rep,
            "nodes_total": len(snap["rows"]),
            "nodes_ok": ok,
            "majority": self.majority,
        }

    def analytics(self, sess, **_):
        c = self.client(sess)
        try:
            status, rows, msg = c.get_booking_analytics()
        finally:
            c.close()
        self._live(status)
        return 200, {"status": status, "message": msg, "rows": rows, "read_at": cluster.now_iso()}

    # ------------------------------------------------------------ demo lab

    @staticmethod
    def _checks(checks):
        return [{"check": n, "passed": bool(p), "detail": d} for n, p, d in checks]

    def _lab_view(self, sess):
        return {
            "timeline": list(sess.timeline.events),
            "sections": [{"title": t, "checks": self._checks(c)} for t, c in sess.sections],
            "failover": dict(sess.failover),
            "nq_pending": dict(sess.nq_pending) if sess.nq_pending else None,
        }

    def lab_state(self, sess, **_):
        snap = self._observe(sess)
        out = self._lab_view(sess)
        out.update(
            status="OK",
            hints=self._hints(),
            leader=snap["leader"],
            term=snap["term"],
            sampled_at=snap["sampled_at"],
            nodes=[{"node": r["node"], "address": r["address"], "role": r.get("role"), "rpc_status": r["rpc_status"]} for r in snap["rows"]],
            majority=self.majority,
        )
        return 200, out

    def _lab_run(self, sess, fn):
        if not sess.lab_lock.acquire(blocking=False):
            raise BadRequest("Another Demo Lab scenario is still running in this session.", "LAB_BUSY", 409)
        try:
            return fn()
        finally:
            sess.lab_lock.release()

    @staticmethod
    def _lab_target(body):
        show = _clean_id(body.get("show_id"), "Show ID")
        seat = _clean_id(body.get("seat_id"), "Seat ID")
        return show, seat

    @staticmethod
    def _lab_n(body):
        try:
            n = int(body.get("n", 0))
        except (TypeError, ValueError):
            n = 0
        if not 2 <= n <= 20:
            raise BadRequest("Concurrency must be between 2 and 20.")
        return n

    def lab_race(self, sess, body, **_):
        show, seat = self._lab_target(body)
        n = self._lab_n(body)

        def run():
            results, checks = cluster.seat_race(self.cfg, sess.token, show, seat, n, sess.timeline)
            if results and all(r["status"] == "AUTH_FAILED" for r in results):
                raise SessionExpired()
            sess.sections.append((f"Seat race {show}/{seat}", checks))
            return 200, {"status": "OK", "results": results, "checks": self._checks(checks), **self._lab_view(sess)}

        return self._lab_run(sess, run)

    def lab_retry(self, sess, body, **_):
        show, seat = self._lab_target(body)
        n = self._lab_n(body)

        def run():
            results, checks = cluster.same_key_retry(self.cfg, sess.token, show, seat, n, sess.timeline)
            if results and all(r["status"] == "AUTH_FAILED" for r in results):
                raise SessionExpired()
            sess.sections.append((f"Same-key retry {show}/{seat}", checks))
            return 200, {"status": "OK", "results": results, "checks": self._checks(checks), **self._lab_view(sess)}

        return self._lab_run(sess, run)

    def lab_failover_watch(self, sess, **_):
        def run():
            snap = self._observe(sess)
            old, term = snap["leader"], snap["term"]
            if not old:
                return 200, {"status": "NO_LEADER_OBSERVED", "message": "No leader is currently observed; nothing to watch.", **self._lab_view(sess)}
            sess.timeline.add("failover.watch", f"watching leader {old} term {term}", node=old)
            res = cluster.watch_failover(self.cfg, sess.token, old, term, sess.timeline)
            if res:
                sess.failover = {"old": old, "old_term": term, "new_leader": res["new_leader"], "term": res["term"], "failover_ms": res["failover_ms"]}
                detail = f"{old} (term {term}) -> {res['new_leader']} (term {res['term']}), {res['failover_ms']} ms"
                checks = [("a different node became leader in a higher term", True, detail)]
                status = "OK"
            else:
                checks = [("a different node became leader in a higher term", False, f"{old} (term {term}) was still the only leader observed for 30 s")]
                status = "NOT_OBSERVED"
            sess.sections.append(("Leader failover", checks))
            return 200, {"status": status, "checks": self._checks(checks), **self._lab_view(sess)}

        return self._lab_run(sess, run)

    def lab_failover_catchup(self, sess, **_):
        old = sess.failover.get("old")
        if not old:
            raise BadRequest("Run the failover watch first.", "NO_FAILOVER", 409)

        def run():
            same, snap = cluster.wait_caught_up(self.cfg, sess.token, old, sess.timeline)
            if snap is None:
                detail = "node did not reach the leader's commit index within 30 s"
            else:
                detail = "commit index equal; log and state digests " + ("identical" if same else "DIFFER")
            checks = [(f"{old} caught up with identical log and state digests", bool(same), detail)]
            sess.sections.append(("Old-leader catch-up", checks))
            return 200, {"status": "OK" if same else "NOT_OBSERVED", "checks": self._checks(checks), **self._lab_view(sess)}

        return self._lab_run(sess, run)

    def lab_noquorum(self, sess, body, **_):
        def run():
            pending = sess.nq_pending
            if pending is None:
                show, seat = self._lab_target(body)
                pending = sess.nq_pending = {"request_id": f"nq-{uuid4().hex[:12]}", "show_id": show, "seat_id": seat, "attempts": 0, "refused_without_quorum": False}
            rid, show, seat = pending["request_id"], pending["show_id"], pending["seat_id"]
            before = cluster.observe(self.cfg, sess.token)
            up = sum(1 for r in before["rows"] if r["reachable"])
            total = len(before["rows"])
            sess.timeline.add("noquorum.observe", f"{up}/{total} nodes answered before the attempt (majority = {self.majority})", correlation=rid)
            pending["attempts"] += 1
            res = cluster.attempt_booking(self.cfg, sess.token, show, seat, rid, sess.timeline)
            status = res["status"]
            if status == "AUTH_FAILED":
                raise SessionExpired()
            if status == "OK":
                ok, rows = cluster.booking_charge_check(self.cfg, sess.token, res["booking_id"])
                detail = f"booking {res['booking_id']}: charges={[r.get('charges') for r in rows]}, attempts={pending['attempts']}"
                if pending["refused_without_quorum"]:
                    title = "No-quorum retry"
                    checks = [("same request ID committed after quorum returned: one booking, one charge", ok, detail)]
                else:
                    title = "Booking with quorum present"
                    checks = [("one booking, one charge (no quorum loss was observed for this request)", ok, detail)]
                sess.nq_pending = None
            elif status in cluster.RETRYABLE:
                title = "No-quorum attempt"
                if up < self.majority:
                    pending["refused_without_quorum"] = True
                    checks = [("booking not acknowledged while a majority was unreachable", True, f"{status}; {up}/{total} nodes answered")]
                else:
                    checks = [(
                        "booking not acknowledged while a majority was unreachable",
                        False,
                        f"INCONCLUSIVE - {status}, but {up}/{total} nodes answered, so a majority existed; this refusal is not evidence of quorum loss",
                    )]
            else:
                title = "No-quorum attempt"
                checks = [("request reached a final status the scenario expects", False, f"unexpected final status {status}: {res['message']}")]
                sess.nq_pending = None
            for name, passed, detail in checks:
                sess.timeline.add("noquorum.check", f"{'PASS' if passed else 'FAIL'} {name} ({detail})", correlation=rid)
            sess.sections.append((title, checks))
            return 200, {"status": "OK", "result": res, "nodes_answered": up, "nodes_total": total, "checks": self._checks(checks), **self._lab_view(sess)}

        return self._lab_run(sess, run)

    def lab_noquorum_reset(self, sess, **_):
        sess.nq_pending = None
        return 200, {"status": "OK", **self._lab_view(sess)}

    def lab_clear(self, sess, **_):
        sess.timeline = cluster.Timeline()
        sess.sections = []
        return 200, {"status": "OK", **self._lab_view(sess)}

    def lab_export(self, sess, match, **_):
        kind = match.group(1)
        if kind == "md":
            body, ctype = cluster.summary_markdown(sess.timeline, sess.sections), "text/markdown; charset=utf-8"
        else:
            body, ctype = cluster.summary_json(sess.timeline, sess.sections), "application/json; charset=utf-8"
        return 200, ("download", f"demo-lab-summary.{kind}", ctype, body.encode())


# (method, path regex, Gateway method name, needs a signed-in session)
ROUTES = [
    ("GET", r"/api/session", "session_info", False),
    ("POST", r"/api/login", "login", False),
    ("POST", r"/api/signup", "signup", False),
    ("POST", r"/api/logout", "logout", False),
    ("GET", r"/api/shows", "shows", True),
    ("GET", r"/api/shows/([^/]+)/seats", "seats", True),
    ("GET", r"/api/quote", "quote", True),
    ("GET", r"/api/bookings", "my_bookings", True),
    ("POST", r"/api/bookings", "book", True),
    ("POST", r"/api/bookings/pending/discard", "discard_pending", True),
    ("POST", r"/api/bookings/([^/]+)/cancel", "cancel", True),
    ("GET", r"/api/faq", "faq_list", True),
    ("POST", r"/api/faq", "faq_ask", True),
    ("POST", r"/api/faq/clear", "faq_clear", True),
    ("GET", r"/api/cluster", "cluster_status", True),
    ("GET", r"/api/analytics", "analytics", True),
    ("GET", r"/api/lab", "lab_state", True),
    ("POST", r"/api/lab/race", "lab_race", True),
    ("POST", r"/api/lab/retry", "lab_retry", True),
    ("POST", r"/api/lab/failover/watch", "lab_failover_watch", True),
    ("POST", r"/api/lab/failover/catchup", "lab_failover_catchup", True),
    ("POST", r"/api/lab/noquorum", "lab_noquorum", True),
    ("POST", r"/api/lab/noquorum/reset", "lab_noquorum_reset", True),
    ("POST", r"/api/lab/clear", "lab_clear", True),
    ("GET", r"/api/lab/export\.(md|json)", "lab_export", True),
]
ROUTES = [(m, re.compile(rx + r"/?$"), fn, auth) for m, rx, fn, auth in ROUTES]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NightProgramme"
    sys_version = ""
    gw = None  # set by build_server
    allowed_hosts = frozenset()

    # ---- plumbing

    def log_message(self, fmt, *args):  # path only: no bodies, cookies, tokens or passwords
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code, ctype, body, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, payload, extra=()):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self._send(code, "application/json; charset=utf-8", body, (("Cache-Control", "no-store"), *extra))

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return name in self.allowed_hosts

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        if STATIC not in target.parents or not target.is_file() or target.suffix not in TYPES:
            return self._send(404, "text/plain; charset=utf-8", b"Not found")
        cache = "public, max-age=31536000, immutable" if target.suffix == ".woff2" else "no-cache"
        self._send(200, TYPES[target.suffix], target.read_bytes(), (("Cache-Control", cache),))

    def _cookie_sid(self):
        try:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001 - a malformed cookie header is just "no session"
            return None
        return jar[COOKIE].value if COOKIE in jar else None

    # ---- dispatch

    def _handle(self):
        if not self._host_ok():
            return self._json(403, {"status": "FORBIDDEN_HOST", "message": "This gateway only answers on its local address."})
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            if self.command not in ("GET", "HEAD"):
                return self._json(405, {"status": "METHOD_NOT_ALLOWED", "message": "Method not allowed."})
            return self._static(url.path)

        route = match = None
        for method, rx, fn, auth in ROUTES:
            m = rx.match(url.path)
            if m and method == self.command:
                route, match = (fn, auth), m
                break
        if route is None:
            return self._json(404, {"status": "NOT_FOUND", "message": "Unknown API route."})

        sid = self._cookie_sid()
        sess = self.gw.store.get(sid) or self.gw.store.create()
        body = {}
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
                return self._json(403, {"status": "CSRF_REJECTED", "message": "Cross-origin request refused."})
            sent = self.headers.get("X-CSRF-Token") or ""
            if not compare_digest(sent.encode(), sess.csrf.encode()):
                return self._finish(sess, sid, 403, {"status": "CSRF_REJECTED", "message": "Missing or stale CSRF token; reload the page."})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                return self._finish(sess, sid, 413, {"status": "INVALID_REQUEST", "message": "Request body too large."})
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode() or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None
            if not isinstance(body, dict):
                return self._finish(sess, sid, 400, {"status": "INVALID_REQUEST", "message": "Body must be a JSON object."})

        fn, needs_auth = route
        if needs_auth and not sess.token:
            return self._finish(sess, sid, 401, {"status": "AUTH_REQUIRED", "message": "Sign in to continue."})
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            code, payload = getattr(self.gw, fn)(sess=sess, match=match, query=query, body=body)
        except SessionExpired:
            self.gw.store.rotate(sess)
            code, payload = 401, {"status": "SESSION_EXPIRED", "message": "Your session expired on the cluster. Sign in again."}
        except BadRequest as err:
            code, payload = err.code, {"status": err.status, "message": err.message}
        except grpc.RpcError as err:
            code, payload = 502, {"status": "RPC_ERROR", "message": f"gRPC call failed ({err.code().name})."}
        except Exception:  # noqa: BLE001 - never leak a traceback to the browser
            log.exception("gateway error on %s %s", self.command, url.path)
            code, payload = 500, {"status": "GATEWAY_ERROR", "message": "The gateway hit an unexpected error; see its console."}
        self._finish(sess, sid, code, payload)

    def _finish(self, sess, sid, code, payload):
        extra = []
        if sess.sid != sid:
            extra.append(("Set-Cookie", f"{COOKIE}={sess.sid}; Path=/; HttpOnly; SameSite=Strict"))
        if isinstance(payload, tuple) and payload[0] == "download":
            _, name, ctype, data = payload
            extra += [("Content-Disposition", f'attachment; filename="{name}"'), ("Cache-Control", "no-store")]
            return self._send(code, ctype, data, extra)
        self._json(code, payload, extra)

    do_GET = do_POST = do_HEAD = _handle


def build_server(cfg=None, host="127.0.0.1", port=8600):
    gw = Gateway(cfg)
    hosts = {"127.0.0.1", "localhost", "[::1]", host.lower()}
    handler = type("BoundHandler", (Handler,), {"gw": gw, "allowed_hosts": frozenset(hosts)})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.gateway = gw
    return srv


def main():
    parser = argparse.ArgumentParser(description="The Night Programme - HTTP gateway + web UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--nodes", default="", help="node-id=host:port,... (default: TB_NODES or 127.0.0.1:50051-50053)")
    parser.add_argument("--redirect-map", default="", help="advertised=reachable,... (default: TB_REDIRECT_MAP)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = cluster.Config(
        nodes=cluster.parse_map(args.nodes) if args.nodes else None,
        redirect_map=cluster.parse_map(args.redirect_map) if args.redirect_map else None,
    )
    if not cfg.nodes:
        parser.error("no cluster nodes configured")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("Binding to %s exposes this demo gateway (plain HTTP, no TLS) beyond this machine.", args.host)
    srv = build_server(cfg, args.host, args.port)
    shown = "localhost" if args.host in ("127.0.0.1", "::1") else args.host
    print(f"The Night Programme: http://{shown}:{srv.server_address[1]}/")
    print("gRPC nodes: " + ", ".join(f"{k}={v}" for k, v in cfg.nodes.items()))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
