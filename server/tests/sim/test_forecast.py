"""Probabilistic conflict forecast (SIM_SPEC §3.6): advisory, seeded, intent-only, bounded."""
import statistics
import time

from server.contracts import ClearanceElement, Instruction
from server.sim import forecast as fc
from server.sim.aircraft import AircraftState
from server.sim.kinematics import tas_of
from server.sim.world import World


def place(w, cs, x, y, alt, hdg, ias=220.0, **kw):
    tas = tas_of(ias, alt)
    w.aircraft[cs] = AircraftState(
        callsign=cs, spoken=cs.lower(), spoken_plain=cs, actype="A320", wake="M",
        persona="crisp_airline", flow="ARR", phase="VECTORS", x=x, y=y, alt=alt, hdg=hdg,
        track=hdg, ias=ias, tas=tas, gs=tas, tgt_hdg=hdg, cleared_alt=alt, **kw)
    return w.aircraft[cs]


def empty(bus, data_dir, seed=1):
    w = World(bus, data_dir)
    w.load_scenario("custom", seed)
    w.settings = w.settings.model_copy(update={"arr_per_hr": 0, "dep_per_hr": 0})
    return w


def el(intended, flown=None):
    return ClearanceElement(type=intended.type, intended=intended, readback=intended,
                            flown=flown or intended)


def head_on(w, offset_nm=0.0, dalt=0.0):
    place(w, "AAA1", -8, 20, 5000, 90)
    place(w, "BBB2", 8, 20 + offset_nm, 5000 + dalt, 270)


def only(w):
    w.forecaster.update(w, force=True)
    return w.forecasts


def test_head_on_same_level_is_a_certain_conflict_with_paths(bus, data_dir):
    w = empty(bus, data_dir)
    head_on(w)
    [f] = only(w)
    assert f["callsigns"] == ["AAA1", "BBB2"] and f["p_conflict"] == 1.0
    assert 80 <= f["t_median_s"] <= 110 and f["min_sep_nm"] < 1
    assert f["advisory"] and f["rollouts"] == 64 and f["horizon_s"] == 120
    assert f["threshold"] == {"nm": 3.0, "ft": 1000.0}
    assert set(f["paths"]) == {"AAA1", "BBB2"} and len(f["paths"]["AAA1"]) == fc.PATH_SAMPLES


def test_clear_geometries_are_not_reported(bus, data_dir):
    for kw in ({"offset_nm": 4.0}, {"dalt": 1000.0}, {"dalt": 2000.0}):
        w = empty(bus, data_dir)
        head_on(w, **kw)
        assert only(w) == [], kw


def test_borderline_crossing_gives_an_uncertain_probability(bus, data_dir):
    w = empty(bus, data_dir)
    place(w, "AAA1", -8, 20, 5000, 90)
    place(w, "BBB2", 0, 31, 5000, 180)                  # crosses ~3 NM behind
    [f] = only(w)
    assert 0.15 <= f["p_conflict"] <= 0.9
    assert f["paths"]


def test_descent_through_anothers_level_is_forecast(bus, data_dir):
    w = empty(bus, data_dir)
    head_on(w, dalt=3000.0)
    w.aircraft["BBB2"].cleared_alt = 3000.0
    [f] = only(w)
    assert f["p_conflict"] > 0.5


def test_pending_clearance_uses_the_controllers_intent_not_the_planned_error(bus, data_dir):
    w = empty(bus, data_dir)
    head_on(w)
    turn_away = Instruction(type="heading", value=360, turn_dir="L")
    wrong = Instruction(type="heading", value=90)        # pilot will keep flying at the traffic
    w.schedule_apply("AAA1", [el(turn_away, flown=wrong)], at=w.t + 3)
    assert only(w) == []


def test_pending_level_change_resolves_or_does_not_by_timing(bus, data_dir):
    w = empty(bus, data_dir)
    head_on(w)
    w.schedule_apply("AAA1", [el(Instruction(type="flight_level", value=70, verb="climb"))],
                     at=w.t + 3)
    assert only(w) == []
    w = empty(bus, data_dir)
    head_on(w)
    w.schedule_apply("AAA1", [el(Instruction(type="altitude", value=6000, verb="climb"))],
                     at=w.t + 95)                        # too late to help
    [f] = only(w)
    assert f["p_conflict"] > 0.9


def test_in_trail_on_the_localiser_is_not_a_conflict(bus, data_dir):
    import math
    w = empty(bus, data_dir)
    c = math.radians(w.loc_course)
    for cs, dist in (("FIN1", 8.0), ("FIN2", 11.0)):
        ac = place(w, cs, -dist * math.sin(c), -dist * math.cos(c), 2500, w.loc_course, 170)
        ac.lat_mode, ac.loc_armed, ac.established = "LOC", True, True
    assert only(w) == []


def test_forecast_is_pure_and_silent(bus, data_dir):
    w = empty(bus, data_dir)
    head_on(w)
    w.schedule_apply("BBB2", [el(Instruction(type="speed", value=180))], at=w.t + 10)
    before, n_events, pending = w.snapshot().model_dump(), len(bus.events), list(w._pending)
    only(w)
    assert w.snapshot().model_dump() == before and len(bus.events) == n_events
    assert w._pending == pending


def test_seeded_and_replayable(bus, data_dir):
    def run(seed):
        w = empty(bus, data_dir, seed)
        place(w, "AAA1", -8, 20, 5000, 90)
        place(w, "BBB2", 0, 31, 5000, 180)
        for _ in range(6):
            w.tick(1.0)
        return w.forecasts
    assert run(3) == run(3)
    assert run(3)[0]["p_conflict"] != run(4)[0]["p_conflict"] or run(3) != run(4)


def test_recomputes_every_two_sim_seconds(bus, data_dir):
    w = empty(bus, data_dir)
    head_on(w)
    stamps = []
    for _ in range(6):
        w.tick(1.0)
        stamps.append(w.forecasts[0]["computed_t"])
    assert stamps == [1.0, 1.0, 3.0, 3.0, 5.0, 5.0]


def test_busy_traffic_stays_within_budget(bus, data_dir):
    w = World(bus, data_dir)
    w.load_scenario("busy", 42)
    ms = []
    for _ in range(900):
        w.tick(1.0)
        if w.forecaster.last_t == w.t:
            ms.append(w.forecaster.last_ms)
    assert statistics.median(ms) < 30.0                 # target 30 ms per pass at 40 aircraft


def test_forty_aircraft_pass_cost(bus, data_dir):
    w = empty(bus, data_dir)
    for k in range(40):                                  # dense block: every pair a candidate
        place(w, f"T{k:02d}", (k % 8) * 2.5, (k // 8) * 2.5, 4000 + (k % 3) * 1000,
              (k * 45) % 360)
    w.forecaster.update(w, force=True)                   # warm-up
    passes = []
    for _ in range(5):
        t0 = time.perf_counter()
        w.forecaster.update(w, force=True)
        passes.append((time.perf_counter() - t0) * 1000)
    per_pass = min(passes)                               # best of 5: immune to a busy machine
    assert per_pass < 60.0, passes                       # generous CI bound; target is 30 ms
