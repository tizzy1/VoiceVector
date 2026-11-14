"""fal features offline: Chatterbox emergency voice, Wizper STT fallback, and the pipeline's fallbacks."""
import asyncio
import io
import wave

import numpy as np
import pytest

from server.agents.fal_emergency import ChatterboxVoice, EmergencyVoiceError, decode_wav, wav_bytes
from server.voice.pipeline import VoicePipeline
from server.voice.stt_fallback import WizperSTT
from server.voice.stt_scribe import STTError
from server.tests.voice.fakes import (FakeBus, FakeComposer, FakeHub, FakePilot, FakeSTT, FakeTTS, FakeWorld,
                                      heading_response)


def wav(samples: np.ndarray, rate: int, width: int = 2, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


class FakeFal:
    def __init__(self, text="speedbird one one seven turn left heading two four zero", delay=0.0, error=None,
                 audio=True):
        self.uploads, self.runs = [], []
        self.text, self.delay, self.error, self.audio = text, delay, error, audio

    async def upload(self, data, content_type, file_name=None):
        self.uploads.append((data, content_type, file_name))
        return f"https://fal.media/files/{len(self.uploads)}.wav"

    async def run(self, model, arguments):
        self.runs.append((model, arguments))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if model == "fal-ai/wizper":
            return {"text": f"  {self.text} ", "chunks": [], "languages": ["en"]}
        return {"audio": {"url": "https://fal.media/out.wav", "content_type": "audio/wav"}} if self.audio else {}


async def fetch_tone(url):
    return wav((np.sin(np.arange(24000) / 10) * 12000).astype("<i2"), 24000)


# ---------------------------------------------------------------- WAV decoding
def test_decode_wav_formats():
    mono = (np.arange(100) * 100).astype("<i2")
    pcm, rate = decode_wav(wav(mono, 24000))
    assert rate == 24000 and np.array_equal(np.frombuffer(pcm, "<i2"), mono)
    stereo = np.stack([mono, -mono], axis=1).astype("<i2")
    pcm, _ = decode_wav(wav(stereo, 24000, channels=2))
    assert not np.frombuffer(pcm, "<i2").any(), "stereo is mixed down"
    pcm8, _ = decode_wav(wav(np.full(10, 255, dtype=np.uint8), 8000, width=1))
    assert np.frombuffer(pcm8, "<i2")[0] > 32000
    raw24 = np.zeros((4, 3), dtype=np.uint8)
    raw24[:, 2] = 0x40                                           # +0.5 full scale in 24-bit
    pcm24, _ = decode_wav(wav(raw24.reshape(-1), 48000, width=3))
    assert abs(np.frombuffer(pcm24, "<i2")[0] - 16384) <= 1
    with pytest.raises(EmergencyVoiceError):
        decode_wav(b"definitely not a wav")
    assert decode_wav(wav_bytes(b"\x01\x00\x02\x00", 16000)) == (b"\x01\x00\x02\x00", 16000)


# ----------------------------------------------------------- Chatterbox voice
@pytest.mark.asyncio
async def test_chatterbox_clones_the_pilot_voice_once_and_streams_pcm():
    fal = FakeFal()
    reference = FakeTTS(chunks=2)
    voice = ChatterboxVoice("key", reference, client=fal, fetch=fetch_tone)
    chunks = [c async for c in voice.stream("mayday mayday mayday", "voiceA")]
    assert b"".join(chunks) and all(len(c) <= 4096 for c in chunks)
    assert voice.sample_rate == 24000 and voice.audio_format == "pcm_s16le"
    model, args = fal.runs[0]
    assert model == "fal-ai/chatterbox/text-to-speech"
    assert args["audio_url"] == "https://fal.media/files/1.wav" and args["exaggeration"] >= 0.8
    assert fal.uploads[0][0][:4] == b"RIFF" and reference.requests[0][1] == "voiceA"
    await asyncio.gather(*(collect(voice.stream("pan pan", "voiceA")) for _ in range(3)))
    assert len(fal.uploads) == 1, "one reference clip per voice, shared by concurrent emergencies"
    await collect(voice.stream("pan pan", "voiceB"))
    assert len(fal.uploads) == 2


async def collect(gen):
    return [c async for c in gen]


@pytest.mark.asyncio
@pytest.mark.parametrize("fal,match", [(FakeFal(delay=5), "exceeded"), (FakeFal(error=RuntimeError("503")), "503"),
                                       (FakeFal(audio=False), "no audio")])
async def test_chatterbox_failures_raise_a_single_error_type(fal, match):
    voice = ChatterboxVoice("key", FakeTTS(), client=fal, fetch=fetch_tone, deadline_s=0.1)
    with pytest.raises(EmergencyVoiceError, match=match):
        await collect(voice.stream("mayday", "v"))


def test_chatterbox_needs_a_key_and_a_reference_voice(monkeypatch):
    with pytest.raises(EmergencyVoiceError):
        ChatterboxVoice("", FakeTTS(), client=FakeFal())
    with pytest.raises(EmergencyVoiceError):
        ChatterboxVoice("k", None, client=FakeFal())
    assert ChatterboxVoice.from_env(FakeTTS()) is None            # conftest removed FAL_KEY


# --------------------------------------------------------------- Wizper STT
@pytest.mark.asyncio
async def test_wizper_uploads_wav_and_returns_text():
    fal = FakeFal()
    stt = WizperSTT("key", client=fal)
    text = await stt.transcribe(b"\x00\x01" * 16000)
    assert text == "speedbird one one seven turn left heading two four zero"
    data, ctype, _ = fal.uploads[0]
    assert data[:4] == b"RIFF" and ctype == "audio/wav"
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 16000)
    model, args = fal.runs[0]
    assert model == "fal-ai/wizper" and args["audio_url"].startswith("https://") and args["language"] == "en"
    assert await stt.transcribe(b"") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("fal", [FakeFal(delay=5), FakeFal(error=RuntimeError("queue full"))])
async def test_wizper_failures_become_stt_errors(fal):
    with pytest.raises(STTError):
        await WizperSTT("key", client=fal, deadline_s=0.1).transcribe(b"\x00\x01" * 1600)


def test_wizper_from_env_without_key():
    assert WizperSTT.from_env() is None


# ------------------------------------------------------------------ pipeline
def make(**kw):
    world, hub, bus = FakeWorld(), FakeHub(), FakeBus()
    pilot = kw.pop("pilot", FakePilot([]))
    pipe = VoicePipeline(world, pilot, FakeComposer(), bus, hub, tts=kw.pop("tts", FakeTTS()), seed=1, **kw)
    return pipe, hub, bus, pilot


def mayday():
    resp = heading_response("BAW117", kind="mayday", apply=False)
    resp.plan = resp.plan.model_copy(update={"kind": "mayday", "elements": [],
                                             "report_values": {"nature": "engine failure"}})
    return resp


@pytest.mark.asyncio
async def test_mayday_uses_the_emotional_voice_at_its_own_sample_rate():
    voice = ChatterboxVoice("key", FakeTTS(), client=FakeFal(), fetch=fetch_tone)
    pipe, hub, bus, _ = make(emergency=voice)
    await pipe.speak(mayday())
    start = hub.of("audio_start")[0]
    assert start.sample_rate == 24000 and start.emotion == "stressed"
    assert len(hub.of("audio_chunk")) == 12                         # 48000 bytes / 4096
    words = hub.captions("pilot_words")[0].words
    assert words[-1].t1 == pytest.approx(1.0, abs=0.02)            # 1 s of 24 kHz audio


@pytest.mark.asyncio
async def test_mayday_falls_back_to_elevenlabs_when_fal_fails():
    voice = ChatterboxVoice("key", FakeTTS(), client=FakeFal(error=RuntimeError("down")), fetch=fetch_tone)
    pipe, hub, bus, _ = make(emergency=voice)
    await pipe.speak(mayday())
    start = hub.of("audio_start")[0]
    assert start.sample_rate == 22050 and start.emotion == "stressed" and hub.of("audio_chunk")


@pytest.mark.asyncio
async def test_routine_readbacks_never_use_the_emergency_voice():
    fal = FakeFal()
    pipe, hub, bus, _ = make(emergency=ChatterboxVoice("key", FakeTTS(), client=fal, fetch=fetch_tone))
    await pipe.speak(heading_response("BAW117"))
    assert not fal.runs and hub.of("audio_start")[0].sample_rate == 22050


class FailingScribe(FakeSTT):
    async def finish(self):
        raise STTError("connect timeout")


@pytest.mark.asyncio
async def test_scribe_failure_is_rescued_by_wizper():
    fal = FakeFal(text="Speedbird 117, turn left heading 240.")
    pipe, hub, bus, pilot = make(stt_fallback=WizperSTT("key", client=fal))
    pipe.stt_factory = lambda terms, cb: FailingScribe()
    await pipe.on_ptt("down")
    for _ in range(10):
        await pipe.on_audio_in(b"\x01\x00" * 1600)
    await pipe.on_ptt("up")
    await hub.settle()
    assert any("backup" in c.text for c in hub.captions("final") if c.speaker == "SYSTEM")
    assert pilot.received and pilot.received[0].receiver == "BAW117"
    assert pilot.received[0].instructions[0].value == 240
    uploaded = fal.uploads[0][0]
    with wave.open(io.BytesIO(uploaded)) as w:
        assert w.getnframes() == 16000                               # the whole 1 s transmission was kept


@pytest.mark.asyncio
async def test_both_stt_services_failing_still_tells_the_controller():
    pipe, hub, bus, pilot = make(stt_fallback=WizperSTT("key", client=FakeFal(error=RuntimeError("x"))))
    pipe.stt_factory = lambda terms, cb: FailingScribe()
    await pipe.on_ptt("down")
    await pipe.on_audio_in(b"\x01\x00" * 16000)
    await pipe.on_ptt("up")
    assert any("Could not hear" in c.text for c in hub.captions("final"))
    assert not pilot.received


@pytest.mark.asyncio
async def test_wizper_alone_works_without_scribe_and_blips_are_ignored():
    fal = FakeFal()
    pipe, hub, bus, pilot = make(stt_fallback=WizperSTT("key", client=fal))
    pipe.stt_factory = None
    await pipe.on_ptt("down")
    await pipe.on_audio_in(b"\x01\x00" * 1000)                    # 62 ms blip
    await pipe.on_ptt("up")
    assert not fal.uploads and not pilot.received
    await pipe.on_ptt("down")
    await pipe.on_audio_in(b"\x01\x00" * 16000)
    await pipe.on_ptt("up")
    await hub.settle()
    assert fal.uploads and pilot.received
