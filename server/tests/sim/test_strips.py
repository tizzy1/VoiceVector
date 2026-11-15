"""Controller's list record (SIM_SPEC §5.5): edits, validation, auto-fill from intent."""
from server.contracts import ClearanceElement, Event, Instruction, Transmission
from server.pilot.engine import PilotEngine


def say(bus, world, cs, *ins, intent="instruction"):
    tx = Transmission(tx_id="s", t=world.t, receiver=cs, instructions=list(ins), intent=intent)
    bus.emit(Event(t=world.t, type="ctrl_transmission", actor="CONTROLLER", callsign=cs,
                   data={"tx": tx.model_dump(), "evals": []}))
    return tx


def test_edit_is_a_record_and_never_moves_the_aircraft(world):
    a = world.aircraft["BAW117"]
    alt, hdg = a.cleared_alt, a.tgt_hdg
    assert world.edit_strip("BAW117", "cfl", "A40")
    assert world.edit_strip("BAW117", "ahdg", 240)
    assert world.edit_strip("BAW117", "dct", "willo")
    view = world.strip("BAW117")
    assert view["cfl"] == 4000 and view["ahdg"] == 240 and view["dct"] == "WILLO"
    for _ in range(20):
        world.tick(1.0)
    assert a.cleared_alt == alt and a.tgt_hdg == hdg


def test_edit_validation(world):
    bad = [("cfl", "F999"), ("ahdg", 400), ("aspd", 50), ("dct", "NOPE"), ("sts", "XYZ"),
           ("assr", "8888"), ("rwy", "27"), ("star", "FOO1A"), ("bogus", 1)]
    for field, value in bad:
        assert not world.edit_strip("BAW117", field, value), field
    assert not world.edit_strip("NOBODY", "cfl", "F80")
    assert world.edit_strip("BAW117", "cfl", "F80") and world.strip("BAW117")["cfl"] == 8000
    assert world.edit_strip("BAW117", "cfl", "") and "cfl" not in world.aircraft["BAW117"].strip


def test_view_defaults_from_the_flight_plan(world):
    v = world.strip("UAE15")                       # on its STAR (EZY45AB starts on vectors)
    a = world.aircraft["UAE15"]
    assert v["rfl"] == a.rfl and v["assr"] == a.assigned_squawk and v["ades"] == "EGKK"
    assert v["star"] == "ARNUN1G" and v["rwy"] == "26L" and v["sts"] == "STAR" and v["fr"] == "I"
    assert world.strip("EZY45AB")["sts"] == "VECT"


def test_autofill_uses_what_the_controller_said(world, bus, data_dir):
    p = PilotEngine(world, bus, data_dir)
    p.force_error("consistent", "wrong_value_adjacent", "BAW117")
    tx = say(bus, world, "BAW117", Instruction(type="altitude", value=4000, verb="descend"),
             Instruction(type="heading", value=240, turn_dir="L"),
             Instruction(type="speed", value=210))
    [r] = p.handle_transmission(tx, world.evaluate(tx))
    v = world.strip("BAW117")
    assert v["cfl"] == 4000 and v["ahdg"] == 240 and v["aspd"] == 210
    assert any(e.readback.value != e.intended.value for e in r.apply)  # pilot got it wrong…
    assert world.strip("BAW117")["cfl"] == 4000                        # …the strip did not


def test_autofill_direct_status_and_override(world, bus):
    world.edit_strip("UAE15", "cfl", "F90")
    say(bus, world, "UAE15", Instruction(type="direct", fix="WILLO"))
    assert world.strip("UAE15")["dct"] == "WILLO" and world.strip("UAE15")["cfl"] == 9000
    say(bus, world, "UAE15", Instruction(type="flight_level", value=70, verb="descend"))
    assert world.strip("UAE15")["cfl"] == 7000                          # new instruction wins
    say(bus, world, "UAE15", Instruction(type="approach_clearance", approach_type="ILS",
                                         runway="26L"))
    assert world.strip("UAE15")["sts"] == "APP"
    say(bus, world, "UAE15", Instruction(type="contact", unit="tower", frequency="124.225"))
    assert world.strip("UAE15")["sts"] == "TWR"


def test_autofill_can_be_turned_off(world, bus):
    world.strip_autofill = False
    say(bus, world, "BAW117", Instruction(type="heading", value=100))
    assert "ahdg" not in world.strip("BAW117")
