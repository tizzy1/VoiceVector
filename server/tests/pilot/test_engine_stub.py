from server.contracts import Evaluation, Event, Instruction, PilotAPI, PilotResponse, Transmission
from server.pilot.engine import PilotEngine


def make(world, bus, data_dir) -> PilotEngine:
    p = PilotEngine(world, bus, data_dir)
    p.set_error_rate("off")                    # these tests check the error-free path
    return p


def tx(receiver="BAW117", **kw) -> Transmission:
    ins = [Instruction(type="heading", value=240, turn_dir="L"),
           Instruction(type="altitude", value=4000, verb="descend")]
    return Transmission(tx_id="tx1", t=0, receiver=receiver, instructions=kw.pop("ins", ins), **kw)


def test_full_readback_mirrors_the_instructions(world, bus, data_dir):
    p = make(world, bus, data_dir)
    _: PilotAPI = p
    t = tx()
    [r] = p.handle_transmission(t, world.evaluate(t))
    assert isinstance(r, PilotResponse)
    assert r.callsign == "BAW117" and r.kind == "full" and r.in_reply_to == "tx1"
    assert r.plan.kind == "full" and r.plan.persona == "crisp_airline"
    assert r.plan.elements == t.instructions
    assert [e.flown for e in r.apply] == t.instructions
    assert [e.readback for e in r.apply] == t.instructions
    assert r.errors == [] and all(e.error_id is None for e in r.apply)
    assert 0.8 <= r.reaction_delay_s <= 1.2 and 0.5 <= r.apply_delay_s <= 2.0


def test_response_applies_through_the_world(world, bus, data_dir):
    p = make(world, bus, data_dir)
    t = tx()
    [r] = p.handle_transmission(t, world.evaluate(t))
    world.schedule_apply(r.callsign, r.apply, at=world.t + r.apply_delay_s)
    for _ in range(5):
        world.tick(1.0)
    a = world.aircraft["BAW117"]
    assert a.tgt_hdg == 240 and a.cleared_alt == 4000


def test_silence_cases(world, bus, data_dir):
    p = make(world, bus, data_dir)
    unresolved = p.handle_transmission(tx(receiver=None), [])     # VOICE §8: silence or 50/50
    assert unresolved == [] or [r.kind for r in unresolved] == ["say_again_request"]
    assert p.handle_transmission(tx(receiver="XXX999"), []) == []
    assert p.handle_transmission(tx(ins=[]), []) == []


def test_unable_has_reason_and_no_apply(world, bus, data_dir):
    p = make(world, bus, data_dir)
    evals = [Evaluation(instr_idx=0, status="OK"),
             Evaluation(instr_idx=1, status="UNABLE", reason_code="UNABLE_SPEED_LOW",
                        reason_values={"min_speed": 205})]
    [r] = p.handle_transmission(tx(), evals)
    assert r.kind == "unable" and r.apply == []
    assert r.plan.reason_code == "UNABLE_SPEED_LOW" and r.plan.reason_values == {"min_speed": 205}


def test_pilot_call_and_recall(world, bus, data_dir):
    p = make(world, bus, data_dir)
    e = Event(t=0, type="pilot_call", actor="PILOT", callsign="UAE15",
              data={"kind": "checkin", "level_fl": 120, "route": "ARNUN1G", "atis": "K"})
    r = p.on_sim_event(e)
    assert r.kind == "checkin" and r.plan.persona == "calm_longhaul"
    assert r.plan.report_values == {"level_fl": 120, "route": "ARNUN1G", "atis": "K"}
    assert p.on_sim_event(Event(t=0, type="spawn", actor="SYSTEM")) is None
    rc = p.on_blocked("BAW117", "tx9")
    assert rc.kind == "recall" and 3.0 <= rc.reaction_delay_s <= 6.0
    assert p.on_blocked("XXX999", "tx9") is None


def test_controls_and_determinism(world, bus, data_dir):
    def delays():
        p = make(world, bus, data_dir)
        p.set_error_rate("off")
        p.force_error("consistent", "wrong_value_adjacent", "BAW117")
        p.tick(1.0)
        out = [p.handle_transmission(tx(), world.evaluate(tx()))[0] for _ in range(5)]
        p.on_readback_spoken(out[0], 3.0)
        return [(r.reaction_delay_s, r.apply_delay_s, r.plan.seed) for r in out]
    assert delays() == delays()
