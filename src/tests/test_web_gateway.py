"""HTTP gateway (web/gateway.py) against a real in-process three-node Raft cluster."""

import ast
import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.raft_faults import wait_converged
from tests.test_raft_m2 import Cluster
from ui import cluster as ui
from web import gateway as gw

WEB_DIR = Path(gw.__file__).resolve().parent


class Browser:
    """One browser profile: its own cookie jar and CSRF token."""

    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def call(self, method, path, body=None, headers=None, csrf=True):
        hdrs = dict(headers or {})
        data = None
        if method == "POST":
            data = json.dumps(body or {}).encode()
            hdrs.setdefault("Content-Type", "application/json")
            if csrf:
                hdrs.setdefault("X-CSRF-Token", self.csrf)
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            res = self.opener.open(req, timeout=90)
            code = res.status
        except urllib.error.HTTPError as err:
            res, code = err, err.code
        raw = res.read()
        out = json.loads(raw) if "json" in res.headers.get("Content-Type", "") and "attachment" not in res.headers.get("Content-Disposition", "") else raw
        if isinstance(out, dict) and out.get("csrf"):
            self.csrf = out["csrf"]
        return code, out, res.headers

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body, **kw)

    def cookie(self):
        return next((c for c in self.jar if c.name == gw.COOKIE), None)

    def sign_in(self, user, password):
        self.get("/api/session")
        code, out, _ = self.post("/api/login", {"username": user, "password": password})
        assert code == 200 and out["status"] == "OK", out
        return self


@pytest.fixture
def site(tmp_path):
    c = Cluster(tmp_path)
    assert c.leader()
    cfg = ui.Config(nodes=dict(c.addrs), redirect_map={}, services={})
    warm = cfg.client()
    assert warm.login("alice", "wonderland")[0]
    assert wait_converged(c.nodes)
    warm.close()
    srv = gw.build_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield c, cfg, base
    srv.shutdown()
    srv.server_close()
    c.close()


def test_gateway_is_grpc_only():
    """Same rule the Streamlit UI obeys: no direct state, storage or process access."""
    src = (WEB_DIR / "gateway.py").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    forbidden = {"sqlite3", "subprocess", "docker", "os", "ticket_booking.application", "ticket_booking.state_machine",
                 "ticket_booking.payment", "ticket_booking.auth", "ticket_booking.raft_node", "ticket_booking.seed"}
    assert not (mods & forbidden), mods & forbidden
    for bad in ("BookingApp", "os.system", "Popen", "docker.sock"):
        assert bad not in src


def test_static_files_headers_and_host_check(site):
    _, _, base = site
    b = Browser(base)
    code, body, headers = b.get("/")
    assert code == 200 and b"THE NIGHT PROGRAMME" in body.upper()
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert b.get("/fonts/fraunces-latin-var.woff2")[0] == 200
    assert b.get("/../gateway.py")[0] == 404
    assert b.get("/%2e%2e/gateway.py")[0] == 404
    code, out, _ = b.get("/api/session", headers={"Host": "evil.example"})
    assert code == 403 and out["status"] == "FORBIDDEN_HOST"


def test_login_cookie_csrf_and_token_never_exposed(site):
    _, _, base = site
    b = Browser(base)
    code, info, _ = b.get("/api/session")
    assert code == 200 and info["authenticated"] is False and info["csrf"]
    before = b.cookie().value
    assert b.get("/api/shows")[0] == 401

    code, out, _ = b.post("/api/login", {"username": "alice", "password": "wonderland"}, csrf=False)
    assert code == 403 and out["status"] == "CSRF_REJECTED"
    code, out, _ = b.post("/api/login", {"username": "alice", "password": "wonderland"}, headers={"Origin": "http://evil.example"})
    assert code == 403 and out["status"] == "CSRF_REJECTED"

    code, out, _ = b.post("/api/login", {"username": "alice", "password": "nope"})
    assert code == 200 and out["status"] == "AUTH_FAILED" and "password" in out["message"].lower()
    code, out, _ = b.post("/api/signup", {"username": "newperson", "password": "short"})
    assert out["status"] == "WEAK_PASSWORD"

    code, out, headers = b.post("/api/login", {"username": "alice", "password": "wonderland"})
    assert code == 200 and out["status"] == "OK" and out["user"] == "alice"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert b.cookie().value != before  # session ID rotated on login
    code, info, _ = b.get("/api/session")
    assert info["authenticated"] and info["user"] == "alice"
    # the gRPC token lives only in the gateway: not in any body, and not in the cookie value
    token = gw_token(site, b)
    assert token and token not in json.dumps(info) and token not in json.dumps(out) and token != b.cookie().value

    code, out, _ = b.post("/api/logout")
    assert out["status"] == "OK" and out["revoked_on_cluster"] is True
    assert b.get("/api/shows")[0] == 401


def gw_token(site, browser):
    # reach into the running gateway only to prove what the browser never receives
    import gc

    for obj in gc.get_objects():
        if isinstance(obj, gw.SessionStore):
            sess = obj.items.get(browser.cookie().value)
            if sess is not None:
                return sess.token
    return None


def test_browse_quote_book_cancel_roundtrip(site):
    _, _, base = site
    b = Browser(base).sign_in("alice", "wonderland")
    code, out, _ = b.get("/api/shows")
    assert out["status"] == "OK" and len(out["shows"]) == 8
    show = out["shows"][0]
    assert show["seats_status"] == "OK" and show["seats_available"] == show["seats_total"] == 50
    assert {"show_id", "name", "venue", "start_time"} <= set(show)

    code, out, _ = b.get(f"/api/quote?show_id={show['show_id']}&seats=A1,A2")
    assert out["status"] == "OK" and out["quote"]["seat_ids"] == ["A1", "A2"]
    assert out["quote"]["total"] == 2 * out["quote"]["unit_price"]
    assert b.get(f"/api/quote?show_id={show['show_id']}&seats=A1,A2,A3,A4,A5,A6")[0] == 400

    code, out, _ = b.post("/api/bookings", {"show_id": show["show_id"], "seat_ids": ["A9"], "payment": "decline"})
    assert out["status"] == "PAYMENT_FAILED" and not out["uncertain"] and not out["booking_id"]
    assert b.post("/api/bookings", {"show_id": show["show_id"], "seat_ids": ["A9"], "payment": "4111111111111111"})[0] == 400

    code, out, _ = b.post("/api/bookings", {"show_id": show["show_id"], "seat_ids": ["A1", "A2"], "payment": "approve"})
    assert out["status"] == "OK" and out["booking_id"] and out["request_id"] and not out["uncertain"]
    assert out["booking"]["seat_id"] == "A1,A2" and out["booking"]["charges"] == 1 and out["booking"]["amount"] == 100.0
    bid = out["booking_id"]

    _, seats, _ = b.get(f"/api/shows/{show['show_id']}/seats")
    state = {s["seat_id"]: s["status"] for s in seats["seats"]}
    assert state["A1"] == state["A2"] == "BOOKED" and state["A9"] == "AVAILABLE"
    _, shows, _ = b.get("/api/shows")
    assert shows["shows"][0]["seats_available"] == 48

    code, out, _ = b.post(f"/api/bookings/{bid}/cancel")
    assert out["status"] == "OK" and not out["uncertain"]
    _, mine, _ = b.get("/api/bookings")
    row = next(r for r in mine["bookings"] if r["booking_id"] == bid)
    assert row["status"] == "CANCELLED" and row["refunded"] is True and row["charges"] == 1
    _, shows, _ = b.get("/api/shows")
    assert shows["shows"][0]["seats_available"] == 50
    assert b.post(f"/api/bookings/{bid}/cancel")[1]["status"] == "ALREADY_CANCELLED"


def test_sessions_are_isolated_and_conflicts_are_reported(site):
    _, _, base = site
    alice = Browser(base).sign_in("alice", "wonderland")
    # the test cluster seeds only alice; the second user registers through the gateway
    bob = Browser(base)
    bob.get("/api/session")
    code, out, _ = bob.post("/api/signup", {"username": "bob_two", "password": "Builder123"})
    assert code == 200 and out["status"] == "OK" and out["user"] == "bob_two"
    assert alice.cookie().value != bob.cookie().value and alice.csrf != bob.csrf

    _, out, _ = alice.post("/api/bookings", {"show_id": "show-2", "seat_ids": ["A5"], "payment": "approve"})
    assert out["status"] == "OK"
    bid = out["booking_id"]
    assert bob.get("/api/bookings")[1]["bookings"] == []
    assert [r["booking_id"] for r in alice.get("/api/bookings")[1]["bookings"]] == [bid]

    _, out, _ = bob.post("/api/bookings", {"show_id": "show-2", "seat_ids": ["A5", "A6"], "payment": "approve"})
    assert out["status"] == "ALREADY_BOOKED" and not out["uncertain"]
    assert bob.post(f"/api/bookings/{bid}/cancel")[1]["status"] == "FORBIDDEN"
    # bob cannot act with alice's CSRF token, nor alice's session with bob's
    assert bob.post("/api/bookings", {"show_id": "show-2", "seat_ids": ["A7"], "payment": "approve"}, headers={"X-CSRF-Token": alice.csrf})[0] == 403

    alice.post("/api/logout")
    assert alice.get("/api/bookings")[0] == 401
    assert bob.get("/api/session")[1]["user"] == "bob_two"
    assert bob.get("/api/bookings")[0] == 200


def test_uncertain_booking_retry_reuses_request_id(site):
    c, _, base = site
    b = Browser(base).sign_in("alice", "wonderland")
    leader = c.leader()
    followers = [n for n in c.ids if n != leader]
    for n in followers:
        c.stop(n)
    body = {"show_id": "show-3", "seat_ids": ["A11", "A12"], "payment": "approve"}
    _, first, _ = b.post("/api/bookings", body)
    assert first["uncertain"] is True and first["status"] in ui.RETRYABLE and not first["booking_id"]
    rid = first["request_id"]
    pending = b.get("/api/session")[1]["pending_booking"]
    assert pending["request_id"] == rid and pending["seat_ids"] == ["A11", "A12"] and "key" not in pending

    c.restart(followers[0])
    out = first
    for _ in range(12):
        _, out, _ = b.post("/api/bookings", body)
        assert out["request_id"] == rid  # same unresolved action -> same request ID, every time
        if out["status"] == "OK":
            break
        time.sleep(0.5)
    assert out["status"] == "OK" and out["attempts"] >= 2
    assert b.get("/api/session")[1]["pending_booking"] is None
    rows = [r for r in b.get("/api/bookings")[1]["bookings"] if r["show_id"] == "show-3"]
    assert len(rows) == 1 and rows[0]["charges"] == 1 and rows[0]["seat_id"] == "A11,A12"

    # a deliberate new action gets a new request ID
    _, other, _ = b.post("/api/bookings", {"show_id": "show-3", "seat_ids": ["A13"], "payment": "approve"})
    assert other["status"] == "OK" and other["request_id"] != rid


def test_faq_is_async_and_fallback_is_labelled(site):
    _, _, base = site
    b = Browser(base).sign_in("alice", "wonderland")
    assert b.post("/api/faq", {"query": ""})[0] == 400
    code, out, _ = b.post("/api/faq", {"query": "How do I cancel a booking?", "show_id": "show-1"})
    assert code == 200 and out["status"] == "PENDING"
    # booking is not blocked by the question in flight
    assert b.get("/api/quote?show_id=show-1&seats=A1")[1]["status"] == "OK"
    entry = None
    for _ in range(100):
        entry = b.get("/api/faq")[1]["entries"][-1]
        if entry["state"] == "done":
            break
        time.sleep(0.1)
    # the test cluster has no LLM node: the reply must be the labelled verbatim fallback, not an "answer"
    assert entry["status"] == "LLM_UNAVAILABLE"
    assert entry["support"]["fallback"] is True and entry["support"]["generated"] is False
    assert entry["support"]["correlation_id"].startswith("faq-") and "cancellation_policy" in entry["support"]["sources"]
    assert "not AI-generated" in entry["text"]
    b.post("/api/faq", {"query": "What is the capital of France?"})
    for _ in range(100):
        entry = b.get("/api/faq")[1]["entries"][-1]
        if entry["state"] == "done":
            break
        time.sleep(0.1)
    assert entry["status"] == "OUT_OF_DOMAIN"


def test_expired_cluster_session_signs_the_browser_out(site):
    _, cfg, base = site
    b = Browser(base).sign_in("alice", "wonderland")
    token = gw_token(site, b)
    revoke = cfg.client(token)
    assert revoke.logout()[0]
    revoke.close()
    code, out, _ = b.get("/api/shows")
    assert code == 401 and out["status"] == "SESSION_EXPIRED"
    assert b.get("/api/session")[1]["authenticated"] is False


def test_observatory_and_lab_report_evidence(site):
    c, _, base = site
    b = Browser(base).sign_in("alice", "wonderland")
    _, out, _ = b.get("/api/cluster")
    rows = out["snapshot"]["rows"]
    assert sorted(r["role"] for r in rows) == ["follower", "follower", "leader"]
    assert out["nodes_ok"] == 3 and out["majority"] == 2 and out["snapshot"]["leader"]
    assert all({"term", "log_length", "commit_index", "last_applied", "log_digest", "state_digest", "rpc_ms", "sampled_at"} <= set(r) for r in rows)
    assert b.get("/api/analytics")[1]["status"] == "OK"

    _, race, _ = b.post("/api/lab/race", {"show_id": "show-8", "seat_id": "A3", "n": 6})
    assert [r["status"] for r in race["results"]].count("OK") == 1 and all(ch["passed"] for ch in race["checks"])
    _, retry, _ = b.post("/api/lab/retry", {"show_id": "show-7", "seat_id": "A4", "n": 4})
    assert len({r["booking_id"] for r in retry["results"]}) == 1 and all(ch["passed"] for ch in retry["checks"])
    # racing for a seat that is already taken must FAIL the "linearizable read ... BOOKED by a winner" story honestly
    _, again, _ = b.post("/api/lab/race", {"show_id": "show-8", "seat_id": "A3", "n": 3})
    assert all(r["status"] == "ALREADY_BOOKED" for r in again["results"])
    assert b.post("/api/lab/race", {"show_id": "show-8", "seat_id": "A3", "n": 99})[0] == 400

    # a refusal with a healthy majority is NOT counted as a no-quorum pass
    _, nq, _ = b.post("/api/lab/noquorum", {"show_id": "show-8", "seat_id": "A3"})
    assert nq["result"]["status"] == "ALREADY_BOOKED" and nq["nodes_answered"] == 3
    assert not any(ch["passed"] for ch in nq["checks"]) and nq["nq_pending"] is None

    # with a majority down the refusal is evidence; after recovery the same request ID commits once
    leader = c.leader()
    followers = [n for n in c.ids if n != leader]
    for n in followers:
        c.stop(n)
    _, nq, _ = b.post("/api/lab/noquorum", {"show_id": "show-6", "seat_id": "A9"})
    assert nq["result"]["status"] in ui.RETRYABLE and nq["nodes_answered"] < 2
    assert nq["checks"][0]["passed"] and nq["nq_pending"]["refused_without_quorum"]
    rid = nq["nq_pending"]["request_id"]
    c.restart(followers[0])
    for _ in range(8):
        _, nq, _ = b.post("/api/lab/noquorum", {})
        assert nq["result"]["request_id"] == rid
        if nq["result"]["status"] == "OK":
            break
    assert nq["result"]["status"] == "OK" and nq["checks"][0]["passed"]
    assert "same request ID committed after quorum returned" in nq["checks"][0]["check"]

    code, md, headers = b.get("/api/lab/export.md")
    assert code == 200 and "attachment" in headers["Content-Disposition"]
    assert b"# Demo Lab summary" in md and b"PASS" in md and b"| t (ms) |" in md
    exported = json.loads(b.get("/api/lab/export.json")[1])
    assert exported["timeline"] and "No-quorum retry" in exported["checks"]
