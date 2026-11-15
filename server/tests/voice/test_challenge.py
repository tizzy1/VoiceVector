"""Judge challenge (server/challenge.py): clock, score, leaderboard, names."""
import json

import pytest

from server.challenge import Challenge, time_points
from server.contracts import Event, Instruction, SessionSettings


def ev(t, type_, callsign=None, **data):
    return Event(t=t, type=type_, actor="SYSTEM", callsign=callsign, data=data)


def test_only_active_in_the_judge_scenario(tmp_path):
    c = Challenge(tmp_path / "lb.json")
    c.start("demo")
    c.on_event(ev(5, "ctrl_transmission"))
    assert not c.active and c.calls == 0 and c.message().status == "off"


def test_clock_runs_from_first_call_to_landing_and_records_the_entry(tmp_path):
    c = Challenge(tmp_path / "lb.json")
    c.start("judge")
    assert c.message().status == "waiting" and c.message().score is None
    c.on_event(ev(30, "ctrl_transmission"))
    c.on_coach(90)
    c.tick(200)
    m = c.message()
    assert m.status == "running" and m.elapsed_s == 170 and m.phraseology == 90
    c.on_event(ev(300, "ctrl_transmission"))
    c.on_coach(70)
    c.on_event(ev(330, "landing", "BAW117"))
    c.tick(400)                                   # clock stops at touchdown
    m = c.message()
    assert m.status == "landed" and m.elapsed_s == 300 and m.calls == 2
    assert m.score == round(40 + 40 * 0.80 + 20) and m.rank == 1        # 300 s: inside the 9-min full-points window
    saved = json.loads((tmp_path / "lb.json").read_text())
    assert saved[0]["score"] == m.score and saved[0]["time_s"] == 300


def test_go_arounds_alerts_and_errors_change_the_score(tmp_path):
    c = Challenge(tmp_path / "lb.json")
    c.start("judge")
    c.on_event(ev(0, "ctrl_transmission"))
    base = c.score()
    c.on_event(ev(10, "go_around", "BAW117"))
    c.on_event(ev(11, "alert_start", kind="STCA_LOS"))
    assert c.score() == base - 15
    c.on_event(ev(12, "error_caught"))
    assert c.score() == base - 10


def test_time_points_shape():
    assert time_points(500) == 40 and time_points(1500) == 10 and 10 < time_points(675) < 40


def test_leaderboard_keeps_top_ten_survives_restart_and_takes_a_name(tmp_path):
    path = tmp_path / "lb.json"
    for k in range(12):
        c = Challenge(path)
        c.start("judge")
        c.on_event(ev(0, "ctrl_transmission"))
        c.on_coach(50 + 4 * k)
        c.on_event(ev(400 + 10 * k, "landing", "BAW117"))
    assert len(c.message().leaderboard) == 10
    assert c.set_name("  Ada   Lovelace ") and c.message().leaderboard[c.rank() - 1].name == "Ada Lovelace"
    assert Challenge(path).board[0]["score"] >= Challenge(path).board[-1]["score"]
    fresh = Challenge(path)
    fresh.start("judge")
    assert not fresh.set_name("too early")


def test_a_real_judge_run_scores_on_landing(tmp_path):
    """The simulator's own landing event finishes the challenge."""
    from server.sim.world import World
    from server.sim.modes import loc_geometry
    from server.tests.voice.bench_latency import Bus
    from pathlib import Path
    data = Path(__file__).resolve().parents[3] / "data"
    bus = Bus()
    world = World(bus, data)
    bus.clock = lambda: world.t
    c = Challenge(tmp_path / "lb.json")
    bus.subscribe(c.on_event)
    world.load_scenario("judge", 42)
    world.configure(SessionSettings(scenario="judge", error_rate="off"))
    c.start("judge")
    ac = world.aircraft["BAW117"]
    for _ in range(20):
        world.tick(1.0)
    bus.emit(ev(world.t, "ctrl_transmission"))
    world._apply(ac, Instruction(type="heading", value=360, turn_dir="L"))
    world._apply(ac, Instruction(type="altitude", value=3000))
    cleared = False
    for _ in range(25 * 60):
        world.tick(1.0)
        c.tick(world.t)
        if not cleared and abs(loc_geometry(world, ac)[1]) <= 5.0:
            world._apply(ac, Instruction(type="heading", value=290, turn_dir="L"))
            world._apply(ac, Instruction(type="approach_clearance", approach_type="ILS", runway=world.runway))
            cleared = True
        if c.status == "landed":
            break
    m = c.message()
    assert m.status == "landed" and 200 < m.elapsed_s < 1500 and m.score and m.rank == 1


@pytest.mark.asyncio
async def test_session_runs_the_challenge_and_takes_a_name(tmp_path):
    from server.contracts import ControlMsg
    from server.session import Session
    from server.tests.voice.fakes import FakeHub
    hub = FakeHub()
    hub.counts = lambda: {}
    hub.on_event = lambda e: None
    s = Session(hub, runs_dir=tmp_path)
    s.settings = SessionSettings(scenario="judge", error_rate="off", paused=True)
    await s.start()
    try:
        assert s.challenge.active and list(s.world.aircraft) == ["BAW117"]
        with pytest.raises(ValueError):
            await s.handle_control(ControlMsg(action="challenge_name", params={"name": "Early"}))
        s.bus.emit(ev(s.world.t, "ctrl_transmission"))
        s.bus.emit(ev(s.world.t + 300, "landing", "BAW117"))
        await s.handle_control(ControlMsg(action="challenge_name", params={"name": "Judge Ada"}))
        msg = [m for m in hub.messages if getattr(m, "type", "") == "challenge"][-1]
        assert msg.status == "landed" and msg.leaderboard[0].name == "Judge Ada"
        assert (tmp_path / "leaderboard.json").exists()
    finally:
        await s.stop()
