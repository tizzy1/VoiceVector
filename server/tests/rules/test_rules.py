"""RulesEngine acceptance tests (ASSIGNMENT_TUDOR §7, SIM_SPEC §3)."""
from server.contracts import ErrorRecord, Event, Instruction, Report, Transmission
from server.rules.engine import RulesEngine
from server.rules.report import score
from server.tests.sim.test_sim_behaviour import empty, on_final, place


def setup(world, bus, data_dir):
    empty(world)
    world.aircraft.clear()
    rules = RulesEngine(world, bus, data_dir)
    bus.subscribe(rules.on_event)
    return rules


def say(bus, world, cs, *instrs, text="", tx_id="t1", evals=None):
    tx = Transmission(tx_id=tx_id, t=world.t, receiver=cs, instructions=list(instrs),
                      raw_text=text, norm_text=text)
    evals = world.evaluate(tx) if evals is None else evals
    bus.emit(Event(t=world.t, type="ctrl_transmission", actor="CONTROLLER", callsign=cs,
                   tx_id=tx_id, data={"tx": tx.model_dump(), "evals": [e.model_dump() for e in evals]}))


def step(world, rules, n):
    for _ in range(n):
        world.tick(1.0)
        rules.on_tick(world.snapshot())


def ids(rules, sev=None):
    return [f.rule_id for f in rules.findings if sev is None or f.severity == sev]


def test_b1_descend_2000_twenty_miles_out_without_approach(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=20.0, y=0.0, alt=5000, hdg=270)
    say(bus, world, "T1", Instruction(type="altitude", value=2000, verb="descend"),
        Instruction(type="qnh", value=1013), text="T1 descend altitude 2000 feet QNH 1013")
    assert ids(rules) == ["B1"]
    f = rules.findings[0]
    assert f.severity == "HIGH" and f.callsigns == ["T1"] and f.evidence["tx_id"] == "t1"
    assert [e.data["rule_id"] for e in bus.of("finding")] == ["B1"]


def test_b1_not_fired_with_approach_clearance(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    x, y = on_final(world, 10.0)
    place(world, "T1", x=x, y=y, alt=3000, hdg=world.loc_course)
    say(bus, world, "T1", Instruction(type="approach_clearance", approach_type="ILS", runway="26L"),
        Instruction(type="altitude", value=2000, verb="descend"), Instruction(type="qnh", value=1013),
        text="T1 cleared ILS approach runway 26L descend altitude 2000 feet QNH 1013")
    assert "B1" not in ids(rules)


def test_los_finding_has_start_end_minimum(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "A", x=30.0, y=10.0, alt=5000, hdg=90, ias=250)
    place(world, "B", x=36.0, y=10.0, alt=5000, hdg=270, ias=250)
    step(world, rules, 120)
    los = [f for f in rules.findings if f.rule_id == "A1"]
    assert len(los) == 1
    ev = los[0].evidence
    assert ev["start"] < ev["end"] and ev["min_nm"] < 1.0 and ev["callsigns"] == ["A", "B"]
    assert los[0].severity == "HIGH"


def test_open_los_closed_when_session_ends_early(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "A", x=30.0, y=10.0, alt=5000, hdg=90, ias=250)
    place(world, "B", x=32.0, y=10.0, alt=5000, hdg=90, ias=250)
    step(world, rules, 5)
    rep = rules.report("S1")
    [f] = [f for f in rep.findings if f.rule_id == "A1"]
    assert f.evidence["end"] is None and rep.stats["los"][0]["callsigns"] == ["A", "B"]


def test_b4_intercept_angle_warn_and_fail(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    x, y = on_final(world, 14.0, offset_nm=4.0)
    place(world, "W", x=x, y=y, alt=3000, hdg=world.loc_course)
    place(world, "F", x=x, y=y + 20, alt=3000, hdg=world.loc_course)
    say(bus, world, "W", Instruction(type="heading", value=round(world.loc_course - 40) % 360, turn_dir="L"),
        Instruction(type="intercept_localiser", runway="26L"), tx_id="w")
    say(bus, world, "F", Instruction(type="heading", value=round(world.loc_course - 60) % 360, turn_dir="L"),
        Instruction(type="intercept_localiser", runway="26L"), tx_id="f")
    b4 = {f.callsigns[0]: f.severity for f in rules.findings if f.rule_id == "B4"}
    assert b4 == {"W": "MEDIUM", "F": "HIGH"}


def test_a3_wake_on_final(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    x1, y1 = on_final(world, 8.0)
    x2, y2 = on_final(world, 11.5)          # 3.5 NM behind a Heavy: needs 5 NM (H>M)
    place(world, "HVY", x=x1, y=y1, alt=2500, hdg=world.loc_course, actype="B77W",
          established=True, lat_mode="LOC")
    world.aircraft["HVY"].wake = "H"
    place(world, "MED", x=x2, y=y2, alt=3500, hdg=world.loc_course, established=True, lat_mode="LOC")
    step(world, rules, 3)
    rep = rules.report("S")
    [f] = [f for f in rep.findings if f.rule_id == "A3"]
    assert f.evidence["required_nm"] == 5 and f.evidence["min_nm"] < 5


def _err(outcome, mode="consistent", caught_by=None):
    return ErrorRecord(id="E1", callsign="T1", kind="wrong_value_adjacent", mode=mode,
                       element_type="altitude", intended=4000, readback_value=5000,
                       flown_value=5000, t_planned=1, t_spoken=3, t_caught=6 if caught_by else None,
                       outcome=outcome, caught_by=caught_by).model_dump()


def test_readback_error_caught_is_d1_positive(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    bus.emit(Event(t=6, type="error_caught", actor="PILOT", callsign="T1",
                   data=_err("caught", caught_by="readback_correction")))
    assert [(f.rule_id, f.severity) for f in rules.findings] == [("D1", "POSITIVE")]
    rep = rules.report("S")
    assert rep.score_breakdown["catches"] == 5
    assert rep.stats["catch_table"][0]["time_to_catch_s"] == 3.0


def test_missed_errors_are_d2_and_e2(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    bus.emit(Event(t=40, type="error_missed", actor="PILOT", callsign="T1", data=_err("missed")))
    bus.emit(Event(t=70, type="error_missed", actor="PILOT", callsign="T1",
                   data={**_err("missed", mode="execution_only"), "id": "E2"}))
    ctrl = [(f.rule_id, f.severity) for f in rules.findings if f.actor == "CONTROLLER"]
    assert ctrl == [("D2", "MEDIUM"), ("E2", "MEDIUM")]
    assert {f.rule_id for f in rules.findings if f.actor == "PILOT"} == {"D2", "E2"}


def test_pilot_d5_adopted_once(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    bus.emit(Event(t=5, type="finding", actor="PILOT", callsign="T1", tx_id="x",
                   data={"rule_id": "D5", "title": "Unnecessary correction: T1"}))
    assert ids(rules) == ["D5"] and len(bus.of("finding")) == 1


def test_score_weights():
    from server.contracts import Finding
    mk = lambda rid, sev: Finding(id=rid, rule_id=rid, category={"A": "A_SAFETY", "B": "B_INSTRUCTION",  # noqa: E731
                                  "C": "C_PHRASEOLOGY", "D": "D_READBACK"}[rid[0]], severity=sev, t=0,
                                  actor="CONTROLLER", title="", detail="")
    fs = [mk("A1", "HIGH"), mk("B5", "MEDIUM"), mk("C1", "LOW")] + [mk("D1", "POSITIVE")] * 7
    total, br = score(fs, 4)
    assert br == {"base": 100, "safety": -15, "instructions": -5, "phraseology": -1,
                  "readback": 0, "compliance": 0, "catches": 25, "efficiency": 4}
    assert total == 100                      # capped at 100
    assert score([mk("A1", "HIGH")] * 8, 0)[0] == 0


def test_phraseology_missing_callsign_and_level_type(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=20.0, y=5.0, alt=5000, hdg=270)
    say(bus, world, "T1", Instruction(type="altitude", value=4000, verb="descend"),
        Instruction(type="qnh", value=1013), text="t1 descend 4000 qnh 1013")
    tx = Transmission(tx_id="n", t=0, receiver=None, instructions=[Instruction(type="heading", value=270)],
                      raw_text="turn left heading 270")
    bus.emit(Event(t=0, type="ctrl_transmission", actor="CONTROLLER", data={"tx": tx.model_dump(), "evals": []}))
    assert ids(rules) == ["C2", "C1"]


def test_report_validates_and_can_end_any_time(world, bus, data_dir):
    rules = RulesEngine(world, bus, data_dir)
    bus.subscribe(rules.on_event)
    step(world, rules, 30)
    rep = rules.report("ABCD")
    Report.model_validate(rep.model_dump())
    for k in ("catch_table", "los", "efficiency", "latency", "findings_by_category"):
        assert k in rep.stats
    assert rep.narrative is None and 0 <= rep.score <= 100


def test_incomplete_readback_not_challenged_is_d3(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    bus.emit(Event(t=40, type="error_missed", actor="PILOT", callsign="T1",
                   data={**_err("missed", mode="readback_only"), "kind": "omitted_element"}))
    assert [(f.rule_id, f.severity) for f in rules.findings] == [("D3", "MEDIUM")]


def test_latency_uses_pilot_transmission_timings(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=20, y=0, alt=6000)
    say(bus, world, "T1", Instruction(type="heading", value=270), text="t1 turn left heading 270")
    base = {"t_ptt_up": 100.0, "t_final": 100.4}
    for tx, play, blocked in (("t1", 101.6, False), ("t1", 105.0, False), ("t2", 200.0, True)):
        bus.emit(Event(t=world.t + 1, type="pilot_transmission", actor="PILOT", callsign="T1",
                       tx_id="p", data={"in_reply_to": tx, "blocked": blocked,
                                        "timings": {**base, "t_play": play}}))
    lat = rules.report("L").stats["latency"]
    assert lat["perceived_s"]["n"] == 1 and abs(lat["perceived_s"]["p50"] - 1.6) < 1e-6
    assert abs(lat["machine_s"]["p50"] - 1.2) < 1e-6
