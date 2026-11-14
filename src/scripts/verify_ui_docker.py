"""Drive every Streamlit UI flow headlessly against the running Docker cluster.

Run from the directory holding docker-compose.yml, with the stack up:

    python src/scripts/verify_ui_docker.py

The real ui/app.py is executed with streamlit.testing.AppTest; it reaches the cluster only
through gRPC on 127.0.0.1:50051-50053. Topology changes use fixed `docker compose stop|start`
commands. Prints a Markdown log; exits non-zero if any check fails.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
os.environ["TB_NODES"] = "node-1=127.0.0.1:50051,node-2=127.0.0.1:50052,node-3=127.0.0.1:50053"
os.environ["TB_REDIRECT_MAP"] = ""
os.environ["TB_COMPOSE_SERVICES"] = "node-1=app-1,node-2=app-2,node-3=app-3"

from streamlit.testing.v1 import AppTest  # noqa: E402

from ui import cluster as ui  # noqa: E402

SERVICE = {"node-1": "app-1", "node-2": "app-2", "node-3": "app-3"}
FAILED = []


def out(line=""):
    print(line, flush=True)


def check(name, ok, detail=""):
    out(f"- {'PASS' if ok else 'FAIL'}: {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def compose(*args):
    r = subprocess.run(["docker", "compose", *args], capture_output=True, text=True, timeout=180)
    out(f"  `docker compose {' '.join(args)}` -> exit {r.returncode}")


def wait_healthy(services, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = subprocess.run(["docker", "compose", "ps", "--format", "{{.Service}} {{.Health}}"], capture_output=True, text=True)
        states = dict(line.split(" ", 1) for line in r.stdout.splitlines() if " " in line)
        if all(states.get(s) == "healthy" for s in services):
            return True
        time.sleep(2)
    return False


def texts(at, kind):
    return " | ".join(e.value for e in getattr(at, kind))


def page_text(at):
    parts = []
    for kind in ("title", "header", "subheader", "markdown", "caption", "success", "warning", "error", "info", "text", "code"):
        parts += [str(e.value) for e in getattr(at, kind)]
    return "\n".join(parts)


def new_app():
    at = AppTest.from_file(str(SRC / "ui" / "app.py"), default_timeout=120)
    at.run()
    return at


def sidebar_radio(at, label):
    return next(r for r in at.sidebar.radio if r.label == label)


def button(at, label):
    return next(b for b in at.button if b.label == label)


def auth(at, user, pw, mode="Log in"):
    at.sidebar.text_input[0].input(user)
    at.sidebar.text_input[1].input(pw)
    sidebar_radio(at, "Action").set_value(mode)
    next(b for b in at.sidebar.button if b.label == "Continue").click().run()


def view(at, name):
    sidebar_radio(at, "View").set_value(name).run()


def select_seats(at, show_id, seats):
    for cb in at.checkbox:
        if cb.key and cb.key.startswith(f"seat-{show_id}-"):
            cb.uncheck()
    for s in seats:
        at.checkbox(key=f"seat-{show_id}-{s}").check()
    at.run()


def free_seats(at, show_id, n):
    return [cb.label for cb in at.checkbox if cb.key and cb.key.startswith(f"seat-{show_id}-") and not cb.disabled][:n]


def free_slot(token, prefer):
    c = ui.Config().client(token)
    try:
        for show in [prefer] + [x for x in ["show-1", "show-2", "show-3", "show-4", "show-5", "show-6", "show-7", "show-8"] if x != prefer]:
            _, seats, _ = c.get_seats(show)
            free = [s["seat_id"] for s in seats if s["status"] == "AVAILABLE"]
            if free:
                return show, free[0]
    finally:
        c.close()
    raise SystemExit("no free seat left in any show")


def fill(at, key, prefer):
    show, seat = free_slot(at.session_state["token"], prefer)
    at.text_input(key=f"{key}-show").input(show)
    at.text_input(key=f"{key}-seat").input(seat)


def main():
    out(f"# UI verification against Docker ({time.strftime('%Y-%m-%d %H:%M:%S %z')})")
    out()
    out("## Auth and session")
    at = new_app()
    check("logged-out landing page asks to log in", "Log in or sign up" in texts(at, "info"))
    user = "ui" + uuid4().hex[:8]
    auth(at, user, "short", mode="Sign up")
    check("invalid signup (weak password) rejected", at.session_state["token"] is None and "8 characters" in texts(at.sidebar, "error"), texts(at.sidebar, "error"))
    auth(at, user, "GoodPass123", mode="Sign up")
    check("signup succeeds and opens a session", at.session_state["user"] == user and bool(at.session_state["token"]))
    token = at.session_state["token"]
    check("new user sees empty My Bookings", "No bookings yet." in page_text(at))
    view(at, "Cluster Observatory")
    view(at, "Booking")
    check("session persists across views/reruns", at.session_state["token"] == token)
    next(b for b in at.sidebar.button if b.label == "Log out").click().run()
    check("logout clears the session", at.session_state["token"] is None and "Log in or sign up" in texts(at, "info"))
    auth(at, user, "wrong-password1")
    check("wrong password rejected", at.session_state["token"] is None, texts(at.sidebar, "error"))
    auth(at, user, "GoodPass123")
    check("login succeeds", at.session_state["user"] == user)
    check("session token never rendered on the page", at.session_state["token"] not in page_text(at))

    out()
    out("## Booking view")
    show_label = at.selectbox[0].options[0]
    check("show catalogue lists 8 shows", len(at.selectbox[0].options) == 8, show_label)
    check("seat map rendered as a linearizable read", "confirmed by a Raft majority" in texts(at, "caption"))
    check("seat grid has 50 labelled seats", len([c for c in at.checkbox if c.key and c.key.startswith("seat-show-1-")]) == 50)
    picks = free_seats(at, "show-1", 6)
    select_seats(at, "show-1", picks)
    check("selecting 6 seats is rejected (limit 5)", "limit is 5" in texts(at, "error") and not any(b.label == "Pay and book" for b in at.button))
    select_seats(at, "show-1", picks[:2])
    md = texts(at, "markdown")
    check("server-authoritative quote shown (2 x 50.00 = 100.00)", "Server quote: 2 x 50.00 = **100.00**" in md, md[:120])
    next(r for r in at.radio if r.label.startswith("Mock payment")).set_value("Mock card - declines (4000 0000 0000 0000)").run()
    button(at, "Pay and book").click().run()
    check("declining mock card -> PAYMENT_FAILED, no booking", "PAYMENT_FAILED" in texts(at, "warning") and "No bookings yet." in page_text(at), texts(at, "warning"))
    next(r for r in at.radio if r.label.startswith("Mock payment")).set_value("Mock card - approves (4242 4242 4242 4242)").run()
    button(at, "Pay and book").click().run()
    ok_msg = texts(at, "success")
    check("approving mock card -> confirmation with booking ID", "Booking confirmed. Booking ID" in ok_msg, ok_msg[:140])
    seats_str = ",".join(picks[:2])
    rows = [m for m in at.markdown if f"seats {seats_str}" in m.value]
    check("My Bookings shows the booking with exactly one charge", len(rows) == 1 and "charged 100.00 (1 charge)" in rows[0].value, rows[0].value if rows else "missing")
    at.run()
    booked_now = [c.label for c in at.checkbox if c.key in (f"seat-show-1-{picks[0]}", f"seat-show-1-{picks[1]}")]
    check("booked seats now labelled booked and disabled", all("booked" in l for l in booked_now), str(booked_now))
    cancel = [b for b in at.button if b.label == "Cancel"]
    cancel[0].click().run()
    md = texts(at, "markdown")
    check("cancellation shows CANCELLED and refunded", "CANCELLED" in md and "refunded" in md)

    out()
    out("## FAQ chat")
    at.text_input[0].input("How do I cancel a booking?")
    button(at, "Ask").click().run()
    md = texts(at, "markdown")
    check("in-domain question answered and labelled LLM-generated", "Assistant (LLM-generated)" in md, md[-160:])
    exp = [e for e in at.expander if "Supporting context" in e.label]
    check("supporting context with correlation ID shown", bool(exp) and "faq-" in exp[0].label and token not in exp[0].label)
    at.text_input[0].input("What is the capital of France?")
    button(at, "Ask").click().run()
    check("out-of-domain question refused", "only answer questions" in texts(at, "info"))
    compose("stop", "llm")
    at.text_input[0].input("Is payment refundable on cancellation?")
    button(at, "Ask").click().run()
    check("LLM outage -> labelled verbatim fallback", "not AI-generated" in texts(at, "warning") and "2 hours" in texts(at, "text"))
    picks2 = free_seats(at, "show-1", 1)
    select_seats(at, "show-1", picks2)
    button(at, "Pay and book").click().run()
    check("booking still works with the LLM node down", "Booking confirmed" in texts(at, "success"))
    compose("start", "llm")
    wait_healthy(["llm"])

    out()
    out("## Cluster Observatory")
    view(at, "Cluster Observatory")
    df = at.dataframe[0].value
    roles = sorted(df["role"].tolist())
    check("three nodes reachable, one leader + two followers", roles == ["follower", "follower", "leader"] and set(df["reachability"]) == {"REACHABLE"}, str(roles))
    cols = {"term", "leader", "log length", "commit", "applied", "commit lag", "leader matchIndex", "log digest", "state digest"}
    check("table has term/leader/log/commit/applied/lag/matchIndex/digests", cols <= set(df.columns), str(sorted(df.columns)))
    check("leader banner names the leader", "Leader:" in texts(at, "success"))
    leader = df[df["role"] == "leader"]["node"].iloc[0]
    follower = df[df["role"] == "follower"]["node"].iloc[0]
    compose("stop", SERVICE[follower])
    at.run()
    df = at.dataframe[0].value
    row = df[df["node"] == follower].iloc[0]
    check("stopped node shown UNREACHABLE (not follower)", row["reachability"] == "UNREACHABLE" and row["role"] == "UNREACHABLE", f"{row['reachability']}/{row['role']}")
    compose("start", SERVICE[follower])
    wait_healthy([SERVICE[follower]])

    out()
    out("## Demo Lab")
    view(at, "Demo Lab")
    fill(at, "race", "show-8")
    button(at, "Run race").click().run()
    s = texts(at, "success")
    check("seat race: all invariant checks PASS", s.count("PASS:") >= 4 and "FAIL" not in texts(at, "error"), s[:200])
    fill(at, "retry", "show-7")
    button(at, "Run retry").click().run()
    s = texts(at, "success")
    check("same-key retry: one booking, charged once (PASS)", "PASS: one booking record, charged exactly once" in s)

    at.run()
    snap = ui.observe(ui.Config(), at.session_state["token"])
    old = snap["leader"]
    check("guided failover shows the stop command for the leader", f"docker compose stop {SERVICE[old]}" in texts(at, "code"))
    timer = threading.Timer(2.0, compose, args=("stop", SERVICE[old]))
    timer.start()
    button(at, "Watch failover").click().run()
    timer.join()
    s = texts(at, "success")
    check("measured failover to a new leader", "measured failover" in s, s[-160:])
    check("restart command shown for the old leader", f"docker compose start {SERVICE[old]}" in texts(at, "code"))
    compose("start", SERVICE[old])
    wait_healthy([SERVICE[old]])
    button(at, "Check old-leader catch-up").click().run()
    check("old leader caught up with identical digests", "PASS" in texts(at, "success") and "caught up" in texts(at, "success"))

    snap = ui.observe(ui.Config(), at.session_state["token"])
    lead = snap["leader"]
    others = [n for n in SERVICE if n != lead]
    fill(at, "nq", "show-6")
    compose("stop", SERVICE[others[0]], SERVICE[others[1]])
    button(at, "Attempt booking now").click().run()
    check("no quorum: booking not committed", "Not committed" in texts(at, "info"), texts(at, "info")[:160])
    view(at, "Cluster Observatory")
    df = at.dataframe[0].value
    check("Observatory: two UNREACHABLE nodes during quorum loss", list(df["reachability"]).count("UNREACHABLE") == 2, str(list(df["role"])))
    view(at, "Booking")
    check("Booking view: stale seat map banner without quorum", "STALE" in texts(at, "warning"), texts(at, "warning")[:200])
    view(at, "Demo Lab")
    compose("start", SERVICE[others[0]])
    wait_healthy([SERVICE[others[0]]])
    presses = 0
    while presses < 5:
        presses += 1
        button(at, "Attempt booking now").click().run()
        if "PASS: exactly one booking and one charge" in texts(at, "success"):
            break
        out(f"  press {presses}: {texts(at, 'info')[-160:]}")
    check("same request ID after quorum returns: one booking, one charge (PASS)", "PASS: exactly one booking and one charge" in texts(at, "success"), f"after {presses} press(es)")
    compose("start", SERVICE[others[1]])
    wait_healthy([SERVICE[others[1]]])

    dl = [e for e in at.get("download_button")]
    check("Markdown and JSON summary download buttons present", len(dl) == 2, str([d.proto.label for d in dl]))
    md = ui.summary_markdown(at.session_state["timeline"], at.session_state["lab_sections"])
    check("summary contains timeline and PASS results, no FAIL", "| t (ms) |" in md and "PASS" in md and "FAIL" not in md)
    check("timeline has correlated request IDs", any(e["correlation"] for e in at.session_state["timeline"].events))

    out()
    out("## Privacy")
    other = new_app()
    auth(other, "bob", "builder")
    check("another user's bookings not visible", f"seats {seats_str}" not in page_text(other))
    txt = page_text(at) + page_text(other)
    check("no password, token or full card number rendered", "GoodPass123" not in txt and at.session_state["token"] not in txt and other.session_state["token"] not in txt)

    out()
    out(f"## Result: {'ALL CHECKS PASSED' if not FAILED else 'FAILED: ' + '; '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
