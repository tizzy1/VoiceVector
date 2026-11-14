"""Remaining catalogue rules: A5, B2, B3, B8, B9, C3, C6, E3 (SIM_SPEC §3.2)."""
from server.contracts import ClearanceElement, ErrorRecord, Event, Instruction, Transmission
from server.sim.airspace import cas_base_ft, in_cas
from server.tests.rules.test_rules import ids, say, setup, step
from server.tests.sim.test_sim_behaviour import place

WILLO_AREA = (-0.7, -9.9)                   # under London TMA 1 (base 2,500 ft)


def tx_event(bus, world, cs, instrs, text, intent="instruction", amb=(), self_corr=0):
    tx = Transmission(tx_id="tx", t=world.t, receiver=cs, instructions=list(instrs),
                      raw_text=text, norm_text=text, intent=intent, ambiguities=list(amb),
                      self_corrections=self_corr)
    evals = world.evaluate(tx)
    bus.emit(Event(t=world.t, type="ctrl_transmission", actor="CONTROLLER", callsign=cs,
                   tx_id="tx", data={"tx": tx.model_dump(),
                                     "evals": [e.model_dump() for e in evals]}))


def hdg(v, d=None):
    return Instruction(type="heading", value=v, turn_dir=d)


def test_airspace_lookup_on_known_points(world):
    assert in_cas(world, 0.0, 0.0, 500)                 # Gatwick CTR
    assert in_cas(world, *WILLO_AREA, 3000)
    assert not in_cas(world, *WILLO_AREA, 2000)         # below the TMA, outside the CTA
    assert cas_base_ft(world, 0.0, -35.0) >= 5500       # high bases over the coast
    assert not in_cas(world, 0.0, -35.0, 4000)


# ---- B2 vectoring out of controlled airspace ---------------------------------------------
def test_b2_heading_south_at_3000_leaves_cas(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=WILLO_AREA[0], y=WILLO_AREA[1], alt=3000, hdg=90, ias=220)
    say(bus, world, "T1", hdg(180, "R"), text="T1 turn right heading 180")
    [f] = [f for f in rules.findings if f.rule_id == "B2"]
    assert f.severity == "MEDIUM" and f.evidence["level"] == 3000


def test_b2_not_for_heading_staying_inside(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=WILLO_AREA[0], y=WILLO_AREA[1], alt=3000, hdg=90, ias=220)
    say(bus, world, "T1", hdg(360, "L"), text="T1 turn left heading 360")
    assert "B2" not in ids(rules)


# ---- B3 level above Director airspace ------------------------------------------------------
def test_b3_climb_above_6500_without_transfer(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=5, y=5, alt=5000, hdg=90)
    say(bus, world, "T1", Instruction(type="flight_level", value=80, verb="climb"),
        text="T1 climb flight level 80")
    assert ids(rules).count("B3") == 1


def test_b3_not_with_transfer_or_for_descent(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=5, y=5, alt=5000, hdg=90)
    place(world, "T2", x=-5, y=-5, alt=10000, hdg=90)
    say(bus, world, "T1", Instruction(type="flight_level", value=80, verb="climb"),
        Instruction(type="contact", unit="london", frequency="134.125"),
        text="T1 climb flight level 80 contact London 134.125", tx_id="a")
    say(bus, world, "T2", Instruction(type="flight_level", value=80, verb="descend"),
        text="T2 descend flight level 80", tx_id="b")
    assert "B3" not in ids(rules)


# ---- B8 conflicting instructions ------------------------------------------------------------
def test_b8_two_headings_in_one_transmission(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=5, y=5, alt=4000, hdg=90)
    tx_event(bus, world, "T1", [hdg(120), hdg(90, "L")], "T1 fly heading 120 turn left heading 090")
    assert ids(rules).count("B8") == 1


def test_b8_not_after_a_self_correction_or_mixed_types(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=5, y=5, alt=4000, hdg=90)
    tx_event(bus, world, "T1", [hdg(120), hdg(90, "L")], "T1 heading 120 correction 090",
             self_corr=1)
    tx_event(bus, world, "T1", [hdg(120), Instruction(type="speed", value=210)],
             "T1 fly heading 120 speed 210")
    assert "B8" not in ids(rules)


# ---- B9 instruction into conflict ---------------------------------------------------------
def test_b9_turning_into_traffic(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "A1", x=10, y=10, alt=5000, hdg=90, ias=220)
    place(world, "B2", x=10, y=22, alt=5000, hdg=180, ias=220)  # 12 NM north, southbound
    say(bus, world, "A1", hdg(360, "L"), text="A1 turn left heading 360")
    [f] = [f for f in rules.findings if f.rule_id == "B9"]
    assert f.callsigns == ["A1", "B2"] and f.evidence["p_after"] >= 0.5
    assert f.evidence["p_before"] < 0.5


def test_b9_not_when_turning_away(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "A1", x=10, y=10, alt=5000, hdg=90, ias=220)
    place(world, "B2", x=10, y=22, alt=5000, hdg=180, ias=220)
    say(bus, world, "A1", hdg(180, "R"), text="A1 turn right heading 180")
    assert "B9" not in ids(rules)


# ---- C3 / C6 phraseology ---------------------------------------------------------------------
def test_c3_number_without_saying_what_it_is(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=5, y=5, alt=5000, hdg=90)
    tx_event(bus, world, "T1", [Instruction(type="altitude", value=240, verb="climb")],
             "t1 climb two four zero", amb=["out_of_range"])
    assert "C3" in ids(rules)
    rules.findings.clear()
    tx_event(bus, world, "T1", [Instruction(type="flight_level", value=240, verb="climb")],
             "t1 climb flight level two four zero", amb=["out_of_range"])
    assert "C3" not in ids(rules)


def test_c6_correction_without_i_say_again(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=5, y=5, alt=5000, hdg=90)
    alt = Instruction(type="altitude", value=4000, verb="descend")
    tx_event(bus, world, "T1", [alt], "t1 negative descend altitude 4000 feet",
             intent="readback_correction")
    assert ids(rules).count("C6") == 1
    tx_event(bus, world, "T1", [alt], "t1 negative i say again descend altitude 4000 feet",
             intent="readback_correction")
    assert ids(rules).count("C6") == 1


# ---- E3 level bust detected -------------------------------------------------------------------
def test_e3_level_bust_caught_is_positive(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    rec = ErrorRecord(id="e1", callsign="T1", kind="level_bust_overshoot",
                      mode="execution_only", element_type="altitude", intended=4000,
                      readback_value=4000, flown_value=3700, t_planned=1.0, t_spoken=2.0,
                      t_caught=20.0, outcome="caught", caught_by="execution_correction")
    bus.emit(Event(t=20.0, type="error_caught", actor="CONTROLLER", callsign="T1",
                   data=rec.model_dump()))
    [f] = rules.findings
    assert f.rule_id == "E3" and f.severity == "POSITIVE" and "Level bust" in f.title


# ---- A5 outside controlled airspace -----------------------------------------------------------
def _vector_out(world, bus, cs, instruct=True):
    if instruct:
        say(bus, world, cs, hdg(180, "R"), text=f"{cs} turn right heading 180")
    i = hdg(180, "R")
    world.schedule_apply(cs, [ClearanceElement(type="heading", intended=i, readback=i,
                                               flown=i)], at=world.t)


def test_a5_worked_aircraft_vectored_out_of_cas(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=WILLO_AREA[0], y=WILLO_AREA[1], alt=3000, hdg=90, ias=220)
    step(world, rules, 1)
    _vector_out(world, bus, "T1")
    step(world, rules, 240)
    a5 = [f for f in rules.findings if f.rule_id == "A5"]
    assert len(a5) == 1 and a5[0].severity == "HIGH" and "below" in a5[0].detail


def test_a5_not_for_an_aircraft_never_worked(world, bus, data_dir):
    rules = setup(world, bus, data_dir)
    place(world, "T1", x=WILLO_AREA[0], y=WILLO_AREA[1], alt=3000, hdg=90, ias=220)
    step(world, rules, 1)
    _vector_out(world, bus, "T1", instruct=False)
    step(world, rules, 240)
    assert "A5" not in ids(rules)
