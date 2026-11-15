"""Live /pipeline activity feed (server/voice/activity.py, 15 Nov, Sanju)."""
from __future__ import annotations

import asyncio

import pytest

from server.contracts import ActivityMsg
from server.voice import activity as A
from server.tests.voice.fakes import FakeHub


def _acts(hub) -> list[ActivityMsg]:
    return [m for m in hub.messages if isinstance(m, ActivityMsg)]


@pytest.fixture
def hub():
    h = FakeHub()
    A.attach(h)
    yield h
    A.detach(h)


async def test_emit_and_call_send_start_end_with_duration(hub):
    A.emit("parse", "Python grammar", "parse", ms=0.42, tx_id="c1", callsign="BAW117", source="voice")
    async with A.call("claude", A.CLAUDE, "coach tip", tx_id="c1"):
        await asyncio.sleep(0.02)
    await hub.settle()
    acts = _acts(hub)
    assert [(a.node, a.phase) for a in acts] == [("parse", "instant"), ("claude", "start"), ("claude", "end")]
    assert acts[0].ms == 0.4 and acts[0].detail == {"source": "voice"}
    assert acts[1].id == acts[2].id and acts[2].ms >= 15 and acts[2].status == "ok"


async def test_failed_call_is_reported_and_reraised(hub):
    with pytest.raises(RuntimeError):
        async with A.call("stt", A.WIZPER, "backup transcription"):
            raise RuntimeError("boom")
    await hub.settle()
    assert _acts(hub)[-1].status == "error"


async def test_span_ends_once_and_without_hub_nothing_breaks(hub):
    span = A.Span("voice", A.FLASH, "readback")
    span.end("ok")
    span.end("error")
    await hub.settle()
    assert [a.phase for a in _acts(hub)] == ["start", "end"]
    A.detach()
    assert A.emit("mic", "Browser microphone", "push-to-talk") is None
    A.Span("mic", "x", "y").end()                       # no hub: silently nothing


async def test_activity_never_carries_text_or_audio(hub):
    A.emit("readback", "Readback composer", "compose + check", tx_id="p1", callsign="BAW117")
    await hub.settle()
    dumped = _acts(hub)[0].model_dump(mode="json")
    assert set(dumped) == {"type", "id", "node", "service", "op", "phase", "status", "ms", "tx_id", "callsign",
                           "at_ms", "detail"}


def _pipeline():
    from server.tests.voice.test_pipeline import make           # the existing offline pipeline harness
    return make()


async def test_pipeline_typed_transmission_lights_the_whole_path():
    """parse -> sim -> pilot -> readback -> voice (API) -> radio, in that order, for one typed call."""
    pipe, world, pilot, hub, bus, holder = _pipeline()
    try:
        await pipe.on_typed("speedbird one one seven turn left heading two four zero")
        await hub.settle()
    finally:
        A.detach(hub)
    acts = [a for a in _acts(hub) if a.op != "warm-up"]
    order = [a.node for a in acts]
    path = ("parse", "sim", "pilot", "readback", "voice", "radio")
    assert all(n in order for n in path), order
    firsts = [order.index(n) for n in path]
    assert firsts == sorted(firsts)
    voice = [a for a in acts if a.node == "voice" and a.op == "readback"]
    assert [a.phase for a in voice] == ["start", "end"] and voice[-1].callsign == "BAW117"
    assert [a.phase for a in acts if a.node == "radio"] == ["start", "end"]


async def test_pipeline_mic_and_live_transcription_spans():
    pipe, world, pilot, hub, bus, holder = _pipeline()
    try:
        await pipe.on_ptt("down")
        for _ in range(5):
            await pipe.on_audio_in(b"\x01\x00" * 1600)
        await pipe.on_ptt("up")
        await hub.settle()
    finally:
        A.detach(hub)
    acts = _acts(hub)
    mic = [a for a in acts if a.node == "mic"]
    stt = [a for a in acts if a.node == "stt" and a.op == "live transcription"]
    assert [a.phase for a in mic] == ["start", "end"] and mic[1].detail["audio_s"] == 0.5
    assert [a.phase for a in stt] == ["start", "end"] and stt[1].status == "ok"
    assert stt[1].ms < 1000 and stt[1].detail["streamed_s"] == 0.5    # release -> final, not the stream
    assert acts.index(mic[0]) < acts.index(stt[0])


async def test_pipeline_short_blip_still_closes_the_transcription_span():
    pipe, world, pilot, hub, bus, holder = _pipeline()
    try:
        await pipe.on_ptt("down")
        await pipe.on_ptt("up")
        await hub.settle()
    finally:
        A.detach(hub)
    stt = [a for a in _acts(hub) if a.node == "stt"]
    assert [a.phase for a in stt] == ["start", "end"]


async def test_mayday_is_flagged_as_an_emergency():
    from server.tests.voice.fakes import heading_response
    pipe, world, pilot, hub, bus, holder = _pipeline()
    resp = heading_response("BAW117", delay=0.0, priority=10)
    resp.kind = "mayday"
    try:
        await pipe.speak(resp)
        await hub.settle()
    finally:
        A.detach(hub)
    acts = _acts(hub)
    sos = [a for a in acts if a.status == "emergency"]
    assert sos and sos[0].op == "MAYDAY" and sos[0].callsign == "BAW117"
    assert any(a.node == "voice" and a.op == "mayday" for a in acts)
