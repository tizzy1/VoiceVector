"""Phraseology coach (server/voice/coach.py): rules, Claude tip guard, pipeline wiring."""
import pytest

from server.voice import coach
from server.voice.parser import parse
from server.voice.parser.callsign import DEMO_CALLSIGNS


def rev(raw, alt=8000, speech=None):
    return coach.review(parse(raw), raw, callsigns=DEMO_CALLSIGNS, aircraft_alt=alt, speech_s=speech)


def texts(r):
    return " ".join(n["text"] for n in r.notes).lower()


def test_standard_call_scores_full_marks():
    r = rev("Speedbird 117, turn left heading 240, descend altitude 4000 feet, QNH 1013")
    assert r.score == 100 and [n["level"] for n in r.notes] == ["good"]


@pytest.mark.parametrize("raw, alt, expect", [
    ("Speedbird 117 descend altitude 4000 feet", 8000, "qnh"),
    ("turn left heading 240", 8000, "callsign"),
    ("Speedbird 117 descend altitude 8000 feet", 9000, "flight level"),
    ("Speedbird 117 descend flight level 50", 9000, "altitude 5,000 feet"),
    ("Speedbird 117 turn left 240", 8000, "heading"),
    ("Speedbird 117 reduce speed 210", 8000, "knots"),
    ("Speedbird 117 cleared ILS approach", 4000, "runway"),
    ("Speedbird 117 contact tower", 3000, "frequency"),
])
def test_each_rule_catches_its_mistake(raw, alt, expect):
    r = rev(raw, alt)
    assert r.score < 100 and expect in texts(r), (raw, r.notes)


def test_fix_costs_more_than_tip_and_never_nearly_there_with_a_fix():
    fix, tip = rev("Speedbird 117 descend altitude 4000 feet"), rev("Speedbird 117 reduce speed 210", 5000)
    assert fix.score < tip.score
    assert "nearly there" not in texts(fix) and "nearly there" in texts(tip)


def test_too_many_instructions_and_fast_speech():
    raw = "Speedbird 117 turn left heading 240 descend altitude 4000 feet reduce speed 210 knots squawk 4521"
    r = rev(raw, alt=5000, speech=2.0)
    assert "4 instructions" in texts(r) and "fast" in texts(r)


def test_acknowledgements_are_not_coached():
    assert rev("Speedbird 117 readback correct").notes == []


def test_claude_numbers_must_come_from_the_call_or_the_notes():
    assert coach._numbers_ok("say altitude 5,000 feet", "descend flight level 50 altitude 5,000 feet")
    assert not coach._numbers_ok("descend altitude 3000 feet", "Speedbird 117 descend altitude 4000 feet")


@pytest.mark.asyncio
async def test_claude_tip_fails_quietly_without_a_key():
    assert await coach.claude_tip("Speedbird 117 descend 4000", rev("Speedbird 117 descend 4000"), client=None) is None


@pytest.mark.asyncio
async def test_pipeline_sends_coach_after_the_reply_and_never_breaks_it(monkeypatch):
    from server.tests.voice.test_pipeline import make, type_and_settle

    async def tip(raw, r, client=None):
        return "Add the QNH."
    monkeypatch.setattr(coach, "claude_tip", tip)
    pipe, world, pilot, hub, bus, _ = make()
    await type_and_settle(pipe, hub, "speedbird one one seven turn left heading two four zero")
    msgs = hub.of("coach")
    assert len(msgs) == 2 and msgs[0].tip is None and msgs[1].tip == "Add the QNH."
    assert msgs[0].tx_id == msgs[1].tx_id and hub.of("audio_start")

    def boom(*a, **k):
        raise RuntimeError("coach bug")
    monkeypatch.setattr(coach, "review", boom)
    pipe2, _, pilot2, hub2, _, _ = make()
    await type_and_settle(pipe2, hub2)
    assert hub2.of("audio_start") and not hub2.of("coach")
