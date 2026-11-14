"""Departures + runway manager, instructor injects, demo determinism (ASSIGNMENT_TUDOR 10/12/14)."""
import math

from server.contracts import ClearanceElement, Instruction, StateMsg
from server.sim import runway
from server.sim.modes import loc_geometry
from server.sim.world import World
from server.tests.sim.test_sim_behaviour import empty, on_final, place, run


def el(**kw):
    i = Instruction(**kw)
    return ClearanceElement(type=i.type, intended=i, readback=i, flown=i)


def dep(world, cs="EZY1", sid="BOGNA1X", actype="A320"):
    ac = runway.make_departure(world, cs, actype, sid, "crisp_airline")
    world.add_aircraft(ac, checkin=False)
    return ac


def calls(bus, cs, kind=None):
    return [e for e in bus.of("pilot_call")
            if e.callsign == cs and (kind is None or e.data["kind"] == kind)]


# ------------------------------------------------------------------ departures
def test_departure_bogna1x_levels_6000_requests_higher_then_b10(world, bus, data_dir):
    """SIM_SPEC §8 test 9."""
    from server.rules.engine import RulesEngine
    empty(world)
    rules = RulesEngine(world, bus, data_dir)
    bus.subscribe(rules.on_event)
    ac = dep(world)
    assert ac.phase == "QUEUED" and not ac.on_frequency
    run(world, 120, until=lambda: bus.of("takeoff"))
    assert ac.phase in ("TAKEOFF", "SID") and ac.lat_mode == "RWY_TRACK"
    assert run(world, 200, until=lambda: calls(bus, "EZY1", "checkin"))
    c = calls(bus, "EZY1", "checkin")[0].data
    assert c["alt"] == 2000 and c["cleared_alt"] == 6000 and c["route"] == "BOGNA1X"
    assert ac.on_frequency and ac.phase == "SID"
    assert run(world, 600, until=lambda: calls(bus, "EZY1", "request"))
    assert abs(ac.alt - 6000) < 1 and ac.lat_mode == "LNAV"
    assert calls(bus, "EZY1", "request")[0].data["what"] == "higher"
    t_level = ac.levelled_t
    assert world.t - t_level > 60
    for _ in range(900):
        world.tick(1.0)
        rules.on_tick(world.snapshot())
        if lambda_b10(rules):
            break
    assert lambda_b10(rules)
    assert abs(ac.alt - 6000) < 1                        # keeps 6,000 without a clearance


def lambda_b10(rules):
    return [f for f in rules.findings if f.rule_id == "B10" and "Departure" in f.title]


def test_contact_london_hands_off_and_aircraft_leaves(world, bus):
    empty(world)
    ac = dep(world, sid="SFD1X")
    run(world, 400, until=lambda: ac.on_frequency)
    world.schedule_apply(ac.callsign, [el(type="contact", unit="london")], at=world.t)
    run(world, 2)
    assert ac.phase == "HANDED_OFF" and not ac.on_frequency
    run(world, 900, until=lambda: ac.callsign not in world.aircraft)
    assert ac.callsign not in world.aircraft


def test_release_waits_for_arrival_inside_6nm(world, bus):
    empty(world)
    x, y = on_final(world, 5.0)
    arr = place(world, "ARR1", x=x, y=y, alt=1800, hdg=world.loc_course, ias=150)
    world.schedule_apply("ARR1", [el(type="approach_clearance", runway="26L")], at=0)
    d = dep(world)
    world.tick(1.0)
    assert d.phase == "QUEUED"
    assert runway.blocked_reason(world, d) == "arrival inside 6 NM"
    run(world, 300, until=lambda: d.phase != "QUEUED")
    assert arr.phase == "LANDED" or arr.callsign not in world.aircraft
    lt = arr.landed_t
    assert d.released_t - lt >= 55                       # arrival runway occupancy


def test_second_departure_waits_for_spacing(world, bus):
    empty(world)
    a = dep(world, "EZY1", "BOGNA1X")
    b = dep(world, "EZY2", "BOGNA1X")
    run(world, 600, until=lambda: b.phase != "QUEUED")
    gap = math.hypot(a.x - world.thr_xy[0], a.y - world.thr_xy[1])
    assert gap >= 2.0 and b.released_t - a.released_t >= 45
    assert world.public_state().backlog["DEP"] == 0


def test_queued_departure_never_triggers_stca(world, bus):
    empty(world)
    dep(world)
    x, y = on_final(world, 1.0)
    place(world, "ARR1", x=x, y=y, alt=500, hdg=world.loc_course, ias=140)
    world.tick(1.0)
    assert not world.alerts


# ------------------------------------------------------------------ localiser
def test_loc_captured_inside_2nm_xte(world, bus):
    empty(world)
    x, y = on_final(world, 12.0, offset_nm=1.5)
    ac = place(world, "T1", x=x, y=y, alt=3000, hdg=world.loc_course - 20)
    world.schedule_apply("T1", [el(type="approach_clearance", runway="26L")], at=0)
    run(world, 3)
    assert ac.lat_mode == "LOC"


def test_60_degree_intercept_overshoots_and_pilot_calls(world, bus):
    """SIM_SPEC §8 test 7."""
    empty(world)
    x, y = on_final(world, 12.0, offset_nm=4.0)
    ac = place(world, "T1", x=x, y=y, alt=3000, hdg=world.loc_course - 60)
    world.schedule_apply("T1", [el(type="approach_clearance", runway="26L")], at=0)
    run(world, 200)
    assert ac.lat_mode != "LOC"
    [c] = calls(bus, "T1", "unable")
    assert c.data["reason_code"] == "UNABLE_INTERCEPT_ANGLE"


# -------------------------------------------------------------------- injects
def test_spawn_conflict_los_in_about_90s(world, bus):
    empty(world)
    world.inject("spawn_conflict")
    a, b = bus.of("inject")[-1].data["spawned"]
    assert world.aircraft[a].alt == world.aircraft[b].alt
    run(world, 2)
    assert any(al.kind == "STCA_PRED" for al in world.alerts.values())
    assert run(world, 200, until=lambda: any(al.kind == "STCA_LOS"
                                             for al in world.alerts.values()))
    assert 80 <= world.t <= 100


def test_force_overlap_two_pilot_calls_same_tick(world, bus):
    empty(world)
    world.inject("force_overlap")
    callers = bus.of("inject")[-1].data["callers"]
    pcs = [e for e in bus.of("pilot_call") if e.callsign in callers]
    assert len(callers) == 2 and len(pcs) == 2 and pcs[0].t == pcs[1].t


def test_mayday_radio_failure_go_around_spawn_departure(world, bus):
    world.inject("mayday", "DLH4KA", {"reason": "engine failure"})
    d = world.aircraft["DLH4KA"]
    assert d.squawk == "7700" and d.emergency
    assert calls(bus, "DLH4KA", "mayday")[0].data["reason"] == "engine failure"
    world.inject("radio_failure", "BAW117")
    assert "BAW117" not in [c.icao for c in world.callsigns()]
    world.inject("go_around", "EZY45AB")
    assert world.aircraft["EZY45AB"].phase == "GO_AROUND"
    world.inject("spawn_departure", params={"sid": "NOVMA1X"})
    [cs] = bus.of("inject")[-1].data["spawned"]
    assert world.aircraft[cs].phase == "QUEUED" and world.aircraft[cs].route_name == "NOVMA1X"
    world.tick(1.0)
    StateMsg.model_validate(world.public_state().model_dump())


def test_arrival_asks_expected_approach_after_two_minutes(world, bus):
    empty(world)
    ac = world.generator.spawn(force=True)
    run(world, 150)
    [c] = calls(bus, ac.callsign, "request")
    assert c.data["what"] == "expected_approach" and 120 <= c.t <= 140


# ----------------------------------------------------------------------- demo
def _demo_run(bus_cls, data_dir):
    bus = bus_cls()
    w = World(bus, data_dir)
    bus.clock = lambda: w.t
    w.load_scenario("demo", 42)
    for t in range(1, 181):                              # the 3-minute pitch
        if t == 20:
            w.inject("spawn_conflict")                   # beat 2
        if t == 50:
            w.inject("force_overlap")                    # beat 3
        if t == 60:
            w.inject("spawn_departure", params={"sid": "BOGNA1X"})
        if t == 140:
            w.inject("mayday", "DLH4KA", {"reason": "engine failure"})   # beat 6
        w.tick(1.0)
    return w, bus


def test_demo_beats_land_inside_three_minutes_and_replay_identically(data_dir):
    from server.tests.sim.conftest import FakeBus
    w1, b1 = _demo_run(FakeBus, data_dir)
    w2, b2 = _demo_run(FakeBus, data_dir)
    assert [e.model_dump() for e in b1.events] == [e.model_dump() for e in b2.events]
    kinds = {(e.data["kind"]) for e in b1.of("alert_start")}
    assert {"STCA_PRED", "STCA_LOS", "EMERGENCY"} <= kinds
    assert b1.of("takeoff") and any(e.data["kind"] == "checkin" and e.data.get("passing")
                                    for e in b1.of("pilot_call"))
    overlap = [e for e in b1.of("pilot_call") if e.data["kind"] == "checkin" and e.t == 49]
    assert len(overlap) == 2


def test_demo_script_fires_timed_injects_once():
    from pathlib import Path
    from server.sim.world import World
    from server.pilot.engine import PilotEngine
    from server.tests.sim.conftest import FakeBus
    bus = FakeBus()
    w = World(bus, Path("data"))
    w.load_scenario("demo", 42)
    pe = PilotEngine(w, bus, Path("data"))
    for _ in range(119):
        w.tick(1.0); pe.tick(w.t)
    assert pe._forced is None
    w.tick(1.0); pe.tick(w.t)
    assert pe._forced == ("consistent", "wrong_value_adjacent", "EZY45AB")
    while w.t < 150:
        w.tick(1.0); pe.tick(w.t)
    assert w.aircraft["DLH4KA"].emergency and w.aircraft["DLH4KA"].squawk == "7700"
    n = sum(1 for e in bus.events if getattr(e, "type", None) == "inject")
    for _ in range(30):
        w.tick(1.0); pe.tick(w.t)
    assert sum(1 for e in bus.events if getattr(e, "type", None) == "inject") == n == 1


def test_wake_alert_on_final_medium_behind_heavy(world, bus):
    empty(world)
    hx, hy = on_final(world, 6.0)
    fx, fy = on_final(world, 10.0)       # 4 NM behind a Heavy: H>M needs 5 NM
    place(world, "HVY1", x=hx, y=hy, alt=1900, ias=160, actype="B77W",
          lat_mode="LOC", established=True).wake = "H"
    place(world, "MED1", x=fx, y=fy, alt=3000, ias=160, lat_mode="LOC", established=True)
    world.tick(1.0)
    wake = [a for a in world.public_state().alerts if a.kind == "WAKE"]
    assert len(wake) == 1 and wake[0].callsigns == ["HVY1", "MED1"]
    assert wake[0].severity == "MEDIUM" and "needs 5 NM" in wake[0].message
    assert [e.data["kind"] for e in bus.of("alert_start")].count("WAKE") == 1
    world.aircraft["MED1"].x, world.aircraft["MED1"].y = on_final(world, 12.0)   # 6 NM: legal
    world.tick(1.0)
    assert not [a for a in world.public_state().alerts if a.kind == "WAKE"]
    assert "WAKE" in [e.data["kind"] for e in bus.of("alert_end")]


def test_no_wake_alert_for_medium_pair_at_4nm(world, bus):
    empty(world)
    for cs, d in (("M1", 6.0), ("M2", 10.0)):
        x, y = on_final(world, d)
        place(world, cs, x=x, y=y, alt=2500, ias=160, lat_mode="LOC", established=True)
    world.tick(1.0)
    assert not [a for a in world.public_state().alerts if a.kind == "WAKE"]


def test_metar_is_simulated_and_matches_sim_wind_and_qnh(world):
    world.load_scenario("demo", 42)
    m = world.metar()
    assert m["source"] == "SIMULATED" and m["qnh_hpa"] == world.qnh == 1013
    assert m["raw"] == "EGKK 141750Z 00000KT 9999 SCT035 11/06 Q1013"
    world.wind_from, world.wind_kt, world.qnh = 244.0, 14.0, 998
    m = world.metar()
    assert m["wind_dir_deg"] == 240 and m["wind_kt"] == 14 and "24014KT" in m["raw"]
    assert m["raw"].endswith("Q0998")


def test_metar_defaults_to_cavok_for_empty_scenarios(world):
    empty(world)
    assert world.metar()["raw"] == "EGKK 141750Z 00000KT CAVOK 12/07 Q1013"


def test_state_message_carries_simulated_metar(world):
    world.load_scenario("demo", 42)
    st = StateMsg.model_validate(world.public_state().model_dump())
    assert st.metar is not None and st.metar.source == "SIMULATED"
    assert st.metar.raw == world.metar()["raw"] and st.metar.qnh_hpa == world.qnh
