"""Pipeline trace (VOICE_PIPELINE §20.1): acceptance rules, exact stage names, fail-open."""
import json

import pytest

from server.contracts import ErrorRecord
from server.tests.voice.fakes import FakeBus, FakePilot, FakeSTT, FakeTimedTTS, heading_response
from server.tests.voice.test_pipeline import make, type_and_settle
from server.voice import trace as T

SPEC_NAMES = ["speech", "stt_partials", "stt_final", "normalise", "parse", "llm_fallback", "callsign_match",
              "evaluate", "pilot_decision", "compose", "validate", "tts_first_audio", "reaction_hold",
              "playback", "apply_scheduled"]


def traces(hub, final=None):
    out = [m.trace for m in hub.of("trace")]
    return [t for t in out if final is None or t.final == final]


async def ptt(pipe, hub, holder, frames=5, partials=3):
    await pipe.on_ptt("down")
    for i in range(frames):
        await pipe.on_audio_in(b"\x01\x00" * 1600)
        if i < partials:
            holder["stt"].on_partial("speedbird one one seven")
    await pipe.on_ptt("up")
    await hub.settle()


def test_stage_table_matches_the_spec_exactly():
    assert list(T.STAGES)[:15] == SPEC_NAMES and list(T.STAGES)[15] == "blocked"
    assert set(T.STAGES.values()) <= {"capture", "stt", "language", "sim", "pilot", "voice", "playback"}


@pytest.mark.asyncio
async def test_twenty_consecutive_transmissions_give_exactly_one_final_trace_each():
    pipe, world, pilot, hub, bus, holder = make(tts=FakeTimedTTS(chunks=3))
    for i in range(20):
        if i % 2:
            await ptt(pipe, hub, holder)
        else:
            await type_and_settle(pipe, hub)
    ctrl_ids = [e.tx_id for e in bus.of("ctrl_transmission")]
    finals = traces(hub, final=True)
    assert len(ctrl_ids) == 20 and sorted(t.tx_id for t in finals) == sorted(ctrl_ids)
    for t in finals:
        names = [s.name for s in t.stages]
        assert names == [n for n in SPEC_NAMES if n in names], names        # panel order
        assert {"speech", "stt_final", "normalise", "parse", "callsign_match", "evaluate", "pilot_decision",
                "compose", "validate", "tts_first_audio", "reaction_hold", "playback",
                "apply_scheduled"} <= set(names)
        assert all(s.t1_ms >= s.t0_ms for s in t.stages)                     # no negative durations
        assert all(s.t0_ms >= 0 for s in t.stages if s.name not in ("speech", "stt_partials"))
        assert t.speaker == "CTRL" and t.reply_callsign == "BAW117" and t.total_ms > 0
    voice = [t for t in finals if next(s for s in t.stages if s.name == "speech").status == "ok"]
    assert len(voice) == 10
    for t in voice:
        speech = next(s for s in t.stages if s.name == "speech")
        assert speech.t0_ms < 0 and speech.t1_ms == 0                       # speech ends at the anchor
        assert next(s for s in t.stages if s.name == "stt_partials").detail["count"] == 3
        assert next(s for s in t.stages if s.name == "stt_final").detail["provider"] == "scribe"


@pytest.mark.asyncio
async def test_machine_ms_and_total_ms_match_the_latency_chips():
    pipe, world, pilot, hub, bus, holder = make(tts=FakeTimedTTS(chunks=3, delay=0.05))
    await ptt(pipe, hub, holder)
    t = traces(hub, final=True)[0]
    chip = hub.captions("pilot_text")[0].latency
    tts = next(s for s in t.stages if s.name == "tts_first_audio")
    assert tts.detail["machine_ms"] == pytest.approx(chip["first_audio_s"] * 1000, abs=1.0)
    assert t.total_ms == pytest.approx(chip["perceived_s"] * 1000, abs=1.0)
    assert tts.t1_ms - tts.t0_ms >= 45                                        # the 50 ms TTS delay


@pytest.mark.asyncio
async def test_provisional_after_validate_updated_at_release_then_final():
    pipe, world, pilot, hub, bus, holder = make(tts=FakeTimedTTS(chunks=3))
    await type_and_settle(pipe, hub)
    seq = [(t.final, [s.name for s in t.stages][-1]) for t in traces(hub)]
    assert seq[0] == (False, "validate") and seq[1] == (False, "reaction_hold")
    assert seq[-1][0] is True and len([f for f, _ in seq if f]) == 1
    detail = next(s for s in traces(hub, True)[0].stages if s.name == "pilot_decision").detail
    assert detail == {"kind": "full", "error_policy": "evaluated"}


@pytest.mark.asyncio
async def test_planned_errors_are_revealed_only_when_reveal_is_on():
    err = ErrorRecord(id="e1", callsign="BAW117", kind="wrong_value_adjacent", mode="consistent",
                      element_type="heading", intended=240, readback_value=250, flown_value=250, t_planned=0)
    for reveal in (False, True):
        pipe, world, pilot, hub, bus, holder = make(pilot=FakePilot([heading_response(errors=[err])]))
        pipe.set_reveal_errors(reveal)
        await type_and_settle(pipe, hub)
        detail = next(s for s in traces(hub, True)[0].stages if s.name == "pilot_decision").detail
        assert ("planned_error_kind" in detail) is reveal
        assert "wrong_value_adjacent" not in json.dumps(detail) or reveal


@pytest.mark.asyncio
async def test_blocked_controller_transmission_has_a_blocked_stage():
    pipe, world, pilot, hub, bus, holder = make()
    pipe.freq.begin("p9", "BAW117")                         # a pilot is on the air
    await ptt(pipe, hub, holder)
    t = traces(hub, final=True)[0]
    names = [s.name for s in t.stages]
    assert "stt_final" in names and names[-1] == "blocked"
    blocked = t.stages[-1]
    assert blocked.status == "error" and blocked.category == "sim" and blocked.detail["callsigns"] == ["BAW117"]


@pytest.mark.asyncio
async def test_no_reply_and_failed_stt_still_end_with_one_final_trace():
    pipe, world, pilot, hub, bus, holder = make(pilot=FakePilot([]))
    await type_and_settle(pipe, hub)
    from server.voice.stt_scribe import STTError
    pipe2, _, _, hub2, _, holder2 = make(stt=FakeSTT(error=STTError("down")))
    pipe2.stt_fallback = None
    await ptt(pipe2, hub2, holder2)
    for h, last in ((hub, "pilot_decision"), (hub2, "stt_final")):
        finals = traces(h, final=True)
        assert len(finals) == 1 and finals[0].stages[-1].name == last


@pytest.mark.asyncio
async def test_pilot_initiated_call_is_anchored_at_compose_start():
    pipe, world, pilot, hub, bus, holder = make(tts=FakeTimedTTS(chunks=3))
    await pipe.speak(heading_response(kind="full"))            # nobody asked: in_reply_to=None
    t = traces(hub, final=True)[0]
    assert t.speaker == "BAW117" and t.stages[0].name == "compose" and t.stages[0].t0_ms == 0


@pytest.mark.asyncio
async def test_final_traces_are_appended_to_the_session_jsonl(tmp_path):
    pipe, world, pilot, hub, bus, holder = make()
    pipe._trace_dir = tmp_path
    await type_and_settle(pipe, hub)
    await type_and_settle(pipe, hub)
    rows = [json.loads(line) for line in (tmp_path / "traces.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and all(r["final"] for r in rows)
    assert "speedbird" not in json.dumps(rows).lower()              # no transcript text in traces


@pytest.mark.asyncio
async def test_tracing_fails_open(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("tracing bug")
    monkeypatch.setattr(T.Trace, "msg", boom)
    monkeypatch.setattr(T.TraceStage, "__init__", boom)
    pipe, world, pilot, hub, bus, holder = make()
    await type_and_settle(pipe, hub)
    assert hub.of("audio_start") and hub.captions("pilot_words") and pilot.spoken
    assert not hub.of("trace")


def test_session_dir_comes_from_the_event_log(tmp_path):
    from server.session import EventLog
    log = EventLog(tmp_path / "abc" / "events.jsonl")
    assert T.session_dir(log) == tmp_path / "abc" and T.session_dir(FakeBus()) is None
