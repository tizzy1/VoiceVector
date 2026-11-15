"""World.evaluate: unable / query rules (SIM_SPEC §2.1–2.3, tests 6, 11, 12)."""
from server.contracts import Instruction, Transmission
from server.sim.modes import glidepath_alt
from server.tests.sim.test_sim_behaviour import on_final, place


def ev(world, cs, *instrs):
    tx = Transmission(tx_id="t", t=world.t, receiver=cs, instructions=list(instrs))
    return [(e.status, e.reason_code, e.reason_values) for e in world.evaluate(tx)]


def I(**kw):
    return Instruction(**kw)


def test_descend_2000_twenty_miles_out_without_approach_is_queried(world):
    place(world, "T1", x=20.0, y=0.0, alt=5000, hdg=270)
    [(s, code, v)] = ev(world, "T1", I(type="altitude", value=2000, verb="descend"))
    assert (s, code) == ("QUERY", "QUERY_LEVEL_BELOW_MIN")
    assert v == {"alt": 2000, "min_alt": 3000}


def test_descend_2000_is_ok_with_approach_clearance_in_same_call(world):
    x, y = on_final(world, 10.0)
    place(world, "T1", x=x, y=y, alt=4000, hdg=world.loc_course)
    out = ev(world, "T1", I(type="approach_clearance", approach_type="ILS", runway="26L"),
             I(type="altitude", value=2000, verb="descend"))
    assert [o[0] for o in out] == ["OK", "OK"]


def test_reduce_150_at_fl90_is_unable(world):
    place(world, "T1", x=20.0, y=5.0, alt=9000, ias=250, hdg=270)
    [(s, code, v)] = ev(world, "T1", I(type="speed", value=150))
    assert (s, code) == ("UNABLE", "UNABLE_SPEED_LOW")
    assert v == {"speed": 150, "min_speed": 205}


def test_speed_above_250_below_fl100_unless_restriction_cancelled(world):
    place(world, "T1", x=20.0, y=5.0, alt=8000, ias=250, hdg=270)
    assert ev(world, "T1", I(type="speed", value=280))[0][1] == "UNABLE_SPEED_HIGH"
    out = ev(world, "T1", I(type="speed_cancel"), I(type="speed", value=280))
    assert out[1][0] == "OK"


def test_established_allows_vapp_plus_five(world):
    x, y = on_final(world, 6.0)
    place(world, "T1", x=x, y=y, alt=2100, ias=170, hdg=world.loc_course, established=True)
    assert ev(world, "T1", I(type="speed", value=145))[0][0] == "OK"
    assert ev(world, "T1", I(type="speed", value=140))[0][1] == "UNABLE_SPEED_LOW"


def test_ceiling_and_level_type(world):
    place(world, "T1", x=20.0, y=5.0, alt=8000, hdg=270)
    assert ev(world, "T1", I(type="flight_level", value=450))[0][1] == "UNABLE_CEILING"
    assert ev(world, "T1", I(type="flight_level", value=50))[0][1] == "QUERY_LEVEL_TYPE"
    assert ev(world, "T1", I(type="altitude", value=8000))[0][1] == "QUERY_LEVEL_TYPE"
    assert ev(world, "T1", I(type="altitude", value=6000))[0][0] == "OK"
    assert ev(world, "T1", I(type="flight_level", value=70))[0][0] == "OK"


def test_sixty_degree_intercept_is_unable(world):
    x, y = on_final(world, 12.0, offset_nm=-4.0)
    place(world, "T1", x=x, y=y, alt=4000, hdg=world.loc_course)
    out = ev(world, "T1", I(type="heading", value=world.loc_course + 60, turn_dir="R"),
             I(type="approach_clearance", approach_type="ILS", runway="26L"))
    assert out[0][0] == "OK" and out[1][1] == "UNABLE_INTERCEPT_ANGLE"
    out = ev(world, "T1", I(type="heading", value=world.loc_course + 30, turn_dir="R"),
             I(type="approach_clearance", approach_type="ILS", runway="26L"))
    assert out[1][0] == "OK"


def test_above_glidepath_is_unable_too_high(world):
    x, y = on_final(world, 6.0, offset_nm=-2.0)
    gp = glidepath_alt(world, 6.0)
    place(world, "T1", x=x, y=y, alt=gp + 2000, hdg=world.loc_course + 20)
    [(s, code, _)] = ev(world, "T1", I(type="approach_clearance", approach_type="ILS"))
    assert (s, code) == ("UNABLE", "UNABLE_TOO_HIGH_GS")


def test_unknown_fix_runway_and_wrong_position(world):
    place(world, "T1", x=20.0, y=5.0, alt=8000, hdg=270)
    assert ev(world, "T1", I(type="direct", fix="ZZZZZ"))[0][1] == "QUERY_UNKNOWN_FIX"
    assert ev(world, "T1", I(type="direct", fix="WILLO"))[0][0] == "OK"
    assert ev(world, "T1", I(type="approach_clearance", runway="08R"))[0][1] == \
        "QUERY_UNKNOWN_RUNWAY"
    assert ev(world, "T1", I(type="approach_clearance", approach_type="RNP"))[0][1] == \
        "UNABLE_NOT_MODELLED"
    assert ev(world, "T1", I(type="cleared_to_land", runway="26L"))[0][1] == \
        "QUERY_WRONG_POSITION"


def test_evaluate_is_pure_and_unknown_receiver_is_ok(world):
    place(world, "T1", x=20.0, y=0.0, alt=5000, hdg=270)
    before = world.snapshot().model_dump()
    ev(world, "T1", I(type="altitude", value=2000), I(type="speed", value=100))
    assert world.snapshot().model_dump() == before
    assert ev(world, "NOPE", I(type="speed", value=100))[0][0] == "OK"


def test_direct_to_a_fix_already_passed_on_own_star_is_queried(world):
    from server.contracts import Instruction, Transmission
    world.inject("spawn_arrival", params={"entry": "DISIT"})
    ac = next(a for a in world.aircraft.values() if a.route_name == "DISIT1G")
    ac.route, ac.route_idx = ["KIDLI", "MID", "TUFOZ", "HOLLY", "WILLO"], 3   # next fix HOLLY
    def ev(fix):
        tx = Transmission(tx_id="d", t=0, receiver=ac.callsign,
                          instructions=[Instruction(type="direct", fix=fix)])
        return world.evaluate(tx)[0]
    behind = ev("TUFOZ")
    assert behind.status == "QUERY" and behind.reason_code == "QUERY_FIX_PASSED"
    assert ev("WILLO").status == "OK" and ev("HOLLY").status == "OK"
    ac.lat_mode = "HDG"                                      # on vectors: any fix is fine
    assert ev("TUFOZ").status == "OK"
