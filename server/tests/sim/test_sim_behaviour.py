"""Behaviour tests (SIM_SPEC §8) for STAR following, holds, ILS, generator and STCA."""
import math

from server.contracts import ClearanceElement, Instruction, StateMsg
from server.sim.aircraft import AircraftState
from server.sim.kinematics import tas_of
from server.sim.modes import glidepath_alt, loc_geometry
from server.sim.world import World


def el(**kw) -> ClearanceElement:
    i = Instruction(**kw)
    return ClearanceElement(type=i.type, intended=i, readback=i, flown=i)


def give(world, cs, *elements):
    world.schedule_apply(cs, list(elements), at=world.t)


def run(world, seconds, until=None):
    for _ in range(seconds):
        world.tick(1.0)
        if until and until():
            return True
    return until is None


def place(world, cs="TST1", x=0.0, y=0.0, alt=3000.0, hdg=257.6, ias=200.0, actype="A320", **kw):
    tas = tas_of(ias, alt)
    ac = AircraftState(callsign=cs, spoken=cs.lower(), spoken_plain=cs, actype=actype, wake="M",
                       persona="crisp_airline", flow="ARR", phase="VECTORS", x=x, y=y, alt=alt,
                       hdg=hdg, track=hdg, ias=ias, tas=tas, gs=tas, tgt_hdg=hdg,
                       cleared_alt=alt, **kw)
    world.aircraft[cs] = ac
    return ac


def on_final(world, dist_nm, offset_nm=0.0):
    """Point `dist_nm` out on the 26L approach, `offset_nm` to the right of the centreline."""
    c = math.radians(world.loc_course)
    return (-dist_nm * math.sin(c) + offset_nm * math.cos(c),
            -dist_nm * math.cos(c) - offset_nm * math.sin(c))


def empty(world):
    world.load_scenario("custom", 1)
    world.settings = world.settings.model_copy(update={"arr_per_hr": 0, "dep_per_hr": 0})
    return world


# ------------------------------------------------------------- STAR / hold
def test_star_flown_to_willo_then_holds_and_calls(world, bus):
    world.script = []                                   # hold only: no scripted MAYDAY
    dlh = world.aircraft["DLH4KA"]                      # at HOLLY, next fix WILLO
    assert dlh.lat_mode == "LNAV" and world.public_state().aircraft[2].next_fix == "WILLO"
    assert run(world, 300, lambda: dlh.lat_mode == "HOLD")
    calls = [e for e in bus.of("pilot_call") if e.callsign == "DLH4KA"]
    assert [c.data for c in calls] == [{"kind": "no_further_clearance", "fix": "WILLO"}]
    assert [e.data["fix"] for e in bus.of("hold_entered") if e.callsign == "DLH4KA"] == ["WILLO"]
    assert dlh.alt == 11000                             # holds its level, never descends alone
    wx, wy = world.fix_xy("WILLO")
    far = 0.0
    for _ in range(600):                                # several racetracks: stays near the fix
        world.tick(1.0)
        far = max(far, math.hypot(dlh.x - wx, dlh.y - wy))
    assert dlh.lat_mode == "HOLD" and far < 9
    assert dlh.ias <= 221
    status = next(a for a in world.public_state().aircraft if a.callsign == "DLH4KA")
    assert status.plain_status == "holding at WILLO · FL110" and status.phase == "HOLD"


def test_heading_takes_aircraft_out_of_the_hold(world):
    dlh = world.aircraft["DLH4KA"]
    run(world, 300, lambda: dlh.lat_mode == "HOLD")
    give(world, "DLH4KA", el(type="heading", value=70, turn_dir="R"))
    run(world, 150)
    assert dlh.lat_mode == "HDG" and dlh.phase == "VECTORS" and dlh.hdg == 70 and dlh.hold is None


def test_direct_and_resume_nav(world):
    uae = world.aircraft["UAE15"]
    give(world, "UAE15", el(type="heading", value=270))
    run(world, 30)
    assert uae.lat_mode == "HDG"
    give(world, "UAE15", el(type="direct", fix="WILLO"))
    run(world, 2)
    assert uae.lat_mode == "LNAV" and uae.route == ["WILLO"] and uae.phase == "STAR"
    give(world, "UAE15", el(type="heading", value=270))
    run(world, 2)
    give(world, "UAE15", el(type="resume_nav"))
    run(world, 2)
    assert uae.lat_mode == "LNAV" and uae.route[-1] == "WILLO"


def test_conditional_after_fix(world):
    dlh = world.aircraft["DLH4KA"]
    give(world, "DLH4KA", el(type="flight_level", value=80, verb="descend",
                             condition={"after_fix": "WILLO"}))
    run(world, 20)
    assert dlh.cleared_alt == 11000 and len(dlh.conditionals) == 1
    run(world, 300, lambda: dlh.lat_mode == "HOLD")
    assert dlh.cleared_alt == 8000 and dlh.conditionals == []


# -------------------------------------------------------------------- ILS
def test_ils_30_degree_intercept_captures_and_lands(bus, data_dir):
    w = empty(World(bus, data_dir))
    x, y = on_final(w, 16.0, offset_nm=3.0)             # right of the centreline, 16 NM out
    ac = place(w, x=x, y=y, alt=3000, hdg=w.loc_course - 30, ias=200)
    give(w, "TST1", el(type="approach_clearance", approach_type="ILS", runway="26L"),
         el(type="report", report_what="established"))
    assert run(w, 300, lambda: ac.lat_mode == "LOC")
    assert ac.established and ac.phase == "APPROACH" and ac.alt == 3000
    assert [e.data["kind"] for e in bus.of("pilot_call")] == ["established"]
    assert run(w, 300, lambda: ac.vert_mode == "GS")
    along, xte = loc_geometry(w, ac)
    assert abs(ac.alt - glidepath_alt(w, along)) < 60 and ac.phase == "FINAL"
    status = w.public_state().aircraft[0]
    assert status.plain_status.startswith("established ILS 26L")
    assert status.approach_cleared == "ILS 26L"
    seen = {}
    while ac.phase != "LANDED" and w.t < 1500:
        w.tick(1.0)
        along, xte = loc_geometry(w, ac)
        if 5 < along < 7:
            seen["mid"] = ac.ias
        if 0.2 < along < 2:
            seen["short"] = (ac.ias, abs(xte), ac.alt - glidepath_alt(w, along))
    assert ac.phase == "LANDED" and [e.callsign for e in bus.of("landing")] == ["TST1"]
    assert abs(seen["mid"] - 160) < 1
    ias, xte, gp_err = seen["short"]
    assert abs(ias - w.perf["A320"]["vapp_kt"]) < 1 and xte < 0.05 and abs(gp_err) < 30
    assert abs(ac.x) < 0.2 and abs(ac.y) < 0.2          # touched down at the threshold
    run(w, 31)
    assert "TST1" not in w.aircraft


def test_no_glideslope_capture_from_above(bus, data_dir):
    w = empty(World(bus, data_dir))
    x, y = on_final(w, 8.0)
    ac = place(w, x=x, y=y, alt=6000, ias=180)          # glidepath there is ~2,700 ft
    give(w, "TST1", el(type="approach_clearance", approach_type="ILS", runway="26L"))
    run(w, 120)
    assert ac.lat_mode == "LOC" and ac.vert_mode == "ALT" and ac.alt == 6000
    assert not bus.of("gs_captured")
    run(w, 200)
    assert bus.of("go_around") and ac.phase == "GO_AROUND" and ac.cleared_alt == 3000
    assert [e for e in bus.of("pilot_call")
            if e.callsign == ac.callsign][-1].data["kind"] == "going_around"


def test_60_degree_intercept_flies_through_the_localiser(bus, data_dir):
    w = empty(World(bus, data_dir))
    x, y = on_final(w, 10.0, offset_nm=4.0)
    ac = place(w, x=x, y=y, alt=3000, hdg=w.loc_course - 60, ias=200)
    give(w, "TST1", el(type="approach_clearance", approach_type="ILS", runway="26L"))
    run(w, 200)
    assert ac.lat_mode == "HDG" and not ac.established and not bus.of("loc_captured")
    assert loc_geometry(w, ac)[1] < -1                  # now well left of the centreline


def test_localiser_only_then_descend_on_glidepath(bus, data_dir):
    w = empty(World(bus, data_dir))
    x, y = on_final(w, 12.0, offset_nm=0.5)
    ac = place(w, x=x, y=y, alt=2500, hdg=w.loc_course - 20, ias=180)
    give(w, "TST1", el(type="intercept_localiser", runway="26L"))
    run(w, 60)
    assert ac.lat_mode == "LOC" and ac.vert_mode == "ALT" and ac.approach_cleared is None
    give(w, "TST1", el(type="descend_glidepath"))
    assert run(w, 200, lambda: ac.vert_mode == "GS")


def test_heading_breaks_off_an_established_aircraft(bus, data_dir):
    w = empty(World(bus, data_dir))
    x, y = on_final(w, 9.0)
    ac = place(w, x=x, y=y, alt=2500, ias=180)
    give(w, "TST1", el(type="approach_clearance", approach_type="ILS", runway="26L"))
    run(w, 60)
    assert ac.vert_mode == "GS"
    give(w, "TST1", el(type="heading", value=180, turn_dir="L"))
    run(w, 60)
    assert ac.lat_mode == "HDG" and ac.hdg == 180 and not ac.established
    assert ac.approach_cleared is None and ac.vert_mode == "ALT" and ac.phase == "VECTORS"


def test_contact_tower_goes_off_frequency_but_keeps_flying(bus, data_dir):
    w = empty(World(bus, data_dir))
    x, y = on_final(w, 6.0)
    ac = place(w, x=x, y=y, alt=1800, ias=160)
    give(w, "TST1", el(type="approach_clearance", approach_type="ILS", runway="26L"),
         el(type="contact", unit="tower", frequency="124.225"))
    run(w, 5)
    assert not ac.on_frequency and w.callsigns() == []
    assert bus.of("handoff")[0].data == {"unit": "tower", "frequency": "124.225"}
    assert run(w, 300, lambda: ac.phase == "LANDED")


# ------------------------------------------------------------- kinematics
def test_descend_and_reduce_halves_the_deceleration(world):
    a = world.aircraft["BAW117"]
    give(world, "BAW117", el(type="altitude", value=4000, verb="descend"),
         el(type="speed", value=180))
    run(world, 10)
    before = a.ias
    run(world, 10)
    assert a.vs < -1000 and math.isclose(before - a.ias, 4.0, abs_tol=0.01)     # 0.4 kt/s


def test_250_below_fl100_and_expedite(bus, data_dir):
    w = empty(World(bus, data_dir))
    ac = place(w, x=-30, y=30, alt=13000, hdg=90, ias=280, actype="A321")
    ac.tgt_ias = 280.0
    give(w, "TST1", el(type="flight_level", value=80, verb="descend"),
         el(type="expedite", verb="descend"))
    run(w, 5)
    assert abs(ac.vs) > w.perf["A321"]["descent_fpm"] * 1.4
    run(w, 300)
    assert ac.alt == 8000 and ac.ias == 250 and not ac.expedite


def test_wind_drifts_track_and_lnav_corrects(bus, data_dir):
    from server.sim.geo import bearing, hdg_diff, wind_vector
    w = empty(World(bus, data_dir))
    w.wind = wind_vector(350, 40)
    ac = place(w, x=20, y=-20, alt=8000, hdg=270, ias=220)
    run(w, 5)
    assert hdg_diff(270, ac.track) < -3 and ac.gs != ac.tas          # blown south of west
    give(w, "TST1", el(type="direct", fix="WILLO"))
    run(w, 60)
    assert abs(hdg_diff(ac.track, bearing(ac.x, ac.y, *w.fix_xy("WILLO")))) < 1


# -------------------------------------------------------------- generator
def test_generator_spawns_separated_arrivals_with_checkins(bus, data_dir):
    w = World(bus, data_dir)
    w.settings = w.settings.model_copy(update={"arr_per_hr": 30, "dep_per_hr": 0})
    w.load_scenario("custom", 3)
    bus.events.clear()
    worst = 99.0
    for _ in range(900):
        w.tick(1.0)
        acs = list(w.aircraft.values())
        for i, a in enumerate(acs):
            for b in acs[i + 1:]:
                if abs(a.alt - b.alt) < 1000:
                    worst = min(worst, math.hypot(a.x - b.x, a.y - b.y))
    spawns = bus.of("spawn")
    checkins = [e for e in bus.of("pilot_call") if e.data["kind"] == "checkin"]
    assert 5 <= len(spawns) <= 10 and len(checkins) == len(spawns)
    c = checkins[0].data
    assert c["atis"] == "K" and c["level_fl"] >= 90
    assert c["route"] in w.egkk["procedures"]["STAR"]["26L"]
    assert worst >= 3.0                                 # nothing spawned into a conflict
    for ac in w.aircraft.values():
        assert ac.actype in w.perf and ac.route[-1] in ("WILLO", "TIMBA")
        assert math.hypot(ac.x, ac.y) < 75
    StateMsg.model_validate(w.public_state().model_dump())
    assert len({c.spoken for c in w.callsigns()}) == len(w.aircraft)


def test_generator_is_seeded(bus, data_dir):
    def sample(seed):
        w = World(bus, data_dir)
        w.settings = w.settings.model_copy(update={"arr_per_hr": 30, "dep_per_hr": 0})
        w.load_scenario("custom", seed)
        for _ in range(600):
            w.tick(1.0)
        return w.public_state().model_dump()
    assert sample(5) == sample(5) and sample(5) != sample(6)


def test_demo_stays_scripted_for_five_minutes_then_generates(world, bus):
    run(world, 299)
    assert len(bus.of("spawn")) == 5
    run(world, 400)
    assert len(bus.of("spawn")) > 5


def test_spawn_arrival_inject_and_backlog(bus, data_dir):
    w = empty(World(bus, data_dir))
    w.inject("spawn_arrival", params={"entry": "GWC"})
    assert len(w.aircraft) == 1 and next(iter(w.aircraft.values())).route_name == "GWC1G"
    assert w.public_state().backlog == {"ARR": 0, "DEP": 0}


# ------------------------------------------------------------------- STCA
def test_converging_pair_predicted_then_loss_of_separation(bus, data_dir):
    w = empty(World(bus, data_dir))
    place(w, "AAA1", x=-12, y=20, alt=5000, hdg=90, ias=220)
    place(w, "BBB2", x=12, y=20, alt=5000, hdg=270, ias=220)
    run(w, 60)
    kinds = [a.kind for a in w.public_state().alerts]
    assert kinds == ["STCA_PRED"]
    assert run(w, 200, lambda: any(a.kind == "STCA_LOS" for a in w.alerts.values()))
    los = next(a for a in w.public_state().alerts if a.kind == "STCA_LOS")
    assert los.callsigns == ["AAA1", "BBB2"] and los.severity == "HIGH" and "NM" in los.message
    assert all(a.kind != "STCA_PRED" for a in w.alerts.values())
    run(w, 200)
    assert not w.alerts
    starts = [e.data["kind"] for e in bus.of("alert_start")]
    ends = [e.data["kind"] for e in bus.of("alert_end")]
    assert starts == ["STCA_PRED", "STCA_LOS"] and ends == ["STCA_PRED", "STCA_LOS"]


def test_vertically_separated_and_in_trail_pairs_do_not_alert(bus, data_dir):
    w = empty(World(bus, data_dir))
    place(w, "AAA1", x=-12, y=20, alt=5000, hdg=90, ias=220)
    place(w, "BBB2", x=12, y=20, alt=6000, hdg=270, ias=220)
    xa, ya = on_final(w, 9.0)
    xb, yb = on_final(w, 14.0)
    for cs, x, y in (("FIN1", xa, ya), ("FIN2", xb, yb)):
        place(w, cs, x=x, y=y, alt=2000, ias=180)
        give(w, cs, el(type="approach_clearance", approach_type="ILS", runway="26L"))
    run(w, 150)
    assert not w.alerts and not bus.of("alert_start")
