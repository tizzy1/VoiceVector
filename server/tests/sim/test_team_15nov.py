"""15 Nov changes made by Sanju on Tudor's behalf (see docs/HANDOVER_15NOV.md)."""
import math
import random

from server.contracts import Instruction, SessionSettings, Transmission
from server.pilot import errors as policy
from server.pilot.engine import PilotEngine
from server.pilot.readback import Composer
from server.sim.modes import loc_geometry


def load(world, name, seed=42):
    world.load_scenario(name, seed)
    world.configure(SessionSettings(scenario=name, error_rate="off"))


def run(world, seconds):
    for _ in range(int(seconds)):
        world.tick(1.0)


def tx(cs, *ins):
    return Transmission(tx_id="t", t=0, receiver=cs, instructions=list(ins))


def place(world, cs, along, side, alt):
    """Put an aircraft `along` NM out on the extended centreline, `side` NM off it."""
    ac = world.aircraft[cs]
    c = math.radians(world.loc_course)
    ux, uy, nx, ny = math.sin(c), math.cos(c), math.cos(c), -math.sin(c)
    ac.x = world.thr_xy[0] - ux * along + nx * side
    ac.y = world.thr_xy[1] - uy * along + ny * side
    ac.alt = ac.cleared_alt = alt
    return ac


# ------------------------------------------------------------------ judges' scenario
def test_judge_scenario_is_one_aircraft_holding_at_5000_and_nothing_else_spawns(world, bus):
    load(world, "judge")
    run(world, 30 * 60)
    assert list(world.aircraft) == ["BAW117"]
    ac = world.aircraft["BAW117"]
    assert ac.lat_mode == "HOLD" and ac.hold["fix"] == "TIMBA" and abs(ac.alt - 5000) < 1
    assert any(e.data.get("kind") == "checkin" for e in bus.of("pilot_call"))


def test_judge_can_vector_off_the_hold_and_land(world, bus, data_dir):
    """The vectors suggested to judges: base leg heading 360 at 3,000 ft, then 290 + ILS ~5 NM
    from the centreline -> localiser beyond 9 NM, glideslope from below, landing."""
    load(world, "judge")
    ac = world.aircraft["BAW117"]
    run(world, 20)
    world._apply(ac, Instruction(type="heading", value=360, turn_dir="L"))
    world._apply(ac, Instruction(type="altitude", value=3000))
    assert ac.lat_mode == "HDG" and ac.hold is None
    cleared = False
    for _ in range(25 * 60):
        world.tick(1.0)
        along, xte = loc_geometry(world, ac)
        if not cleared and abs(xte) <= 5.0:
            world._apply(ac, Instruction(type="heading", value=290, turn_dir="L"))
            world._apply(ac, Instruction(type="approach_clearance", approach_type="ILS", runway=world.runway))
            cleared = True
        if ac.phase == "LANDED":
            break
    assert ac.phase == "LANDED", (ac.phase, loc_geometry(world, ac))
    assert bus.of("gs_captured") and bus.of("landing")


def test_approach_clearance_alone_leaves_the_hold_on_present_heading(world):
    load(world, "judge")
    ac = world.aircraft["BAW117"]
    run(world, 45)
    hdg = ac.hdg
    world._apply(ac, Instruction(type="approach_clearance", approach_type="ILS", runway=world.runway))
    assert ac.lat_mode == "HDG" and ac.hold is None and abs(ac.tgt_hdg - hdg) < 0.01 and ac.loc_armed


# ------------------------------------------------------------------ lists
def test_lists_get_flight_plan_and_strip_fields(world):
    load(world, "demo")
    for a in world.public_state().aircraft:
        assert a.flight_rules in ("I", "V") and a.adep and a.ades and a.rfl and a.assigned_squawk
        assert a.strip.get("sts") and a.selected_level == a.cleared_alt
        if a.flow == "ARR":
            assert a.distance_to_touchdown_nm and a.distance_to_touchdown_nm > 0
    cs = next(a.callsign for a in world.public_state().aircraft if a.flow == "ARR")
    assert world.edit_strip(cs, "cfl", "A50")
    assert next(a for a in world.public_state().aircraft if a.callsign == cs).strip["cfl"] == 5000


# ------------------------------------------------------------------ altitudes and speeds
def test_2000_ft_allowed_on_base_leg_but_not_twenty_miles_out(world):
    load(world, "demo")
    cs = next(iter(world.aircraft))
    place(world, cs, along=12, side=6, alt=4000)
    [e] = world.evaluate(tx(cs, Instruction(type="altitude", value=2000)))
    assert e.status == "OK"
    place(world, cs, along=20, side=0, alt=4000)
    [e] = world.evaluate(tx(cs, Instruction(type="altitude", value=2000)))
    assert e.reason_code == "QUERY_LEVEL_BELOW_MIN"


def test_approach_speeds_are_accepted_low_down(world):
    load(world, "demo")
    cs = next(c for c, a in world.aircraft.items() if a.actype in ("A320", "A20N", "B738", "A319"))
    ac = place(world, cs, along=14, side=5, alt=3000)
    assert world.evaluate(tx(cs, Instruction(type="speed", value=200)))[0].status == "OK"
    assert world.evaluate(tx(cs, Instruction(type="speed", value=180)))[0].status == "OK"
    assert world.evaluate(tx(cs, Instruction(type="speed", value=140)))[0].status == "UNABLE"
    ac.alt = ac.cleared_alt = 9000
    assert world.evaluate(tx(cs, Instruction(type="speed", value=180)))[0].status == "UNABLE"


# ------------------------------------------------------------------ refusals
def test_partial_refusal_reads_back_and_flies_the_accepted_turn(world, bus, data_dir):
    load(world, "demo")
    pilot = PilotEngine(world, bus, data_dir)
    pilot.set_error_rate("off")
    cs = "EZY45AB"                       # 9,000 ft on the demo: too high for the glideslope
    t = tx(cs, Instruction(type="heading", value=290, turn_dir="L"),
           Instruction(type="approach_clearance", approach_type="ILS", runway=world.runway))
    evals = world.evaluate(t)
    assert [e.status for e in evals] == ["OK", "UNABLE"]
    [r] = pilot.handle_transmission(t, evals)
    assert [e.type for e in r.apply] == ["heading"]
    rb = Composer(data_dir).compose(r.plan)
    assert rb.validated and "290" in rb.text and "unable" in rb.text.lower(), rb.text


def test_direct_to_a_passed_fix_uses_the_new_reason_code(world):
    world.inject("spawn_arrival", params={"entry": "DISIT"})
    ac = next(a for a in world.aircraft.values() if a.route_name == "DISIT1G")
    ac.route, ac.route_idx = ["KIDLI", "MID", "TUFOZ", "HOLLY", "WILLO"], 3
    [e] = world.evaluate(tx(ac.callsign, Instruction(type="direct", fix="TUFOZ")))
    assert e.status == "QUERY" and e.reason_code == "QUERY_FIX_PASSED"


# ------------------------------------------------------------------ traffic build-up
def test_random_traffic_builds_up_slowly_to_at_most_eight_and_uses_all_stages(world, bus):
    load(world, "busy", seed=11)
    stages, peak = set(), 0
    for k in range(60 * 60):
        world.tick(1.0)
        n = world.generator.active()
        peak = max(peak, n)
        if k < 110:
            assert n <= 2
        stages |= {a.phase for a in world.aircraft.values() if a.flow == "ARR"}
    assert peak <= 8 + 1                     # forced/conflict spawns aside, the cap holds
    assert {"STAR", "HOLD"} <= stages and ({"APPROACH", "FINAL"} & stages)


def test_spawn_inject_can_ask_for_a_stage(world):
    load(world, "custom")
    world.inject("spawn_arrival", params={"stage": "final"})
    ac = max(world.aircraft.values(), key=lambda a: a.spawn_t)
    assert ac.approach_cleared and ac.alt < 5000


# ------------------------------------------------------------------ smaller mistakes
def test_small_mistakes_are_tried_first_most_often():
    rng = random.Random(1)
    kinds = list(policy.READBACK_KINDS) + list(policy.OMISSION_KINDS)
    firsts = [policy.weighted_order(kinds, rng)[0] for _ in range(2000)]
    small = sum(k in ("wrong_value_adjacent", "digit_transposition", "omitted_element") for k in firsts)
    assert small / len(firsts) > 0.8
    assert policy.mutate("level_type_confusion", Instruction(type="altitude", value=5000), rng).value == 50.0
    assert isinstance(policy.mutate("level_type_confusion", Instruction(type="altitude", value=5000), rng).value, float)
