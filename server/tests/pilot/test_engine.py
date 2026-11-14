"""PilotEngine acceptance tests (ASSIGNMENT_TUDOR §7 a–g)."""
from server.contracts import Instruction, Transmission
from server.pilot.engine import PilotEngine
from server.sim.geo import dist
from server.sim.world import World
from server.tests.sim.test_sim_behaviour import empty, place


def I(**kw):
    return Instruction(**kw)


def tx(cs="BAW117", *ins, intent="instruction", tx_id="tx1"):
    return Transmission(tx_id=tx_id, t=0, receiver=cs, intent=intent, instructions=list(ins),
                        receiver_confidence=1.0)


def say(p, world, t):
    """Run one transmission end to end: evaluate, respond, 'speak', apply."""
    out = p.handle_transmission(t, world.evaluate(t))
    for r in out:
        p.on_readback_spoken(r, world.t)
        world.schedule_apply(r.callsign, r.apply, at=world.t + r.apply_delay_s)
    return out


def engine(world, bus, data_dir, rate="off"):
    p = PilotEngine(world, bus, data_dir)
    p.set_error_rate(rate)
    return p


ALT = I(type="altitude", value=4000, verb="descend")


def test_a_consistent_error_is_read_back_and_flown(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("consistent", "wrong_value_adjacent", "BAW117")
    [r] = say(p, world, tx("BAW117", ALT))
    [rb] = r.plan.elements
    assert rb.type == "altitude" and rb.value in (3000, 5000)
    assert r.apply[0].flown.value == rb.value and r.apply[0].intended.value == 4000
    assert r.errors[0].mode == "consistent" and r.apply[0].error_id == r.errors[0].id
    assert [e.type for e in bus.of("error_planned")] == ["error_planned"]
    assert [e.type for e in bus.of("error_spoken")] == ["error_spoken"]


def test_b_negative_correction_reads_back_only_altitude(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("consistent", "wrong_value_adjacent", "BAW117")
    say(p, world, tx("BAW117", ALT))
    [r] = say(p, world, tx("BAW117", ALT, intent="readback_correction", tx_id="tx2"))
    assert r.kind == "partial_correction"
    assert [(e.type, e.value) for e in r.plan.elements] == [("altitude", 4000)]
    assert [e.flown.value for e in r.apply] == [4000]
    [caught] = bus.of("error_caught")
    assert caught.data["caught_by"] == "readback_correction"


def test_c_full_repeat_after_one_error_rereads_only_that_element(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("consistent", "wrong_value_adjacent", "BAW117")
    three = [I(type="heading", value=240, turn_dir="L"), ALT, I(type="speed", value=210)]
    [r1] = say(p, world, tx("BAW117", *three))
    wrong_type = r1.errors[0].element_type
    [r2] = say(p, world, tx("BAW117", *three, intent="readback_correction", tx_id="tx2"))
    assert [e.type for e in r2.plan.elements] == [wrong_type]


def test_d_unnecessary_correction_flags_d5_and_rereads(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    say(p, world, tx("BAW117", ALT))
    [r] = say(p, world, tx("BAW117", ALT, intent="readback_correction", tx_id="tx2"))
    assert r.kind == "partial_correction" and [e.value for e in r.plan.elements] == [4000]
    assert r.apply == []
    assert [f.data["rule_id"] for f in bus.of("finding")] == ["D5"]


def test_e_uncorrected_error_is_missed_after_30s(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("consistent", "wrong_value_adjacent", "BAW117")
    say(p, world, tx("BAW117", ALT))
    for _ in range(29):
        world.tick(1.0)
        p.tick(world.t)
    assert bus.of("error_missed") == []
    world.tick(1.0)
    p.tick(world.t)
    [m] = bus.of("error_missed")
    assert m.data["outcome"] == "missed" and m.data["reason"] == "timeout"


def test_e_next_transmission_without_correction_misses_it(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("consistent", "wrong_value_adjacent", "BAW117")
    say(p, world, tx("BAW117", ALT))
    say(p, world, tx("BAW117", I(type="speed", value=210), tx_id="tx2"))
    assert [m.data["reason"] for m in bus.of("error_missed")] == ["next_transmission"]


def test_f_wrong_turn_execution_error_is_corrected(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("execution_only", "wrong_turn_direction", "BAW117")
    hdg = I(type="heading", value=240, turn_dir="L")
    [r] = say(p, world, tx("BAW117", hdg))
    assert r.plan.elements[0].turn_dir == "L"           # read back correctly
    for _ in range(4):
        world.tick(1.0)
    assert world.aircraft["BAW117"].turn_dir == "R"     # but turning the wrong way
    [c] = say(p, world, tx("BAW117", hdg, intent="readback_correction", tx_id="tx2"))
    assert [(e.type, e.turn_dir) for e in c.plan.elements] == [("heading", "L")]
    for _ in range(4):
        world.tick(1.0)
    assert world.aircraft["BAW117"].turn_dir == "L"
    assert bus.of("error_caught")[0].data["caught_by"] == "execution_correction"


def test_confirm_reveals_believed_value_then_correction_is_credited(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("consistent", "wrong_value_adjacent", "BAW117")
    [r] = say(p, world, tx("BAW117", ALT))
    wrong = r.plan.elements[0].value
    [c] = say(p, world, tx("BAW117", I(type="confirm", report_what="level"),
                           intent="confirm_request", tx_id="tx2"))
    assert c.kind == "report" and c.plan.report_values == {"cleared_alt": wrong}
    say(p, world, tx("BAW117", ALT, intent="readback_correction", tx_id="tx3"))
    assert bus.of("error_caught")[0].data["caught_by"] == "confirm_request"


def test_say_again_repeats_and_read_back_rereads(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    [r] = say(p, world, tx("BAW117", ALT, I(type="speed", value=210)))
    [s] = say(p, world, tx("BAW117", intent="say_again_request", tx_id="tx2"))
    assert s.kind == "repeat_last" and s.plan.elements == r.plan.elements and s.apply == []
    [f] = say(p, world, tx("BAW117", intent="readback_request", tx_id="tx3"))
    assert f.kind == "full" and [e.value for e in f.plan.elements] == [4000, 210]


def test_guards_one_per_aircraft_two_global(world, bus, data_dir):
    p = engine(world, bus, data_dir, rate="high")
    p.error_rate = "high"
    import server.pilot.errors as pol
    pol.RATE["high"] = 1.0
    try:
        for n in range(5):
            say(p, world, tx("BAW117", I(type="speed", value=200 + n * 5), tx_id=f"a{n}"))
            for cs in ("EZY45AB", "DLH4KA"):
                say(p, world, tx(cs, I(type="speed", value=210), tx_id=f"{cs}{n}"))
            assert len(p._open_errors("BAW117")) <= 1
            assert len(p._open_errors()) <= 2
    finally:
        pol.RATE["high"] = 0.15


def test_reaction_delay_by_persona_and_seeded(world, bus, data_dir):
    a = [say(engine(World(bus, data_dir), bus, data_dir), world, tx("BAW117", ALT))[0]
         .reaction_delay_s for _ in range(3)]
    assert len(set(a)) == 1 and 0.8 <= a[0] <= 1.2


def test_g_guard_never_creates_los_within_30s(bus, data_dir):
    """500 seeded runs: a forced error next to parallel traffic never loses separation in 30 s."""
    planned = 0
    for seed in range(500):
        w = empty(World(bus, data_dir))
        w.load_scenario("custom", seed)
        w.settings = w.settings.model_copy(update={"arr_per_hr": 0})
        place(w, "TOP1", x=10.0, y=0.0, alt=5000, hdg=270, ias=220)
        place(w, "LOW1", x=10.0, y=-3.2 - (seed % 5) * 0.2, alt=5000, hdg=270, ias=220)
        p = PilotEngine(w, bus, data_dir)
        p.force_error(("execution_only", "consistent", "readback_only")[seed % 3], None, "TOP1")
        t = Transmission(tx_id="g", t=0, receiver="TOP1", receiver_confidence=1.0,
                         instructions=[I(type="heading", value=360, turn_dir="R")])
        [r] = p.handle_transmission(t, w.evaluate(t))
        planned += bool(r.errors)
        w.schedule_apply("TOP1", r.apply, at=w.t + r.apply_delay_s)
        for _ in range(30):
            w.tick(1.0)
            a, b = w.aircraft["TOP1"], w.aircraft["LOW1"]
            assert not (dist(a.x, a.y, b.x, b.y) < 3.0 and abs(a.alt - b.alt) < 1000), seed
    assert planned > 100                                # the guard still lets safe errors through


HDG = I(type="heading", value=240, turn_dir="L")


def test_omitted_element_left_out_of_readback_then_missed(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("readback_only", "omitted_element", "BAW117")
    [r] = say(p, world, tx("BAW117", HDG, ALT))
    assert len(r.plan.elements) == 1 and r.errors[0].kind == "omitted_element"
    assert sorted(e.flown.type for e in r.apply) == ["altitude", "heading"]   # still flies both
    say(p, world, tx("BAW117", I(type="speed", value=220), tx_id="tx2"))
    [m] = bus.of("error_missed")
    assert m.data["kind"] == "omitted_element"


def test_omitted_element_repeated_by_controller_is_caught(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("readback_only", "omitted_element", "BAW117")
    [r] = say(p, world, tx("BAW117", HDG, ALT))
    left_out = ({"heading", "altitude"} - {e.type for e in r.plan.elements}).pop()
    again = HDG if left_out == "heading" else ALT
    [r2] = say(p, world, tx("BAW117", again, tx_id="tx2"))
    assert r2.kind == "partial_correction" and [e.type for e in r2.plan.elements] == [left_out]
    assert bus.of("error_caught") and not bus.of("error_missed")
    assert not [e for e in bus.of("finding") if e.data.get("rule_id") == "D5"]


def test_omission_never_planned_for_single_element(world, bus, data_dir):
    p = engine(world, bus, data_dir)
    p.force_error("readback_only", "omitted_element", "BAW117")
    [r] = say(p, world, tx("BAW117", ALT))
    assert not r.errors and len(r.plan.elements) == 1
