import ast
import threading
from pathlib import Path

import pytest

from tests.raft_faults import wait_converged
from tests.test_raft_m2 import Cluster
from ui import cluster as ui

UI_DIR = Path(__file__).resolve().parents[1] / "ui"


def imported_modules(path):
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


@pytest.mark.parametrize("name", ["app.py", "cluster.py"])
def test_ui_is_grpc_only(name):
    mods = imported_modules(UI_DIR / name)
    forbidden = {"sqlite3", "subprocess", "docker", "ticket_booking.application", "ticket_booking.state_machine",
                 "ticket_booking.payment", "ticket_booking.auth", "ticket_booking.raft_node", "ticket_booking.seed"}
    assert not (mods & forbidden), mods & forbidden
    src = (UI_DIR / name).read_text()
    for bad in ("BookingApp", "os.system", "Popen", "docker.sock"):
        assert bad not in src


@pytest.fixture
def live(tmp_path):
    c = Cluster(tmp_path)
    leader = c.leader()
    assert leader
    cfg = ui.Config(nodes=dict(c.addrs), redirect_map={}, services={})
    cl = cfg.client()
    assert cl.login("alice", "wonderland")[0]
    assert wait_converged(c.nodes)
    yield c, cfg, cl.token
    cl.close()
    c.close()


def test_observatory_distinguishes_unreachable_from_follower(live):
    c, cfg, token = live
    snap = ui.observe(cfg, token)
    roles = sorted(r["role"] for r in snap["rows"])
    assert roles == ["follower", "follower", "leader"]
    assert ui.consistency_report(snap)["multiple_leaders_same_term"] == {}
    follower = next(r["node"] for r in snap["rows"] if r["role"] == "follower")
    c.stop(follower)
    snap = ui.observe(cfg, token)
    row = next(r for r in snap["rows"] if r["node"] == follower)
    assert row["reachable"] is False and row["role"] == "UNREACHABLE"
    assert "commit_lag" not in row
    assert sorted(r["role"] for r in snap["rows"]) == ["UNREACHABLE", "follower", "leader"]


def test_demo_lab_race_and_retry_checks_are_real(live):
    c, cfg, token = live
    tl = ui.Timeline()
    results, checks = ui.seat_race(cfg, token, "show-8", "A3", 6, tl)
    assert [r["status"] for r in results].count("OK") == 1
    assert all(p for _, p, _ in checks), checks
    results, checks = ui.same_key_retry(cfg, token, "show-7", "A4", 4, tl)
    assert len({r["booking_id"] for r in results}) == 1
    assert all(p for _, p, _ in checks), checks
    assert any(e["kind"] == "race.check" for e in tl.events)
    md = ui.summary_markdown(tl, [("race", checks)])
    assert "PASS" in md and "| t (ms) |" in md
    # retrying the same seat again must now fail the "OK" path honestly
    results, checks = ui.seat_race(cfg, token, "show-8", "A3", 3, tl)
    assert all(r["status"] == "ALREADY_BOOKED" for r in results)


def test_demo_lab_failover_measurement_and_catch_up(live):
    c, cfg, token = live
    tl = ui.Timeline()
    snap = ui.observe(cfg, token)
    old, term = snap["leader"], snap["term"]
    killer = threading.Timer(0.5, c.stop, args=(old,))
    killer.start()
    res = ui.watch_failover(cfg, token, old, term, tl, timeout=15)
    killer.join()
    assert res and res["new_leader"] != old and res["term"] > term
    assert 0 <= res["failover_ms"] < 15000
    c.restart(old)
    same, _ = ui.wait_caught_up(cfg, token, old, tl, timeout=15)
    assert same
    kinds = [e["kind"] for e in tl.events]
    assert kinds.index("failover.leader_lost") < kinds.index("failover.new_leader") < kinds.index("recovery.caught_up")


def test_streamlit_views_render_against_live_cluster(live, monkeypatch):
    from streamlit.testing.v1 import AppTest

    c, cfg, token = live
    monkeypatch.setenv("TB_NODES", ",".join(f"{k}={v}" for k, v in cfg.nodes.items()))
    monkeypatch.setenv("TB_REDIRECT_MAP", "")
    at = AppTest.from_file(str(UI_DIR / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("Log in or sign up" in i.value for i in at.info)
    at.sidebar.text_input[0].input("alice")
    at.sidebar.text_input[1].input("wonderland")
    at.sidebar.button[0].click().run()
    assert not at.exception
    assert at.session_state["user"] == "alice"
    assert any("linearizable" in cap.value for cap in at.caption)
    seats = [cb for cb in at.checkbox if cb.label in ("A1", "A2")]
    for cb in seats:
        cb.check()
    at.run()
    assert any("Server quote" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "Pay and book").click().run()
    assert not at.exception
    assert any("Booking confirmed" in s.value for s in at.success)
    assert any("A1,A2" in m.value for m in at.markdown)

    at.sidebar.radio[0].set_value("Cluster Observatory").run()
    assert not at.exception
    assert any("Leader:" in s.value for s in at.success)

    at.sidebar.radio[0].set_value("Demo Lab").run()
    assert not at.exception
    next(b for b in at.button if b.label == "Run race").click().run()
    assert not at.exception
    assert any(s.value.startswith("PASS: at most one OK booking") for s in at.success)


def test_booking_view_shows_stale_seat_map_when_quorum_is_lost(live, monkeypatch):
    from streamlit.testing.v1 import AppTest

    c, cfg, token = live
    monkeypatch.setenv("TB_NODES", ",".join(f"{k}={v}" for k, v in cfg.nodes.items()))
    monkeypatch.setenv("TB_REDIRECT_MAP", "")
    at = AppTest.from_file(str(UI_DIR / "app.py"), default_timeout=60)
    at.run()
    at.sidebar.text_input[0].input("alice")
    at.sidebar.text_input[1].input("wonderland")
    at.sidebar.button[0].click().run()
    assert any("linearizable" in cap.value for cap in at.caption)
    leader = c.leader()
    for n in [n for n in c.ids if n != leader]:
        c.stop(n)
    at.run()
    assert not at.exception
    warnings = " ".join(w.value for w in at.warning)
    assert "STALE" in warnings and "seat map last confirmed" in warnings, warnings
    assert any(cb.label == "A1" for cb in at.checkbox)


def test_demo_lab_no_quorum_retry_survives_view_switch(live, monkeypatch):
    """The pending no-quorum request must be retried with its original show/seat even after
    the user visits another view (Streamlit resets widgets that were not rendered)."""
    from streamlit.testing.v1 import AppTest

    c, cfg, token = live
    monkeypatch.setenv("TB_NODES", ",".join(f"{k}={v}" for k, v in cfg.nodes.items()))
    monkeypatch.setenv("TB_REDIRECT_MAP", "")
    at = AppTest.from_file(str(UI_DIR / "app.py"), default_timeout=60)
    at.run()
    at.sidebar.text_input[0].input("alice")
    at.sidebar.text_input[1].input("wonderland")
    at.sidebar.button[0].click().run()
    at.sidebar.radio[0].set_value("Demo Lab").run()
    at.text_input(key="nq-show").input("show-5")
    at.text_input(key="nq-seat").input("A7")
    leader = c.leader()
    followers = [n for n in c.ids if n != leader]
    for n in followers:
        c.stop(n)
    next(b for b in at.button if b.label == "Attempt booking now").click().run()
    assert any("Not committed" in i.value for i in at.info)
    at.sidebar.radio[0].set_value("Cluster Observatory").run()
    at.sidebar.radio[0].set_value("Demo Lab").run()
    c.restart(followers[0])
    for _ in range(5):
        next(b for b in at.button if b.label == "Attempt booking now").click().run()
        if any(s.value.startswith("PASS: exactly one booking") for s in at.success):
            break
    assert not any("REQUEST_CONFLICT" in i.value for i in at.info), [i.value for i in at.info]
    assert any(s.value.startswith("PASS: exactly one booking") for s in at.success)
    assert c.nodes[leader].app.seats.grid["show-5"]["A7"].status == "BOOKED"
