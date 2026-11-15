"""Regression tests for Tudor's 15 Nov playtest notes (voice side)."""
import random
from pathlib import Path

import pytest

from server.contracts import Instruction, ReadbackPlan
from server.pilot.readback import Composer, needs_readback
from server.voice.parser import parse
from server.voice.parser.callsign import DEMO_CALLSIGNS
from server.voice.parser.llm_fallback import merge, needs_fallback

DATA = Path(__file__).resolve().parents[3] / "data"


@pytest.fixture(scope="module")
def composer():
    return Composer(DATA)


def kinds(text):
    return [(i.type, i.value, i.turn_dir) for i in parse(text).instructions]


# "turn left XXX" read back twice (HDG 240 L and TURN L 240)
@pytest.mark.parametrize("said", ["Speedbird 117, turn left 240 degrees.", "Speedbird 117, turn left 240.",
                                  "Speedbird 117 turn left heading 240", "Speedbird 117, turn left two four zero degrees"])
def test_turn_left_three_digits_is_one_heading(said):
    assert kinds(said) == [("heading", 240.0, "L")]


def test_small_relative_turns_stay_relative():
    assert kinds("Speedbird 117, turn left 30 degrees") == [("turn_by", 30.0, "L")]
    assert kinds("Speedbird 117 turn right by 20 degrees") == [("turn_by", 20.0, "R")]


def test_llm_merge_never_adds_a_second_turn_or_level():
    tx = parse("Speedbird 117 er turn left 30 degrees and something unclear about the speed")
    out = {"receiver": "BAW117", "intent": "instruction",
           "instructions": [{"type": "heading", "value": 240, "turn_dir": "L"},
                            {"type": "speed", "value": 220}]}
    merged = merge(tx, out, DEMO_CALLSIGNS, set(), ["26L", "26R", "08L", "08R"])
    lateral = [i for i in merged.instructions if i.type in ("heading", "turn_by")]
    assert len(lateral) == 1 and any(i.type == "speed" for i in merged.instructions)


# ILS clearance dropped next to a turn
@pytest.mark.parametrize("said", [
    "Speedbird 117, turn left heading 290, cleared ILS approach runway 26 left.",
    "Speedbird 117, turn left heading 290, cleared I.L.S. approach runway two six left.",
    "Speedbird 117, turn left heading 290 cleared ILS 26 left",
    "Speedbird 117 turn left heading two niner zero cleared ILS approach runway two six left",
])
def test_turn_plus_ils_clearance_keeps_both(said):
    tx = parse(said)
    got = [(i.type, i.value, i.runway, i.approach_type) for i in tx.instructions]
    assert got == [("heading", 290.0, None, None), ("approach_clearance", None, "26L", "ILS")], got
    assert not needs_fallback(tx)


def test_expect_ils_runway_without_the_word_runway():
    assert parse("Speedbird 117, expect ILS two six left").instructions[0].expect_what == "ILS 26L"


# pilots repeating an older instruction
@pytest.mark.parametrize("said", ["Speedbird 117 readback correct", "Speedbird 117, read back correct.",
                                  "Speedbird 117, affirm"])
def test_readback_correct_is_not_a_request_to_read_back(said):
    tx = parse(said)
    assert tx.intent == "unknown" and not tx.instructions and not needs_fallback(tx)


def test_readback_request_still_works():
    assert parse("Speedbird 117, read back").intent == "readback_request"


def test_same_instruction_twice_in_one_call_is_read_back_once():
    assert kinds("Speedbird 117 turn left heading 240 say again heading 240") == [("heading", 240.0, "L")]


# "expecting" with nothing after it
def test_expect_is_read_back_in_full_or_just_rogered(composer):
    rng = random.Random(5)
    for what in (None, "vectors", "ILS 26L", "ILS 08R", "FL70"):
        for with_heading in (False, True):
            els = ([Instruction(type="heading", value=290, turn_dir="L")] if with_heading else []) + \
                  [Instruction(type="expect", expect_what=what)]
            for _ in range(15):
                rb = composer.compose(ReadbackPlan(callsign="BAW117", persona=rng.choice(
                    ["crisp_airline", "calm_longhaul", "student", "rushed"]), kind="full", elements=els,
                    seed=rng.randint(0, 10**6)))
                words = rb.text.lower().replace(",", "").split()
                assert rb.validated and not rb.fallback_used, rb
                if needs_readback(els[-1]):
                    assert what.split()[0].lower() in rb.text.lower(), rb.text
                else:
                    assert "expect" not in words and "expecting" not in words, rb.text
                    if not with_heading:
                        assert words[0] in ("roger", "copied") or words[-1] in ("roger", "copied"), rb.text
                    else:
                        assert "290" in rb.text


# "say again the altitude" for 2000 ft
def test_refusals_with_numbers_always_say_why(composer):
    for seed in range(30):
        rb = composer.compose(ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="query",
                                           reason_code="QUERY_LEVEL_BELOW_MIN",
                                           reason_values={"alt": 2000, "min_alt": 3000}, seed=seed))
        assert "3000" in rb.text and "say again" not in rb.text.lower(), rb.text


def test_turn_and_ils_end_to_end_in_the_sim(composer):
    from server.contracts import SessionSettings
    from server.pilot.engine import PilotEngine
    from server.sim.world import World
    from server.tests.voice.bench_latency import Bus
    bus = Bus()
    world = World(bus, DATA)
    bus.clock = lambda: world.t
    world.load_scenario("demo", 42)
    world.configure(SessionSettings(error_rate="off"))
    pilot = PilotEngine(world, bus, DATA)
    pilot.set_error_rate("off")
    said = "Speedbird 117, turn left heading 290, cleared I.L.S. approach runway two six left."
    tx = parse(said, callsigns=world.callsigns(), fixes=set(world.fixes()))
    evals = world.evaluate(tx)
    assert [e.status for e in evals] == ["OK", "OK"]
    resp = pilot.handle_transmission(tx, evals)[0]
    rb = composer.compose(resp.plan)
    assert "290" in rb.text and "ILS" in rb.text and "26L" in rb.text, rb.text
