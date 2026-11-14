"""LLM parser fallback: triggers, validation, conservative merge, pipeline wiring (offline, faked client)."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from server.voice.parser import default_fixes, default_runways, parse
from server.voice.parser.callsign import DEMO_CALLSIGNS
from server.voice.parser.llm_fallback import complete, merge, needs_fallback, refine, validate_instructions
from server.contracts import Instruction
from server.tests.voice.fakes import FakeBus, FakeComposer, FakeHub, FakePilot, FakeTTS, FakeWorld
from server.voice.pipeline import VoicePipeline

FIXES = set(default_fixes())
RUNWAYS = default_runways()


class FakeClient:
    def __init__(self, output=None, delay=0.0, error=None):
        self.output, self.delay, self.error, self.calls = output, delay, error, []
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        text = self.output if isinstance(self.output, str) else json.dumps(self.output)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


# ------------------------------------------------------------------ triggers
@pytest.mark.parametrize("text,expected", [
    ("speedbird one one seven turn left heading two four zero", False),
    ("all stations gatwick q n h one zero one two", False),
    ("speedbird one one seven turn left", True),                                   # no value
    ("speed bird one seventeen come left onto two four zero and down to four thousand", True),   # orphan numbers
    ("lufthansa four kilo alpha descend flight level eight zero then set the transponder to four five two one", True),
    ("turn left heading two four zero", True),                                    # no callsign
    ("speedbird one one seven say again", False),
    ("speedbird one one seven blah blah something", True),                        # nothing understood
])
def test_fallback_triggers(text, expected):
    assert needs_fallback(parse(text)) is expected


def test_silent_drops_are_now_flagged():
    tx = parse("lufthansa four kilo alpha descend flight level eight zero then set the transponder to four five two one")
    assert [i.type for i in tx.instructions] == ["flight_level"] and "unparsed_clause" in tx.ambiguities
    tx = parse("speed bird one seventeen come left onto two four zero and down to four thousand")
    assert "unparsed_clause" in tx.ambiguities


def test_out_of_range_and_fuzzy_fix_flags():
    tx = parse("speedbird one one seven descend flight level four hundred and ninety")
    assert tx.instructions[0].value == 490 and "out_of_range" in tx.ambiguities
    tx = parse("speedbird one one seven proceed direct to willow")
    assert tx.instructions[0].fix == "WILLO" and tx.instructions[0].confidence == 0.85
    assert "fix_fuzzy_match" in tx.ambiguities and not needs_fallback(tx)
    assert parse("speedbird one one seven hold at timber").instructions == []          # too far from TIMBA


# ---------------------------------------------------------------- validation
def test_validation_rejects_invented_or_incomplete_content():
    kept, rejected = validate_instructions([
        {"type": "heading", "value": 240, "turn_dir": "L"},
        {"type": "heading", "turn_dir": "L"},                                       # "turn left" with no heading
        {"type": "direct", "fix": "NOWHERE"},
        {"type": "approach_clearance", "approach_type": "ILS", "runway": "27"},
        {"type": "flight_level", "value": 900, "verb": "climb"},
        {"type": "squawk", "value": 421},
        {"type": "teleport"},
        "nonsense",
    ], FIXES, RUNWAYS)
    assert [(i.type, i.value) for i in kept] == [("heading", 240), ("squawk", "0421")]
    assert all(i.confidence == 0.85 for i in kept)
    for reason in ("incomplete_heading", "unknown_fix_NOWHERE", "unknown_runway_27", "llm_out_of_range_flight_level",
                   "llm_invalid_teleport", "llm_bad_instruction"):
        assert reason in rejected
    assert validate_instructions("x", FIXES, RUNWAYS) == ([], ["llm_bad_output"])


def test_complete():
    assert not complete(Instruction(type="contact"))
    assert complete(Instruction(type="contact", unit="tower"))
    assert complete(Instruction(type="go_around"))
    assert not complete(Instruction(type="expect"))


# --------------------------------------------------------------------- merge
def test_merge_keeps_regex_results_and_only_fills_gaps():
    tx = parse("lufthansa four kilo alpha descend flight level eight zero then set the transponder to four five two one")
    out = merge(tx, {"receiver": "BAW117", "intent": "instruction", "instructions": [
        {"type": "flight_level", "value": 90, "verb": "descend"},                 # disagrees with the regex: ignored
        {"type": "squawk", "value": "4521"}]}, DEMO_CALLSIGNS, FIXES, RUNWAYS)
    assert out.receiver == "DLH4KA", "a resolved regex callsign is never replaced"
    assert [(i.type, i.value) for i in out.instructions] == [("flight_level", 80), ("squawk", "4521")]
    assert out.parse_path == "mixed" and "unparsed_clause" not in out.ambiguities


def test_merge_never_accepts_an_off_scope_callsign():
    tx = parse("turn left heading two four zero")
    out = merge(tx, {"receiver": "RYR42", "intent": "instruction", "instructions": []}, DEMO_CALLSIGNS, FIXES, RUNWAYS)
    assert out.receiver is None and "callsign_unresolved" in out.ambiguities
    ok = merge(tx, {"receiver": "baw117", "intent": "instruction", "instructions": []}, DEMO_CALLSIGNS, FIXES, RUNWAYS)
    assert ok.receiver == "BAW117" and ok.receiver_confidence == 0.85 and "callsign_unresolved" not in ok.ambiguities


def test_merge_from_nothing_and_intent_upgrade():
    tx = parse("speedbird one one seven blah blah something")
    out = merge(tx, {"receiver": None, "intent": "say_again_request", "instructions": [],
                     "ambiguities": ["not an instruction"]}, DEMO_CALLSIGNS, FIXES, RUNWAYS)
    assert out.intent == "say_again_request" and "not an instruction" in out.ambiguities
    tx = parse("speedbird one one seven turn left")
    out = merge(tx, {"intent": "instruction", "instructions": [{"type": "heading", "turn_dir": "L"}]},
                DEMO_CALLSIGNS, FIXES, RUNWAYS)
    assert out.instructions == [] and "incomplete_heading" in out.ambiguities and "unparsed_clause" in out.ambiguities


# -------------------------------------------------------------------- refine
@pytest.mark.asyncio
async def test_refine_calls_haiku_without_thinking_and_merges():
    client = FakeClient({"receiver": "BAW117", "intent": "instruction",
                         "instructions": [{"type": "heading", "value": 240, "turn_dir": "L"},
                                          {"type": "altitude", "value": 4000, "verb": "descend"}]})
    tx = parse("speed bird one seventeen come left onto two four zero and down to four thousand")
    out = await refine(tx, DEMO_CALLSIGNS, FIXES, RUNWAYS, client=client)
    assert [(i.type, i.value) for i in out.instructions] == [("altitude", 4000), ("heading", 240)]
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["thinking"] == {"type": "disabled"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    context = json.loads(call["messages"][0]["content"])
    assert {c["icao"] for c in context["callsigns_on_frequency"]} == {c.icao for c in DEMO_CALLSIGNS}
    assert "WILLO" in context["fixes"] and context["runways"] == RUNWAYS
    assert "llm_s" in out.timings


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [FakeClient(error=RuntimeError("boom")), FakeClient("not json at all"),
                                    FakeClient({"instructions": []}, delay=5)])
async def test_refine_failure_keeps_the_regex_parse(client, monkeypatch):
    from server.voice.parser import llm_fallback
    monkeypatch.setattr(llm_fallback, "DEADLINE_S", 0.05)
    tx = parse("speedbird one one seven turn left")
    out = await refine(tx, DEMO_CALLSIGNS, FIXES, RUNWAYS, client=client)
    assert out.instructions == tx.instructions and out.receiver == tx.receiver and out.parse_path == "regex"


@pytest.mark.asyncio
async def test_no_call_when_not_needed_or_no_key():
    client = FakeClient({})
    tx = parse("speedbird one one seven turn left heading two four zero")
    assert await refine(tx, DEMO_CALLSIGNS, FIXES, RUNWAYS, client=client) is tx and not client.calls
    hard = parse("speedbird one one seven turn left")
    assert await refine(hard, DEMO_CALLSIGNS, FIXES, RUNWAYS) is hard          # conftest removed the API key


# ------------------------------------------------------------------ pipeline
@pytest.mark.asyncio
async def test_pipeline_shows_regex_caption_first_then_the_refined_one():
    world, hub, bus, pilot = FakeWorld(), FakeHub(), FakeBus(), FakePilot([])
    pipe = VoicePipeline(world, pilot, FakeComposer(), bus, hub, tts=FakeTTS(), seed=1)
    pipe.llm_client = FakeClient({"receiver": None, "intent": "instruction",
                                  "instructions": [{"type": "squawk", "value": "4521"}]})
    await pipe.on_typed("lufthansa four kilo alpha descend flight level eight zero then set the transponder to four five two one")
    finals = hub.captions("final")
    assert len(finals) == 2 and finals[0].tx_id == finals[1].tx_id
    assert [c.label for c in finals[0].chips] == ["DLH4KA", "FL80"]
    assert [c.label for c in finals[1].chips] == ["DLH4KA", "FL80", "SQK 4521"]
    assert "llm_s" in finals[1].latency
    sent = pilot.received[0]
    assert sent.parse_path == "mixed" and [i.type for i in sent.instructions] == ["flight_level", "squawk"]
    assert bus.of("ctrl_transmission")[0].data["tx"]["parse_path"] == "mixed"


@pytest.mark.asyncio
async def test_pipeline_does_not_call_the_llm_for_clean_transmissions():
    world, hub, bus, pilot = FakeWorld(), FakeHub(), FakeBus(), FakePilot([])
    pipe = VoicePipeline(world, pilot, FakeComposer(), bus, hub, tts=FakeTTS(), seed=1)
    pipe.llm_client = FakeClient({})
    await pipe.on_typed("speedbird one one seven turn left heading two four zero")
    assert not pipe.llm_client.calls and len(hub.captions("final")) == 1
