import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from ui import cluster  # noqa: E402

st.set_page_config(page_title="Raft Ticket Booking", layout="wide")

CARDS = {
    "Mock card - approves (4242 4242 4242 4242)": "4242424242424242",
    "Mock card - declines (4000 0000 0000 0000)": "4000000000000000",
}
FAILURE_STATES = {
    "UNAVAILABLE": "Disconnected: no cluster node answered.",
    "NO_QUORUM": "The cluster has no majority right now; nothing was committed.",
    "NO_LEADER": "The cluster is electing a leader; try again in a moment.",
}

ss = st.session_state
ss.setdefault("token", None)
ss.setdefault("user", None)
ss.setdefault("pending_rid", None)
ss.setdefault("seat_cache", {})
ss.setdefault("lag_history", [])
ss.setdefault("timeline", cluster.Timeline())
ss.setdefault("lab_sections", [])
ss.setdefault("failover", {})
ss.setdefault("chat", [])

cfg = cluster.Config()


def show_status(status, message="", ok_text=None):
    if status == "OK":
        st.success(ok_text or message or "OK")
    elif status in FAILURE_STATES:
        st.error(f"{status}: {FAILURE_STATES[status]} {message}")
    else:
        st.warning(f"{status}: {message}")


def client(timeout=3.0):
    return cfg.client(ss.token, timeout=timeout)


# ---------------------------------------------------------------- sidebar / auth

st.sidebar.title("Raft Ticket Booking")
st.sidebar.caption("UI talks to the cluster only through gRPC.")
st.sidebar.write("Nodes: " + ", ".join(f"{k} ({v})" for k, v in cfg.nodes.items()))
view = st.sidebar.radio("View", ["Booking", "Cluster Observatory", "Demo Lab"])

if ss.token:
    st.sidebar.write(f"Signed in as **{ss.user}**")
    if st.sidebar.button("Log out"):
        c = client()
        ok, msg = c.logout()
        c.close()
        ss.token, ss.user = None, None
        st.rerun()
else:
    with st.sidebar.form("auth"):
        uname = st.text_input("Username")
        pw = st.text_input("Password", type="password")
        mode = st.radio("Action", ["Log in", "Sign up"], horizontal=True)
        go = st.form_submit_button("Continue")
    if go:
        c = cfg.client()
        with st.spinner("Contacting cluster..."):
            ok, msg = c.login(uname, pw) if mode == "Log in" else c.signup(uname, pw)
        if ok:
            ss.token, ss.user = c.token, uname
            c.close()
            st.rerun()
        c.close()
        st.sidebar.error(msg)

if not ss.token:
    st.title("Distributed Ticket Booking (Raft)")
    st.info("Log in or sign up in the sidebar to continue. Seeded demo accounts are listed in the README.")
    st.stop()


# ---------------------------------------------------------------- booking view


def load_shows(c):
    with st.spinner("Loading shows..."):
        status, shows, msg = c.get_shows()
    if status == "OK":
        ss.shows_cache = shows
        return shows
    show_status(status, msg)
    if ss.get("shows_cache"):
        st.warning("STALE: showing the last loaded show list.")
        return ss.shows_cache
    return None


def load_seats(c, show_id):
    with st.spinner("Reading committed seat map (leader read barrier)..."):
        status, seats, msg = c.get_seats(show_id)
    if status == "OK":
        ss.seat_cache[show_id] = (seats, datetime.now().strftime("%H:%M:%S"))
        st.caption(f"Seat map is linearizable: read confirmed by a Raft majority at {ss.seat_cache[show_id][1]}.")
        return seats
    cache = ss.seat_cache.get(show_id)
    if cache:
        st.warning(
            f"STALE: live read failed ({status}). Showing the seat map last confirmed at {cache[1]}; booking may fail."
        )
        return cache[0]
    show_status(status, msg)
    return None


def seat_picker(show_id, seats):
    st.subheader("Seats (select up to 5)")
    st.caption("Each seat is labelled with its state; booked seats are disabled.")
    picked = []
    cols = st.columns(10)
    for i, s in enumerate(seats):
        booked = s["status"] != "AVAILABLE"
        label = f"{s['seat_id']} - booked" if booked else s["seat_id"]
        with cols[i % 10]:
            if st.checkbox(
                label, key=f"seat-{show_id}-{s['seat_id']}", disabled=booked, help="Booked" if booked else "Available"
            ):
                picked.append(s["seat_id"])
    return picked


def checkout(c, show_id, picked):
    if len(picked) > 5:
        st.error(f"{len(picked)} seats selected; the limit is 5 per booking.")
        return
    if not picked:
        return
    qstatus, quote, qmsg = c.get_quote(show_id, picked)
    if qstatus == "OK":
        st.write(f"Server quote: {len(quote['seat_ids'])} x {quote['unit_price']:.2f} = **{quote['total']:.2f}**")
    else:
        show_status(qstatus, qmsg)
    card_label = st.radio("Mock payment method (no real card data)", list(CARDS))
    if qstatus != "OK" or not st.button("Pay and book", type="primary"):
        return
    key = (show_id, tuple(picked), card_label)
    if ss.pending_rid is None or ss.pending_rid[0] != key:
        ss.pending_rid = (key, uuid4().hex)
    rid = ss.pending_rid[1]
    with st.spinner("Proposing booking to the Raft leader..."):
        bstatus, bid, bmsg = c.book_seat(show_id, ",".join(picked), card=CARDS[card_label], req_id=rid)
    if bstatus in cluster.RETRYABLE:
        show_status(bstatus, bmsg + " Pressing the button again retries with the same request ID (no double charge).")
        return
    ss.pending_rid = None
    if bstatus == "OK":
        st.success(f"Booking confirmed. Booking ID {bid}. Request ID {rid}.")
        proof = c.last_booking_receipt
        if proof and proof.get("committed"):
            with st.expander("Raft consensus proof", expanded=True):
                st.markdown("**Booking lifecycle**")
                st.write("1. Request received and processed by the Raft leader.")
                st.write("2. Booking entry appended to the replicated Raft log.")
                st.write("3. Majority commitment confirmed; entry applied to the state machine.")
                if proof.get("replayed"):
                    st.info("Idempotent retry: the original committed booking was returned. No duplicate booking was created.")
                st.markdown("**Consensus details**")
                st.write(f"Leader node: `{proof['leader']}`")
                st.write(f"Raft term: `{proof['term']}`")
                st.write(f"Booking log index: `{proof['log_index']}`")
                st.write(f"Commit index: `{proof['commit_index']}`")
                st.write(f"Last applied index: `{proof['last_applied']}`")
                st.success("This booking's log entry is committed and applied.")
    else:
        show_status(bstatus, bmsg)


def my_bookings(c):
    st.subheader("My bookings")
    mstatus, rows, mmsg = c.get_my_bookings()
    if mstatus != "OK":
        show_status(mstatus, mmsg)
    elif not rows:
        st.write("No bookings yet.")
    for r in rows:
        line = f"`{r['booking_id'][:8]}` {r['show_id']} seats {r['seat_id']} - {r['status']} - charged {r['amount']:.2f} ({r['charges']} charge)"
        if r["refunded"]:
            line += " - refunded"
        a, b = st.columns([5, 1])
        a.markdown(line)
        if r["status"] == "CONFIRMED" and b.button("Cancel", key=f"cancel-{r['booking_id']}"):
            with st.spinner("Cancelling through Raft..."):
                cstatus, _, cmsg = c.cancel_seat(r["booking_id"])
            show_status(cstatus, cmsg, ok_text="Cancelled; refund issued.")
            if cstatus == "OK":
                st.rerun()


def faq_chat(show_id):
    st.subheader("FAQ assistant")
    q = st.text_input("Ask about bookings, seats, payments, cancellations or refunds")
    if st.button("Ask") and q.strip():
        fc = client(timeout=40.0)
        try:
            with st.spinner("Asking the LLM node..."):
                ss.chat.append((q, *fc.ask_faq_detailed(q, show_id=show_id)))
        finally:
            fc.close()
    for q_, fstatus, text, support in reversed(ss.chat[-5:]):
        st.markdown(f"**You:** {q_}")
        if fstatus == "OK":
            st.markdown(f"**Assistant (LLM-generated):** {text}")
        elif fstatus == "LLM_UNAVAILABLE":
            st.warning("LLM unavailable - fallback below is verbatim FAQ text, not AI-generated.")
            st.text(text)
        elif fstatus == "OUT_OF_DOMAIN":
            st.info(text)
        else:
            show_status(fstatus, text)
        if support:
            with st.expander(f"Supporting context (correlation {support.get('correlation_id', '')})"):
                st.write("FAQ sources:", support.get("sources") or "none")
                if support.get("faq_context"):
                    st.text(support["faq_context"])
                if support.get("live_context"):
                    st.text(support["live_context"])


def booking_view():
    st.header("Booking")
    c = client()
    try:
        shows = load_shows(c)
        if shows is None:
            return
        labels = {f"{s['name']} - {s['venue']} - {s['start_time']}": s["show_id"] for s in shows}
        show_id = labels[st.selectbox("Show", list(labels))]
        seats = load_seats(c, show_id)
        if seats is None:
            return
        checkout(c, show_id, seat_picker(show_id, seats))
        my_bookings(c)
        faq_chat(show_id)
    finally:
        c.close()


# ---------------------------------------------------------------- observatory


def observatory_view():
    st.header("Cluster Observatory")
    st.caption("Every value below comes from a live CLUSTER_STATUS RPC sent to that node alone.")
    auto = st.toggle("Auto-refresh every 2 s", value=False)

    def render():
        snap = cluster.observe(cfg, ss.token)
        rep = cluster.consistency_report(snap)
        ss.lag_history.append(
            {"t": snap["sampled_at"][11:19], **{r["node"]: r.get("commit_lag") for r in snap["rows"]}}
        )
        ss.lag_history = ss.lag_history[-60:]
        if snap["leader"]:
            st.success(f"Leader: {snap['leader']} (term {snap['term']}) - sampled {snap['sampled_at']}")
        else:
            st.error(f"No leader observed - sampled {snap['sampled_at']}")
        if rep["multiple_leaders_same_term"]:
            st.error(f"Multiple leaders in one term: {rep['multiple_leaders_same_term']}")
        table = []
        for r in snap["rows"]:
            table.append(
                {
                    "node": r["node"],
                    "address": r["address"],
                    "reachability": "REACHABLE"
                    if r["rpc_status"] == "OK"
                    else ("UNREACHABLE" if not r["reachable"] else r["rpc_status"]),
                    "role": r.get("role"),
                    "term": r.get("term"),
                    "leader": r.get("leader_id"),
                    "log length": r.get("log_length"),
                    "commit": r.get("commit_index"),
                    "applied": r.get("last_applied"),
                    "commit lag": r.get("commit_lag"),
                    "leader matchIndex": r.get("leader_match_index"),
                    "log digest": r.get("log_digest"),
                    "state digest": r.get("state_digest"),
                    "rpc ms": r["rpc_ms"],
                }
            )
        st.dataframe(table, hide_index=True, use_container_width=True)
        for g in rep["groups"]:
            st.write(
                f"commit {g['commit_index']}: nodes {', '.join(g['nodes'])} - "
                f"log digests {'equal' if g['log_digests_equal'] else 'DIFFER'}, "
                f"state digests {'equal' if g['state_digests_equal'] else 'differ/applying'}"
            )
        st.caption("Commit lag = leader commit index - node commit index (sampled; blank when unreachable).")
        st.line_chart(ss.lag_history, x="t")

    if auto:
        st.fragment(run_every=2)(render)()
    else:
        if st.button("Refresh"):
            pass
        render()


# ---------------------------------------------------------------- demo lab


def show_checks(checks):
    for name, passed, detail in checks:
        (st.success if passed else st.error)(f"{'PASS' if passed else 'FAIL'}: {name} ({detail})")


def scenario(title, key, show_default, count_label, count_default, button, spinner, title_fmt, run):
    st.subheader(title)
    a, b, c3 = st.columns(3)
    show = a.text_input("Show", show_default, key=f"{key}-show")
    seat = b.text_input("Seat", f"A{(int(time.time()) % 50) + 1}", key=f"{key}-seat")
    n = c3.number_input(count_label, 2, 20, count_default, key=f"{key}-n")
    if st.button(button):
        with st.spinner(spinner):
            results, checks = run(cfg, ss.token, show, seat, int(n), ss.timeline)
        ss.lab_sections.append((f"{title_fmt} {show}/{seat}", checks))
        st.dataframe(results, hide_index=True)
        show_checks(checks)


def failover_section():
    tl = ss.timeline
    st.subheader("3. Guided leader failure and recovery")
    snap = cluster.observe(cfg, ss.token)
    if not snap["leader"]:
        st.warning("No leader currently observed.")
    else:
        st.write(f"Current leader: **{snap['leader']}** (term {snap['term']}).")
        st.code(cfg.stop_hint(snap["leader"]), language="bash")
        st.write("Click **Watch failover**, then run the command above within 30 s.")
        if st.button("Watch failover"):
            tl.add("failover.watch", f"watching leader {snap['leader']} term {snap['term']}", node=snap["leader"])
            with st.spinner("Polling node status every 200 ms..."):
                res = cluster.watch_failover(cfg, ss.token, snap["leader"], snap["term"], tl)
            if res:
                ss.failover = {"old": snap["leader"], **{k: res[k] for k in ("new_leader", "term", "failover_ms")}}
                st.success(
                    f"New leader {res['new_leader']} in term {res['term']}; measured failover {res['failover_ms']} ms."
                )
                ss.lab_sections.append(
                    ("Leader failover", [("new leader elected in higher term", True, f"{res['failover_ms']} ms")])
                )
            else:
                st.error("No new leader observed within 30 s.")
    old = ss.failover.get("old")
    if not old:
        return
    st.write(f"Restart the old leader **{old}**, then check catch-up:")
    st.code(cfg.start_hint(old), language="bash")
    if st.button("Check old-leader catch-up"):
        with st.spinner("Waiting for old leader to match the leader's commit index..."):
            same, _ = cluster.wait_caught_up(cfg, ss.token, old, tl)
        ss.lab_sections.append(("Old-leader catch-up", [(f"{old} log/state digests match leader", same, "")]))
        (st.success if same else st.error)(
            f"{'PASS' if same else 'FAIL'}: {old} caught up with identical log and state digests"
        )


def no_quorum_section():
    st.subheader("4. No-quorum scenario")
    st.write("Stop two of the three nodes, attempt a booking, restart one node, then retry with the same request ID.")
    for nid in cfg.nodes:
        st.code(f"{cfg.stop_hint(nid)}    /    {cfg.start_hint(nid)}", language="bash")
    a, b = st.columns(2)
    nq_show = a.text_input("Show", "show-6", key="nq-show")
    nq_seat = b.text_input("Seat", "A50", key="nq-seat")
    pending = ss.get("nq_pending")
    if pending:
        st.caption(f"Pending request {pending[0]} for {pending[1]}/{pending[2]} (retried unchanged).")
    if not st.button("Attempt booking now"):
        return
    if "nq_pending" not in ss:
        ss.nq_pending = (f"nq-{uuid4().hex[:12]}", nq_show, nq_seat)
    rid, show, seat = ss.nq_pending
    res = cluster.attempt_booking(cfg, ss.token, show, seat, rid, ss.timeline)
    st.write(res)
    if res["status"] == "OK":
        ok, rows = cluster.booking_charge_check(cfg, ss.token, res["booking_id"])
        ss.lab_sections.append(("No-quorum retry", [("same request ID -> one booking, one charge", ok, str(rows))]))
        (st.success if ok else st.error)(f"{'PASS' if ok else 'FAIL'}: exactly one booking and one charge for {rid}")
        del ss["nq_pending"]
    else:
        ss.lab_sections.append(
            ("No-quorum attempt", [("booking not acknowledged without quorum", True, res["status"])])
        )
        st.info(
            f"Not committed ({res['status']}). Restart a node and press the button again: request ID {rid} for {show}/{seat} is reused."
        )


def timeline_section():
    tl = ss.timeline
    st.subheader("Timeline")
    st.dataframe(tl.events, hide_index=True, use_container_width=True)
    a, b, c3 = st.columns(3)
    a.download_button(
        "Download summary (Markdown)", cluster.summary_markdown(tl, ss.lab_sections), "demo-lab-summary.md"
    )
    b.download_button("Download summary (JSON)", cluster.summary_json(tl, ss.lab_sections), "demo-lab-summary.json")
    if c3.button("Clear timeline"):
        ss.timeline = cluster.Timeline()
        ss.lab_sections = []
        st.rerun()


def demo_lab_view():
    st.header("Demo Lab")
    st.caption(
        "Scenarios issue real RPCs; checks are computed from the responses. Node stops are done by you in a terminal - the UI has no Docker or shell access."
    )
    scenario(
        "1. Concurrent seat race",
        "race",
        "show-8",
        "Concurrent clients",
        8,
        "Run race",
        "Racing...",
        "Seat race",
        cluster.seat_race,
    )
    scenario(
        "2. Same-key retry",
        "retry",
        "show-7",
        "Concurrent copies",
        5,
        "Run retry",
        "Submitting duplicates...",
        "Same-key retry",
        cluster.same_key_retry,
    )
    failover_section()
    no_quorum_section()
    timeline_section()


if view == "Booking":
    booking_view()
elif view == "Cluster Observatory":
    observatory_view()
else:
    demo_lab_view()

