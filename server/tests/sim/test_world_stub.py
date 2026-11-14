import math

from server.contracts import (
    ClearanceElement, Instruction, StateMsg, Transmission, WorldAPI, WorldSnapshot,
)
from server.sim.world import World


def el(**kw) -> ClearanceElement:
    i = Instruction(**kw)
    return ClearanceElement(type=i.type, intended=i, readback=i, flown=i)


def ac(world, cs="BAW117"):
    return world.aircraft[cs]


def test_has_every_contract_method(world):
    for name in ("configure", "load_scenario", "tick", "public_state", "snapshot", "callsigns",
                 "fixes", "evaluate", "schedule_apply", "set_transmitting", "inject"):
        assert callable(getattr(world, name)), name
    assert isinstance(world.t, float)
    _: WorldAPI = world


def test_demo_scenario_loads_and_state_is_complete(world, bus):
    state = world.public_state()
    assert isinstance(state, StateMsg)
    assert [a.callsign for a in state.aircraft] == [
        "BAW117", "EZY45AB", "DLH4KA", "UAE15", "WZZ4471"]
    baw = state.aircraft[0]
    assert baw.spoken_plain == "British Airways 117"
    assert baw.plain_status == "level FL080 · 220 kt"
    assert baw.route_name == "GWC1G" and baw.wake == "M"
    assert len(bus.of("spawn")) == 5
    StateMsg.model_validate(state.model_dump())
    assert isinstance(world.snapshot(), WorldSnapshot)
    assert world.snapshot().aircraft["BAW117"]["persona"] == "crisp_airline"


def test_latlon_matches_projection(world):
    w = world.public_state().aircraft[0]
    willo = world.egkk["fixes"]["WILLO"]          # BAW117 starts at WILLO
    assert abs(w.lat - willo["lat"]) < 1e-3 and abs(w.lon - willo["lon"]) < 1e-3


def test_callsigns_spoken_forms(world):
    cs = {c.icao: c for c in world.callsigns()}
    assert cs["BAW117"].telephony == "Speedbird"
    assert cs["BAW117"].spoken == "speedbird one one seven"
    assert cs["BAW117"].spoken_digits == "speedbird 117"
    assert cs["EZY45AB"].spoken == "easy four five alpha bravo"
    assert cs["EZY45AB"].spoken_digits == "easy 45 alpha bravo"
    assert cs["DLH4KA"].spoken == "lufthansa four kilo alpha"
    assert "WILLO" in world.fixes() and "TIMBA" in world.fixes()


def test_flies_straight_when_not_instructed(world):
    a = ac(world)
    x0, y0, gs = a.x, a.y, a.gs
    for _ in range(60):
        world.tick(1.0)
    assert world.t == 60.0
    assert a.hdg == 75 and a.alt == 8000
    assert math.isclose(math.hypot(a.x - x0, a.y - y0), gs / 60.0, rel_tol=1e-6)


def test_turn_left_is_monotonic_and_rate_limited(world):
    a = ac(world)
    a.hdg = a.tgt_hdg = 270.0
    world.schedule_apply("BAW117", [el(type="heading", value=240, turn_dir="L")], at=world.t)
    prev = a.hdg
    for _ in range(40):
        world.tick(1.0)
        step = (prev - a.hdg) % 360
        assert 0 <= step <= 3.0 + 1e-9
        prev = a.hdg
    assert a.hdg == 240 and a.turn_dir is None and a.phase == "VECTORS"


def test_turn_right_goes_the_long_way(world):
    a = ac(world)
    a.hdg = a.tgt_hdg = 270.0
    world.schedule_apply("BAW117", [el(type="heading", value=180, turn_dir="R")], at=world.t)
    seen_north = False
    for _ in range(200):
        world.tick(1.0)
        seen_north = seen_north or a.hdg < 90
    assert seen_north and a.hdg == 180


def test_descend_levels_off_without_overshoot(world):
    a = ac(world)
    world.schedule_apply("BAW117", [el(type="altitude", value=4000, verb="descend")], at=world.t)
    low = a.alt
    for _ in range(400):
        world.tick(1.0)
        low = min(low, a.alt)
    assert a.alt == 4000 and low == 4000 and a.vs == 0
    assert world.public_state().aircraft[0].plain_status == "level 4,000 ft · 220 kt"


def test_flight_level_and_speed(world):
    world.schedule_apply("BAW117", [el(type="flight_level", value=70, verb="descend"),
                                    el(type="speed", value=180)], at=world.t)
    world.tick(1.0)
    a = ac(world)
    assert a.cleared_alt == 7000 and a.tgt_ias == 180
    assert world.public_state().aircraft[0].plain_status.startswith("descending to FL070")
    for _ in range(120):
        world.tick(1.0)
    assert a.ias == 180 and a.alt == 7000


def test_apply_waits_for_its_time_and_emits_event(world, bus):
    world.schedule_apply("BAW117", [el(type="heading", value=100)], at=world.t + 5)
    for _ in range(4):
        world.tick(1.0)
    assert ac(world).tgt_hdg == 75 and not bus.of("instruction_applied")
    world.tick(1.0)
    assert ac(world).tgt_hdg == 100
    e = bus.of("instruction_applied")[0]
    assert e.callsign == "BAW117" and e.data["element_type"] == "heading"


def test_evaluate_is_ok_and_pure(world):
    tx = Transmission(tx_id="t1", t=0, receiver="BAW117",
                      instructions=[Instruction(type="heading", value=240),
                                    Instruction(type="altitude", value=4000)])
    before = world.snapshot().model_dump()
    evals = world.evaluate(tx)
    assert [(e.instr_idx, e.status) for e in evals] == [(0, "OK"), (1, "OK")]
    assert world.snapshot().model_dump() == before


def test_transmitting_inject_and_unknown_callsign(world, bus):
    world.set_transmitting("BAW117", True)
    assert world.public_state().aircraft[0].transmitting
    world.set_transmitting("NOPE1", True)
    world.schedule_apply("NOPE1", [el(type="heading", value=100)], at=world.t)
    world.inject("mayday", "DLH4KA", {"reason": "engine failure"})
    world.tick(1.0)
    dlh = next(a for a in world.public_state().aircraft if a.callsign == "DLH4KA")
    assert dlh.emergency and dlh.squawk == "7700"
    assert bus.of("inject")[0].data["kind"] == "mayday"


def test_replays_identically(bus, data_dir):
    def run() -> dict:
        w = World(bus, data_dir)
        w.load_scenario("demo", 7)
        w.schedule_apply("UAE15", [el(type="heading", value=10, turn_dir="R")], at=3)
        for _ in range(90):
            w.tick(1.0)
        return w.public_state().model_dump()
    assert run() == run()


def test_other_scenarios_start_empty(world):
    world.load_scenario("easy", 1)
    assert world.public_state().aircraft == [] and world.t == 0.0
