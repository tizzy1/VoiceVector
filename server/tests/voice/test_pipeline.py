"""VoicePipeline behaviour against offline fakes (no network, no API keys)."""
import asyncio

import pytest

from server.contracts import ErrorRecord
from server.voice.pipeline import VoicePipeline
from server.voice.stt_scribe import STTError
from server.tests.voice.fakes import (FakeBus, FakeComposer, FakeHub, FakePilot, FakeSTT, FakeTTS,
                                      FakeWorld, heading_response)


def make(pilot=None, tts="default", stt=None, world=None):
    world, hub, bus = world or FakeWorld(), FakeHub(), FakeBus()
    pilot = pilot or FakePilot()
    tts = FakeTTS() if tts == "default" else tts
    holder = {}

    def factory(terms, on_partial):
        holder["terms"] = terms
        holder["stt"] = stt or FakeSTT(on_partial=on_partial)
        holder["stt"].on_partial = on_partial
        return holder["stt"]

    pipe = VoicePipeline(world, pilot, FakeComposer(), bus, hub, tts=tts, stt_factory=factory, seed=42)
    return pipe, world, pilot, hub, bus, holder


async def type_and_settle(pipe, hub, text="speedbird one one seven turn left heading two four zero"):
    await pipe.on_typed(text)
    await hub.settle()


@pytest.mark.asyncio
async def test_typed_transmission_end_to_end_message_order():
    pipe, world, pilot, hub, bus, _ = make()
    await type_and_settle(pipe, hub)
    kinds = [(m.type, getattr(m, "stage", None)) for m in hub.messages]
    assert kinds[0] == ("caption", "final")
    start = kinds.index(("audio_start", None))
    assert kinds[start + 1] == ("caption", "pilot_text")
    assert kinds.count(("audio_chunk", None)) == 3
    assert kinds.index(("audio_end", None)) > kinds.index(("audio_chunk", None))
    assert ("caption", "pilot_words") in kinds
    final = hub.captions("final")[0]
    assert final.speaker == "CTRL" and final.chips[0].label == "BAW117"
    assert [c.label for c in final.chips][1] == "HDG 240 L"
    assert "turn left 240°" in final.plain_english and "British Airways 117" in final.plain_english
    assert final.latency["parse_s"] < 0.05
    chunks = hub.of("audio_chunk")
    assert [c.seq for c in chunks] == [0, 1, 2]


@pytest.mark.asyncio
async def test_events_and_sim_hooks():
    pipe, world, pilot, hub, bus, _ = make()
    await type_and_settle(pipe, hub)
    ctrl = bus.of("ctrl_transmission")[0]
    assert ctrl.callsign == "BAW117" and ctrl.data["tx"]["receiver"] == "BAW117"
    assert ctrl.data["evals"][0]["status"] == "OK"
    assert ctrl.data["tx"]["timings"]["t_final"] <= ctrl.data["tx"]["timings"]["t_parsed"]
    reply = bus.of("pilot_transmission")[0]
    assert reply.data["in_reply_to"] == ctrl.tx_id and reply.callsign == "BAW117"
    assert reply.data["kind"] == "full" and "heading 240" in reply.data["text"]
    for stage in ("t_ptt_up", "t_final", "t_play"):
        assert stage in reply.data["timings"]
    assert world.transmitting == [("BAW117", True), ("BAW117", False)]
    assert len(pilot.spoken) == 1
    cs, elements, at = world.applied[0]
    assert cs == "BAW117" and at == pytest.approx(world.t + 1.5)
    assert [m.stage for m in hub.messages if getattr(m, "stage", "") in ("pilot_text", "pilot_words")] == ["pilot_text", "pilot_words"]


@pytest.mark.asyncio
async def test_pilot_words_are_evenly_timed_over_the_audio_duration():
    pipe, *_, hub, bus, _ = make()
    await type_and_settle(pipe, hub)
    words = hub.captions("pilot_words")[0].words
    assert [w.w for w in words] == hub.captions("pilot_text")[0].text.split()
    assert words[0].t0 == 0 and all(a.t1 <= b.t0 + 1e-6 for a, b in zip(words, words[1:]))
    duration = 3 * 490 / (2 * 22050)
    assert words[-1].t1 == pytest.approx(duration, abs=0.01)


@pytest.mark.asyncio
async def test_voice_ptt_path_with_partials_and_final():
    pipe, world, pilot, hub, bus, holder = make()
    await pipe.on_ptt("down")
    assert holder["stt"].started and "Speedbird" in holder["terms"] and "niner" in holder["terms"]
    assert bus.of("ctrl_ptt_down") and pipe.is_frequency_busy()
    for _ in range(5):
        await pipe.on_audio_in(b"\x01\x00" * 1600)
    await pipe.on_ptt("up")
    await hub.settle()
    assert not pipe.is_frequency_busy() or pipe.freq.active
    assert bus.of("ctrl_ptt_up") and bus.of("ctrl_transmission")
    assert pilot.received[0].source == "voice" and pilot.received[0].receiver == "BAW117"
    tx = bus.of("ctrl_transmission")[0].data["tx"]["timings"]
    assert {"t_ptt_down", "t_ptt_up", "t_final", "t_parsed"} <= set(tx)
    assert hub.of("audio_start")


@pytest.mark.asyncio
async def test_short_blip_is_discarded():
    pipe, world, pilot, hub, bus, holder = make()
    await pipe.on_ptt("down")
    await pipe.on_audio_in(b"\x00\x00" * 800)          # 50 ms
    await pipe.on_ptt("up")
    await hub.settle()
    assert holder["stt"].cancelled and not pilot.received and not bus.of("ctrl_transmission")


@pytest.mark.asyncio
async def test_partials_are_rate_limited_to_five_per_second():
    pipe, world, pilot, hub, bus, holder = make()
    await pipe.on_ptt("down")
    for i in range(50):
        holder["stt"].on_partial(f"speedbird one one {i}")
    await hub.settle()
    assert 1 <= len(hub.captions("partial")) <= 2
    await pipe.on_ptt("up")


@pytest.mark.asyncio
async def test_stt_failure_tells_the_controller_and_does_not_crash():
    pipe, world, pilot, hub, bus, holder = make(stt=FakeSTT(error=STTError("connect timeout")))
    await pipe.on_ptt("down")
    await pipe.on_audio_in(b"\x01\x00" * 16000)
    await pipe.on_ptt("up")
    await hub.settle()
    notices = [c for c in hub.captions("final") if c.speaker == "SYSTEM"]
    assert notices and "typed" in notices[0].text
    assert not pilot.received
    await type_and_settle(pipe, hub)               # typed input still works afterwards
    assert pilot.received


@pytest.mark.asyncio
async def test_no_stt_configured_falls_back_to_typed_message():
    world, hub, bus = FakeWorld(), FakeHub(), FakeBus()
    pipe = VoicePipeline(world, FakePilot(), FakeComposer(), bus, hub, tts=FakeTTS(), stt_factory=None, seed=1)
    pipe.stt_factory = None
    await pipe.on_ptt("down")
    await pipe.on_ptt("up")
    assert any(c.speaker == "SYSTEM" and "typed" in c.text for c in hub.captions("final"))


@pytest.mark.asyncio
async def test_empty_transcript_gives_a_notice():
    pipe, world, pilot, hub, bus, holder = make(stt=FakeSTT(text="  "))
    await pipe.on_ptt("down")
    await pipe.on_audio_in(b"\x01\x00" * 16000)
    await pipe.on_ptt("up")
    assert any("Nothing heard" in c.text for c in hub.captions("final"))
    assert not pilot.received


@pytest.mark.asyncio
async def test_controller_keying_over_a_pilot_blocks_both_and_triggers_recall():
    recall = heading_response(cs="BAW117", kind="recall", apply=False)
    recall.plan = recall.plan.model_copy(update={"kind": "recall", "elements": []})
    pilot = FakePilot([heading_response(delay=0.0)])
    pilot.recall = recall
    pipe, world, _, hub, bus, holder = make(pilot=pilot, tts=FakeTTS(chunks=40, delay=0.0))
    # pilot starts talking (long audio keeps it on the air)
    await pipe.on_typed("speedbird one one seven turn left heading two four zero")
    for _ in range(100):
        if pipe.freq.active:
            break
        await asyncio.sleep(0.01)
    assert pipe.freq.active and pipe.is_frequency_busy()
    tx_on_air = next(iter(pipe.freq.active))
    await pipe.on_ptt("down")
    overlap = bus.of("overlap")
    assert overlap and overlap[0].data["with_ctrl"] is True and tx_on_air in overlap[0].data["tx_ids"]
    await pipe.on_audio_in(b"\x01\x00" * 16000)
    await pipe.on_ptt("up")
    await hub.settle(10)
    assert bus.of("ctrl_blocked")
    assert len(bus.of("ctrl_transmission")) == 1            # only the first, typed transmission was delivered
    assert pilot.blocked_calls == [("BAW117", tx_on_air)]
    assert any("blocked" in c.text.lower() for c in hub.captions("final"))
    assert holder["stt"].cancelled
    replies = bus.of("pilot_transmission")
    assert any(r.data["blocked"] for r in replies)           # the stepped-on readback
    assert any(r.data["kind"] == "recall" for r in replies)  # and the re-call
    blocked_reply = next(r for r in replies if r.data["blocked"])
    assert all(resp.kind != "full" for resp, _ in pilot.spoken)   # a blocked readback is never "heard"
    assert blocked_reply.data["in_reply_to"]


@pytest.mark.asyncio
async def test_pilots_do_not_overlap_and_mayday_goes_first():
    world, hub, bus = FakeWorld(), FakeHub(), FakeBus()
    routine = heading_response("EZY45AB", delay=0.0)
    mayday = heading_response("BAW117", delay=0.0, priority=10)
    pipe = VoicePipeline(world, FakePilot([]), FakeComposer(), bus, hub, tts=FakeTTS(chunks=4), seed=3)
    await pipe.on_ptt("down")                       # frequency is held while both queue up
    t1 = hub.spawn(pipe.speak(routine))
    t2 = hub.spawn(pipe.speak(mayday))
    await asyncio.sleep(0.1)
    assert not hub.of("audio_start")                # nobody talks over the keyed controller
    pipe._ptt = None
    pipe.freq.ctrl_up()
    await hub.settle(10)
    order = [m.callsign for m in hub.of("audio_start")]
    assert order == ["BAW117", "EZY45AB"]
    starts = [i for i, m in enumerate(hub.messages) if m.type == "audio_start"]
    ends = [i for i, m in enumerate(hub.messages) if m.type == "audio_end"]
    assert ends[0] < starts[1]                      # the second pilot waited for the first to finish


@pytest.mark.asyncio
async def test_tts_failure_degrades_to_captions_and_still_applies_the_clearance():
    from server.voice.tts import TTSError
    pipe, world, pilot, hub, bus, _ = make(tts=FakeTTS(fail=TTSError("402 quota")))
    await type_and_settle(pipe, hub)
    assert hub.captions("pilot_text") and not hub.of("audio_start")
    assert world.applied and pilot.spoken
    assert bus.of("pilot_transmission")[0].data["spoken_audio"] is False


@pytest.mark.asyncio
async def test_tts_dying_midstream_still_ends_the_transmission_cleanly():
    from server.voice.tts import TTSError
    pipe, world, pilot, hub, bus, _ = make(tts=FakeTTS(chunks=5, fail=TTSError("reset"), fail_after=2))
    await type_and_settle(pipe, hub)
    assert len(hub.of("audio_chunk")) == 2 and hub.of("audio_end")
    assert world.transmitting[-1] == ("BAW117", False) and world.applied


@pytest.mark.asyncio
async def test_no_tts_at_all_is_captions_only():
    pipe, world, pilot, hub, bus, _ = make(tts=None)
    pipe.tts = None
    await type_and_settle(pipe, hub)
    assert hub.captions("pilot_text") and hub.captions("pilot_words") and not hub.of("audio_start")
    assert world.applied


@pytest.mark.asyncio
async def test_reveal_errors_toggle():
    err = ErrorRecord(id="E1", callsign="BAW117", kind="wrong_value_adjacent", mode="consistent",
                      element_type="altitude", intended=4000, readback_value=5000, flown_value=5000, t_planned=1.0)
    pilot = FakePilot([heading_response(errors=[err])])
    pipe, world, _, hub, bus, _ = make(pilot=pilot)
    await type_and_settle(pipe, hub)
    assert hub.captions("pilot_text")[0].error_reveal is None
    pipe.set_reveal_errors(True)
    await type_and_settle(pipe, hub)
    assert hub.captions("pilot_text")[-1].error_reveal == "said 5,000 — cleared 4,000"
    assert pipe.error_reveal_text(heading_response(errors=[err.model_copy(update={"mode": "execution_only"})])) == \
        "read back 4,000 correctly, flying 5,000"


@pytest.mark.asyncio
async def test_latency_is_reported_and_metrics_recorded():
    pipe, world, pilot, hub, bus, _ = make(tts=FakeTTS(delay=0.05))
    await type_and_settle(pipe, hub)
    latency = hub.captions("pilot_text")[0].latency
    assert latency["first_audio_s"] >= 0.04 and latency["perceived_s"] >= latency["first_audio_s"] - 0.01
    summary = pipe.metrics.summary()
    assert summary["perceived_s"]["n"] == 1 and summary["first_audio_s"]["p50"] >= 0.04


@pytest.mark.asyncio
async def test_silence_and_unknown_responses_are_ignored():
    silent = heading_response()
    silent.kind = "silence"
    nobody = heading_response()
    nobody.callsign = None
    pipe, world, pilot, hub, bus, _ = make(pilot=FakePilot([silent, nobody]))
    await type_and_settle(pipe, hub)
    assert not hub.of("audio_start") and not bus.of("pilot_transmission")


@pytest.mark.asyncio
async def test_voices_and_settings_are_deterministic_per_callsign():
    pipe, world, pilot, hub, bus, _ = make(tts=FakeTTS())
    await pipe.speak(heading_response("BAW117", persona="crisp_airline"))
    await pipe.speak(heading_response("EZY45AB", persona="rushed"))
    await pipe.speak(heading_response("BAW117", persona="crisp_airline"))
    requests = pipe.tts.requests
    assert requests[0][1] == requests[2][1] and requests[0][1] != requests[1][1]
    # Voices are pace-calibrated: a naturally fast voice gets a LOWER speed setting, so compare the
    # resulting pace (speed x the voice's natural words/s), which is what the listener hears.
    natural = {v.id: v.natural_wps or 3.0 for v in pipe.voices.pool}
    pace = [r[2]["speed"] * natural[r[1]] for r in requests]
    assert pace[1] > pace[0], "the rushed pilot speaks faster than the crisp one"


@pytest.mark.asyncio
async def test_stressed_calls_are_flagged_for_the_audio_engine():
    resp = heading_response("BAW117", kind="mayday")
    resp.plan = resp.plan.model_copy(update={"kind": "mayday", "elements": []})
    pipe, world, pilot, hub, bus, _ = make()
    await pipe.speak(resp)
    assert hub.of("audio_start")[0].emotion == "stressed"


# ------------------------------------------------------------ deliberate overlap
def _inject_overlap(bus, callers):
    from server.contracts import Event
    bus.emit(Event(t=1.0, type="inject", actor="INSTRUCTOR", data={"kind": "force_overlap", "callers": callers}))


@pytest.mark.asyncio
async def test_force_overlap_releases_both_callers_together_with_a_shared_group():
    recall = heading_response("BAW117", kind="recall", apply=False)
    recall.plan = recall.plan.model_copy(update={"kind": "recall", "elements": []})
    pilot = FakePilot([])
    pilot.recall = recall
    pipe, world, _, hub, bus, _ = make(pilot=pilot, tts=FakeTTS(chunks=6))
    _inject_overlap(bus, ["BAW117", "EZY45AB"])
    a = heading_response("BAW117", delay=0.0)
    b = heading_response("EZY45AB", delay=0.3)                  # different reaction times: still released together
    hub.spawn(pipe.speak(a))
    hub.spawn(pipe.speak(b))
    await hub.settle(10)
    starts = [m for m in hub.messages if m.type == "audio_start"]
    pair = [m for m in starts if m.overlap_group]
    assert len(pair) == 2 and pair[0].overlap_group == pair[1].overlap_group
    assert {m.callsign for m in pair} == {"BAW117", "EZY45AB"}
    # audio_end only means "all data sent"; what matters is that both pilots are on the air at once
    on_air = [(cs, on) for cs, on in world.transmitting if cs in ("BAW117", "EZY45AB")][:4]
    assert [on for _, on in on_air] == [True, True, False, False], on_air
    overlap = bus.of("overlap")
    assert len(overlap) == 1 and overlap[0].data["with_ctrl"] is False
    assert set(overlap[0].data["tx_ids"]) == {m.tx_id for m in pair}
    assert {e.callsign for e in bus.of("pilot_blocked")} == {"BAW117", "EZY45AB"}
    assert {cs for cs, _ in pilot.blocked_calls} == {"BAW117", "EZY45AB"}
    assert pilot.spoken == [(r, t) for r, t in pilot.spoken if r.kind == "recall"], "garbled calls are never 'heard'"
    assert any(r.data["kind"] == "recall" for r in bus.of("pilot_transmission"))


@pytest.mark.asyncio
async def test_overlap_partner_that_never_comes_lets_the_other_talk_normally(monkeypatch):
    from server.voice import pipeline as module
    monkeypatch.setattr(module, "OVERLAP_WAIT_S", 0.1)
    pipe, world, pilot, hub, bus, _ = make()
    _inject_overlap(bus, ["BAW117", "EZY45AB"])
    await pipe.speak(heading_response("BAW117"))
    start = hub.of("audio_start")[0]
    assert start.overlap_group is None and not bus.of("overlap") and not bus.of("pilot_blocked")
    assert len(pilot.spoken) == 1                               # an ordinary, heard transmission
    await pipe.speak(heading_response("EZY45AB"))               # the late partner is not overlapped either
    assert hub.of("audio_start")[1].overlap_group is None and not bus.of("pilot_blocked")


@pytest.mark.asyncio
async def test_unrelated_injects_do_not_create_overlaps():
    from server.contracts import Event
    pipe, world, pilot, hub, bus, _ = make()
    bus.emit(Event(t=1.0, type="inject", actor="INSTRUCTOR", data={"kind": "mayday"}))
    bus.emit(Event(t=1.0, type="inject", actor="INSTRUCTOR", data={"kind": "force_overlap", "callers": ["BAW117"]}))
    await pipe.speak(heading_response("BAW117"))
    assert hub.of("audio_start")[0].overlap_group is None



@pytest.mark.asyncio
async def test_pilot_words_use_real_tts_timestamps_mapped_onto_display_words():
    from server.tests.voice.fakes import FakeTimedTTS
    pipe, world, pilot, hub, bus, _ = make(tts=FakeTimedTTS(chunks=3))
    await type_and_settle(pipe, hub)
    text = hub.captions("pilot_text")[0].text
    words = hub.captions("pilot_words")[0].words
    assert [w.w for w in words] == text.split()
    tts_text = pipe.tts.requests[0][0]
    numeric = next(w for w in words if any(c.isdigit() for c in w.w))
    spoken_start = tts_text.index(next(t for t in ("two four zero", "two") if t in tts_text)) * 0.06
    assert abs(numeric.t0 - spoken_start) < 0.07, "the number starts when its first spoken digit starts"
    assert numeric.t1 - numeric.t0 > 0.3, "and spans all of its spoken digits"


@pytest.mark.asyncio
async def test_mismatched_alignment_falls_back_to_an_even_spread():
    from server.tests.voice.fakes import FakeTimedTTS
    pipe, world, pilot, hub, bus, _ = make(tts=FakeTimedTTS(chunks=3, break_alignment=True))
    await type_and_settle(pipe, hub)
    words = hub.captions("pilot_words")[0].words
    assert words and words[0].t0 == 0 and words[-1].t1 == pytest.approx(3 * 490 / 44100, abs=0.01)
