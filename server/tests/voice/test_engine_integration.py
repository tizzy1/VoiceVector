"""Parser + Composer against Tudor's REAL World and PilotEngine (read-only use of his modules).

Every plan the engine can produce for typed controller speech must compose into a validated,
non-fallback readback: this is the "zero unplanned readback errors" gate on real data.
"""
import random
from pathlib import Path

import pytest

from server.contracts import Event, SessionSettings
from server.pilot.engine import PilotEngine
from server.pilot.readback import Composer
from server.sim.world import World
from server.voice.normalise import normalise_tokens
from server.voice.parser import parse

DATA = Path(__file__).resolve().parents[3] / "data"

COMMANDS = [
    "turn left heading two four zero", "turn right heading one eight zero", "fly heading three six zero",
    "descend altitude four thousand feet", "descend flight level eight zero", "climb altitude six thousand feet",
    "reduce speed two one zero knots", "speed one eight zero knots or greater", "speed two two zero knots or less",
    "turn right heading one eight zero descend altitude five thousand feet speed two two zero knots",
    "cleared i l s approach runway two six left", "turn left heading two niner zero cleared i l s approach runway two six left report established",
    "squawk four five two one", "squawk ident", "direct willo", "hold at timba", "contact tower one two four decimal two two five",
    "q n h one zero one three", "expedite descent", "descend at one thousand five hundred feet per minute or less",
    "maintain flight level niner zero", "resume own navigation", "no a t c speed restriction",
    "intercept the localiser runway two six left", "turn left ten degrees", "go around",
    "negative descend altitude four thousand feet", "negative turn left heading two four zero",
    "i say again turn left heading two four zero", "confirm cleared level", "confirm heading", "confirm assigned speed",
    "say again", "read back", "report established", "expect i l s approach runway two six left",
    "cleared to land runway two six left", "descend flight level four hundred", "reduce speed one zero zero knots",
    "direct nowhere", "turn left heading", "descend",
]


class Bus:
    def __init__(self):
        self.events, self.subs, self.clock = [], [], lambda: 0.0

    def emit(self, e):
        self.events.append(e)
        for cb in self.subs:
            cb(e)

    def subscribe(self, cb):
        self.subs.append(cb)

    def now(self):
        return self.clock()


def make(rate: str, seed: int = 42):
    bus = Bus()
    world = World(bus, DATA)
    bus.clock = lambda: world.t
    world.load_scenario("demo", seed)
    world.configure(SessionSettings(error_rate=rate, seed=seed))
    pilot = PilotEngine(world, bus, DATA)
    pilot.set_error_rate(rate)
    for _ in range(60):
        world.tick(1.0)
        pilot.tick(world.t)
    return world, pilot, bus


def drive(world, pilot, composer, rng, rounds: int):
    plans = bad = 0
    kinds: dict[str, int] = {}
    callsigns = world.callsigns()
    for _ in range(rounds):
        for info in callsigns:
            text = f"{info.spoken} {rng.choice(COMMANDS)}"
            tx = parse(text, callsigns=callsigns, fixes=set(world.fixes()), tx_id=f"t{plans}", t=world.t)
            evals = world.evaluate(tx)
            for resp in pilot.handle_transmission(tx, evals):
                if resp.plan is None or resp.kind == "silence":
                    continue
                readback = composer.compose(resp.plan)
                plans += 1
                kinds[resp.kind] = kinds.get(resp.kind, 0) + 1
                if not readback.validated or readback.fallback_used or not readback.tts_text:
                    bad += 1
                    print("BAD", resp.kind, resp.plan.model_dump(exclude_defaults=True), "->", readback.text)
                assert not any(c.isdigit() for c in readback.tts_text)
                pilot.on_readback_spoken(resp, world.t)
            world.tick(1.0)
            pilot.tick(world.t)
    return plans, bad, kinds


@pytest.mark.parametrize("rate", ["off", "low", "med", "high"])
def test_every_engine_plan_composes_into_a_validated_readback(rate):
    world, pilot, _ = make(rate)
    composer = Composer(DATA)
    plans, bad, kinds = drive(world, pilot, composer, random.Random(f"engine-{rate}"), rounds=30)
    assert plans > 100, plans
    assert bad == 0, f"{bad}/{plans} plans failed to compose ({kinds})"
    assert composer.rejections == 0, "templates were rejected at run time (bank/validator mismatch)"


def test_planned_errors_are_spoken_exactly_as_planned():
    """When the engine plans a readback error, the spoken text carries the wrong value verbatim."""
    world, pilot, _ = make("high", seed=7)
    composer = Composer(DATA)
    rng = random.Random("errors")
    callsigns = world.callsigns()
    seen = 0
    for _ in range(40):
        info = rng.choice(callsigns)
        tx = parse(f"{info.spoken} descend altitude four thousand feet", callsigns=callsigns,
                   fixes=set(world.fixes()), tx_id=f"e{seen}", t=world.t)
        for resp in pilot.handle_transmission(tx, world.evaluate(tx)):
            if not resp.errors or resp.plan is None or resp.kind != "full":
                continue
            seen += 1
            readback = composer.compose(resp.plan)
            for err in resp.errors:
                if err.element_type == "altitude" and err.readback_value is not None:
                    spoken = " ".join(normalise_tokens(readback.tts_text))
                    from server.voice.numbers import altitude_to_speech
                    assert altitude_to_speech(int(float(err.readback_value))) in spoken, (err, readback.tts_text)
        world.tick(1.0)
        pilot.tick(world.t)
    assert seen >= 1


@pytest.mark.parametrize("kind,data", [
    ("checkin", {"route": "WILLO1G", "atis": "K", "level_fl": 90}),
    ("checkin", {"route": "TIMBA1G", "atis": "K", "alt": 6000}),
    ("established", {"runway": "26L"}),
    ("no_further_clearance", {"fix": "WILLO"}),
    ("going_around", {}),
    ("mayday", {}),
    ("pan", {"nature": "medical"}),
])
def test_event_driven_pilot_calls_compose(kind, data):
    world, pilot, bus = make("low")
    composer = Composer(DATA)
    cs = world.callsigns()[0].icao
    resp = pilot.on_sim_event(Event(t=world.t, type="pilot_call", actor="PILOT", callsign=cs,
                                    data={"kind": kind, **data}))
    assert resp is not None
    readback = composer.compose(resp.plan)
    assert readback.validated and not readback.fallback_used, (kind, readback)
    assert readback.tts_text


def test_recall_after_being_blocked_composes():
    world, pilot, _ = make("low")
    composer = Composer(DATA)
    cs = world.callsigns()[1].icao
    resp = pilot.on_blocked(cs, "tx1")
    assert resp is not None and resp.kind == "recall"
    readback = composer.compose(resp.plan)
    assert readback.validated and "easy four five alpha bravo" in readback.tts_text


def test_repeat_last_returns_the_previous_readback():
    world, pilot, _ = make("off")
    composer = Composer(DATA)
    info = world.callsigns()[0]
    callsigns = world.callsigns()
    first = parse(f"{info.spoken} turn left heading two four zero", callsigns=callsigns,
                  fixes=set(world.fixes()), tx_id="a", t=world.t)
    resp = pilot.handle_transmission(first, world.evaluate(first))[0]
    spoken = composer.compose(resp.plan)
    pilot.on_readback_spoken(resp, world.t)
    again = parse(f"{info.spoken} say again", callsigns=callsigns, fixes=set(world.fixes()), tx_id="b", t=world.t)
    repeat = pilot.handle_transmission(again, world.evaluate(again))[0]
    assert repeat.kind == "repeat_last"
    assert composer.compose(repeat.plan).tts_text == spoken.tts_text
