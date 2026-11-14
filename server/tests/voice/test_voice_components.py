"""Frequency manager, Scribe stream, TTS client, voices, captions and metrics (offline)."""
import asyncio
import base64
import json

import httpx
import pytest

from server.contracts import Instruction, Transmission
from server.voice.captions import chips_for, estimated_words, plain_english
from server.voice.frequency import FrequencyManager
from server.voice.metrics import Metrics, Stamps, percentile
from server.voice.parser import parse
from server.voice.stt_scribe import (MAX_KEYTERMS, STATIC_KEYTERMS, ScribeStream, STTError, build_keyterms)
from server.voice.tts import ElevenLabsTTS, TTSError, format_info
from server.voice.voices import DEFAULT_POOL, Voices, settings_for


# ------------------------------------------------------------------ frequency manager
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_ctrl_keying_marks_on_air_pilots_as_blocked():
    f = FrequencyManager()
    f.begin("p1", "BAW117")
    assert f.is_busy()
    assert f.ctrl_down() == ["p1"] and f.is_blocked("p1")
    assert f.end("p1") is True and not f.is_blocked("p1")
    f.ctrl_up()
    assert not f.is_busy()


def test_ctrl_down_with_a_free_frequency_blocks_nobody():
    f = FrequencyManager()
    assert f.ctrl_down() == [] and f.is_busy()
    f.ctrl_up()
    assert not f.is_busy()


@pytest.mark.asyncio
async def test_pilot_waits_for_a_free_frequency_then_gap():
    clock = Clock()
    f = FrequencyManager(clock=clock)
    f.ctrl_down()
    waiter = asyncio.create_task(f.wait_turn())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    clock.t += 5
    f.ctrl_up()
    clock.t += 5                                     # gap elapsed
    f._pump()
    await asyncio.wait_for(waiter, 1)
    f.begin("p1", "BAW117")
    assert f.is_busy()


@pytest.mark.asyncio
async def test_priority_orders_the_queue_and_one_turn_at_a_time(monkeypatch):
    from server.voice import frequency
    monkeypatch.setattr(frequency, "GAP_RANGE_S", (0.01, 0.02))
    f = FrequencyManager()
    f.begin("busy", "AAA1")
    order = []

    async def go(name, priority):
        await f.wait_turn(priority)
        assert not f.active, "two pilots on the air at once"
        order.append(name)
        f.begin(name, name)
        await asyncio.sleep(0.01)
        f.end(name)

    tasks = [asyncio.create_task(go("routine", 0)), asyncio.create_task(go("mayday", 10)),
             asyncio.create_task(go("routine2", 0))]
    await asyncio.sleep(0.05)
    f.end("busy")
    await asyncio.wait_for(asyncio.gather(*tasks), 3)
    assert order[0] == "mayday" and order[1:] == ["routine", "routine2"]


@pytest.mark.asyncio
async def test_gap_between_transmissions_is_300_to_1000_ms():
    clock = Clock()
    gaps = []
    for seed in range(40):
        f = FrequencyManager(seed=seed, clock=clock)
        f.begin("p", "X")
        f.end("p")
        gaps.append(f._free_at - clock.t)
    assert all(0.3 <= g <= 1.0 for g in gaps) and len({round(g, 2) for g in gaps}) > 10


@pytest.mark.asyncio
async def test_cancelled_reservation_does_not_deadlock_the_frequency():
    f = FrequencyManager()
    await f.wait_turn()
    assert f._reserved
    f.cancel_reservation()
    f._free_at = 0
    await asyncio.wait_for(f.wait_turn(), 1)


@pytest.mark.asyncio
async def test_force_turn_skips_the_gap():
    clock = Clock()
    f = FrequencyManager(clock=clock)
    f.begin("p", "X")
    f.end("p")
    f.force_turn()
    await asyncio.wait_for(f.wait_turn(), 1)


# ------------------------------------------------------------------------ Scribe
class FakeConnection:
    def __init__(self, partials=("speedbird", "speedbird one one"), final="speedbird one one seven turn left"):
        self.handlers, self.sent, self.committed, self.closed = {}, [], False, False
        self.partials, self.final = partials, final

    def on(self, event, cb):
        self.handlers.setdefault(event, []).append(cb)

    async def send(self, data):
        self.sent.append(base64.b64decode(data["audio_base_64"]))
        if len(self.sent) == 2:
            for p in self.partials:
                for cb in self.handlers.get("partial_transcript", []):
                    cb({"text": p})

    async def commit(self):
        self.committed = True
        for cb in self.handlers.get("committed_transcript", []):
            cb({"text": self.final})

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_scribe_buffers_audio_until_connected_and_returns_the_committed_text():
    conn = FakeConnection()
    gate = asyncio.Event()

    async def connect():
        await gate.wait()                              # handshake still in flight while the controller speaks
        return conn

    seen = []
    stream = ScribeStream("k", ["Speedbird"], seen.append, connect=connect)
    stream.start()
    for i in range(5):
        stream.feed(bytes([i]) * 3200)
    gate.set()
    text = await stream.finish()
    assert text == "speedbird one one seven turn left"
    assert [chunk[0] for chunk in conn.sent] == [0, 1, 2, 3, 4]      # nothing lost, order kept
    assert conn.committed and conn.closed
    assert seen == ["speedbird", "speedbird one one"]
    assert stream.duration_s == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_scribe_connect_timeout_raises_stt_error():
    async def never():
        await asyncio.sleep(30)

    stream = ScribeStream("k", connect=never, connect_timeout=0.05, commit_timeout=0.05)
    stream.start()
    stream.feed(b"\x00" * 3200)
    with pytest.raises(STTError):
        await stream.finish()


@pytest.mark.asyncio
async def test_scribe_connection_error_raises_stt_error():
    async def boom():
        raise ConnectionRefusedError("no route")

    stream = ScribeStream("k", connect=boom)
    stream.start()
    with pytest.raises(STTError, match="ConnectionRefusedError"):
        await stream.finish()


@pytest.mark.asyncio
async def test_scribe_empty_transcript_is_not_an_error():
    stream = ScribeStream("k", connect=lambda: _ready(FakeConnection(final="")))
    stream.start()
    stream.feed(b"\x00" * 3200)
    assert await stream.finish() == ""


async def _ready(conn):
    return conn


@pytest.mark.asyncio
async def test_scribe_cancel_stops_the_task():
    async def slow():
        await asyncio.sleep(30)

    stream = ScribeStream("k", connect=slow)
    stream.start()
    await stream.cancel()
    assert stream._task.done()


def test_keyterms_respect_scribe_limits():
    terms = build_keyterms(["Speedbird", "speedbird", "Easy"], STATIC_KEYTERMS, ["x" * 25, *[f"FIX{i}" for i in range(80)]])
    assert len(terms) == MAX_KEYTERMS
    assert all(len(t) <= 20 for t in terms) and len({t.lower() for t in terms}) == len(terms)
    assert terms[0] == "Speedbird" and "x" * 25 not in terms


# --------------------------------------------------------------------------- TTS
def tts_with(handler, **kw) -> ElevenLabsTTS:
    client = httpx.AsyncClient(base_url="https://api.elevenlabs.io", transport=httpx.MockTransport(handler))
    return ElevenLabsTTS("secret", client=client, **kw)


@pytest.mark.asyncio
async def test_tts_streams_pcm_keeps_16bit_alignment_and_caches():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, content=b"\x01\x02" * 2501)             # 5002 bytes

    tts = tts_with(handler)
    chunks = [c async for c in tts.stream("hello", "voiceA", {"stability": 0.5})]
    assert b"".join(chunks) == b"\x01\x02" * 2501 and all(len(c) % 2 == 0 for c in chunks)
    request = calls[0]
    assert request.url.path == "/v1/text-to-speech/voiceA/stream"
    assert request.url.params["output_format"] == "pcm_22050"
    assert request.headers["xi-api-key"] == "secret"
    body = json.loads(request.content)
    assert body["text"] == "hello" and body["model_id"] == "eleven_flash_v2_5" and body["voice_settings"]["stability"] == 0.5
    again = [c async for c in tts.stream("hello", "voiceA", {"stability": 0.5})]
    assert again == chunks and len(calls) == 1                              # served from cache
    assert tts.cached("voiceA", "hello", {"stability": 0.5})
    await tts.stream("hello", "voiceB").__anext__()
    assert len(calls) == 2                                                  # other voice -> new request


@pytest.mark.asyncio
async def test_tts_odd_chunk_boundaries_never_split_a_sample():
    def handler(request):
        return httpx.Response(200, content=bytes(range(200)) * 41)

    tts = tts_with(handler)
    pieces = [c async for c in tts.stream("x", "v")]
    assert all(len(c) % 2 == 0 for c in pieces) and b"".join(pieces) == bytes(range(200)) * 41


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 429, 500])
async def test_tts_http_errors_raise_tts_error(status):
    tts = tts_with(lambda r: httpx.Response(status, json={"detail": {"status": "quota_exceeded"}}))
    with pytest.raises(TTSError, match=str(status)):
        async for _ in tts.stream("hello", "v"):
            pass


@pytest.mark.asyncio
async def test_tts_network_error_raises_tts_error_and_is_not_cached():
    def handler(request):
        raise httpx.ConnectError("down")

    tts = tts_with(handler)
    with pytest.raises(TTSError):
        async for _ in tts.stream("hello", "v"):
            pass
    assert not tts.cached("v", "hello")


def test_tts_requires_a_key_and_reports_format():
    with pytest.raises(TTSError):
        ElevenLabsTTS("")
    assert format_info("pcm_22050") == ("pcm_s16le", 22050)
    assert format_info("mp3_44100_128") == ("mp3", 44100)


@pytest.mark.asyncio
async def test_tts_lru_evicts_oldest():
    tts = tts_with(lambda r: httpx.Response(200, content=b"\x00\x00" * 10), cache_size=2)
    for text in ("a", "b", "c"):
        _ = [c async for c in tts.stream(text, "v")]
    assert not tts.cached("v", "a") and tts.cached("v", "b") and tts.cached("v", "c")


# ------------------------------------------------------------------------ voices
def test_voice_assignment_is_deterministic_and_spreads_aircraft():
    callsigns = ["BAW117", "EZY45AB", "DLH4KA", "UAE15", "WZZ4471", "RYR8KP"]
    a = Voices(seed=42)
    b = Voices(seed=42)
    assert [a.voice_for(c, "crisp_airline").id for c in callsigns] == [b.voice_for(c, "crisp_airline").id for c in callsigns]
    assert a.voice_for("BAW117", "rushed") is a.voice_for("BAW117", "crisp_airline")   # sticky per callsign
    ids = {a.voice_for(c, "crisp_airline").id for c in callsigns}
    assert len(ids) >= 4
    assert len(DEFAULT_POOL) == 8 and len({v.id for v in DEFAULT_POOL}) == 8


def test_voice_pool_loads_from_data_file(tmp_path):
    (tmp_path / "voices.json").write_text(json.dumps({"voices": [{"id": "abc", "name": "X", "personas": ["student"]}]}))
    assert Voices.load(tmp_path).voice_for("BAW117", "student").id == "abc"
    assert Voices.load(tmp_path / "missing").pool == DEFAULT_POOL


def test_persona_settings_differ_and_stress_overrides():
    assert settings_for("rushed")["speed"] > settings_for("calm_longhaul")["speed"]
    assert settings_for("crisp_airline", stressed=True)["stability"] == settings_for("stressed")["stability"]
    assert settings_for("unknown") == settings_for("crisp_airline")


# ---------------------------------------------------------------------- captions
def test_chips_and_plain_english():
    tx = parse("speedbird one one seven turn left heading two four zero descend altitude four thousand feet "
               "speed two one zero knots or less cleared i l s approach runway two six left")
    labels = [c.label for c in chips_for(tx)]
    assert labels == ["BAW117", "HDG 240 L", "ALT 4000", "SPD 210-", "ILS 26L"]
    text = plain_english(tx, {"BAW117": "British Airways 117"})
    assert text == "British Airways 117 → turn left 240°, descend 4,000 ft, speed 210 kt or less, cleared ILS 26L"


def test_chips_for_non_instruction_intents():
    tx = parse("easy four five alpha bravo negative descend altitude four thousand feet")
    assert [c.label for c in chips_for(tx)] == ["EZY45AB", "ALT 4000", "READBACK CORRECTION"]
    assert plain_english(tx).startswith("EZY45AB → correction:")
    assert plain_english(parse("speedbird one one seven say again")).endswith("say again request")
    assert plain_english(parse("ryanair four two descend altitude four thousand feet")).startswith("Unknown station")


def test_estimated_words_cover_the_whole_duration_in_order():
    words = estimated_words("Left heading 240, Speedbird 117", 2.0)
    assert [w.w for w in words] == ["Left", "heading", "240,", "Speedbird", "117"]
    assert words[0].t0 == 0 and words[-1].t1 == pytest.approx(2.0, abs=0.01)
    assert estimated_words("", 2.0) == [] and estimated_words("x", 0) == []


# ----------------------------------------------------------------------- metrics
def test_percentiles_and_rolling_metrics():
    assert percentile([], 50) is None and percentile([5], 90) == 5
    assert percentile([1, 2, 3, 4, 5], 50) == 3 and percentile(list(range(1, 11)), 90) == pytest.approx(9.1)
    m = Metrics(window=3)
    for v in (1, 2, 3, 100):
        m.record("x", v)
    m.record("x", None)
    m.record("x", -1)
    assert m.summary()["x"] == {"p50": 3, "p90": pytest.approx(80.6), "n": 3}   # window [2, 3, 100]


def test_stamps_span():
    s = Stamps()
    s.mark("a", 1.0)
    s.mark("b", 3.5)
    assert s.span("a", "b") == 2.5 and s.span("a", "zzz") is None



@pytest.mark.asyncio
async def test_tts_timed_stream_parses_ndjson_audio_and_alignment():
    lines = [
        {"audio_base64": base64.b64encode(b"\x01\x02\x03").decode(), "alignment": {
            "characters": list("left "), "character_start_times_seconds": [0, .1, .2, .3, .4],
            "character_end_times_seconds": [.1, .2, .3, .4, .5]}},
        {"audio_base64": base64.b64encode(b"\x04").decode(), "alignment": {"characters": []}},
        {"audio_base64": "", "alignment": None},
    ]
    body = "\n".join(json.dumps(l) for l in lines) + "\n"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=body.encode())

    tts = tts_with(handler)
    items = [x async for x in tts.stream_timed("left", "v")]
    assert calls[0].url.path == "/v1/text-to-speech/v/stream/with-timestamps"
    assert b"".join(a for a, _ in items) == b"\x01\x02\x03\x04"
    assert all(len(a) % 2 == 0 for a, _ in items)
    assert [al["characters"] for _, al in items if al] == [list("left ")]
    again = [x async for x in tts.stream_timed("left", "v")]
    assert again == items and len(calls) == 1


def test_word_alignment_helpers():
    from server.voice.captions import align_display_words, spoken_word_times
    text = "Q N H one zero one three, easy four five alpha bravo"
    t = [i * 0.05 for i in range(len(text))]
    spoken = spoken_word_times(text, list(text), t, [x + 0.04 for x in t])
    words = align_display_words("QNH 1013, Easy 45AB", spoken)
    assert [w.w for w in words] == ["QNH", "1013,", "Easy", "45AB"]
    assert words[0].t0 == 0 and words[1].t0 == pytest.approx(text.index("one") * 0.05)
    assert words[-1].t1 == pytest.approx(t[-1] + 0.04, abs=1e-3)
    assert align_display_words("Right heading 240", spoken_word_times(
        "left heading two four zero", list("left heading two four zero"), t, t)) is None
    assert spoken_word_times("abc", list("xyz"), [0, 1, 2], [1, 2, 3]) is None


# ------------------------------------------------------------- designed voices
def test_voices_follow_the_airline_accent_and_students_sound_like_students(tmp_path):
    from server.voice.voices import Voice, Voices
    pool = (Voice("irish", "Irish", "male", ("rushed", "crisp_airline"), ("RYR",), "Irish"),
            Voice("german", "German", "male", ("crisp_airline",), ("DLH",), "German"),
            Voice("uk1", "UK1", "male", ("crisp_airline", "calm_longhaul"), ("BAW", "EZY"), "British"),
            Voice("uk2", "UK2", "female", ("crisp_airline", "rushed"), ("BAW", "EZY"), "British"),
            Voice("student", "Student", "female", ("student", "stressed"), (), "British"))
    v = Voices(pool=pool, seed=1)
    assert v.voice_for("RYR42KP", "crisp_airline").id == "irish"
    assert v.voice_for("DLH4KA", "calm_longhaul").id == "german"        # airline beats persona
    assert {v.voice_for("BAW117", "crisp_airline").id, v.voice_for("EZY45AB", "crisp_airline").id} == {"uk1", "uk2"}
    assert v.voice_for("GABCD", "student").id == "student"
    assert v.voice_for("XYZ1", "rushed").id in {"irish", "uk2"}          # unknown airline -> persona


def test_voices_json_with_airlines_round_trips(tmp_path):
    import json
    from server.voice.voices import Voices
    (tmp_path / "voices.json").write_text(json.dumps({"voices": [
        {"id": "a", "name": "A", "gender": "male", "personas": ["crisp_airline"], "airlines": ["RYR"], "accent": "Irish"},
        {"name": "no id is skipped"}]}))
    pool = Voices.load(tmp_path).pool
    assert len(pool) == 1 and pool[0].airlines == ("RYR",) and pool[0].accent == "Irish"


def test_design_keeps_the_most_intelligible_preview_and_skips_existing(tmp_path):
    import asyncio
    import base64
    import json
    from types import SimpleNamespace
    from server.voice import voice_design as vd

    spec = vd.SPECS[0]
    good, mumble = spec.sample, "speedbird something heading something"
    pcm = (b"\x01\x00" * 16000 * 7)                                  # 7 s at 16 kHz: realistic pace

    class Client:
        def __init__(self):
            self.created, self.designs = [], 0
            self.text_to_voice = SimpleNamespace(design=self.design, create=self.create)
            self.speech_to_text = SimpleNamespace(convert=self.convert)
            self.voices = SimpleNamespace(search=self.search)
            self.heard = iter([mumble, good, mumble])

        async def search(self, **kw):
            return SimpleNamespace(voices=[])

        async def design(self, **kw):
            self.designs += 1
            assert kw["text"] == spec.sample and kw["output_format"] == "pcm_16000"
            assert "radiotelephony" in kw["voice_description"]
            return SimpleNamespace(previews=[SimpleNamespace(generated_voice_id=f"g{i}", duration_secs=7.0,
                                                             audio_base_64=base64.b64encode(pcm).decode())
                                             for i in range(3)])

        async def convert(self, **kw):
            assert kw["file_format"] == "pcm_s16le_16"
            return SimpleNamespace(text=next(self.heard))

        async def create(self, **kw):
            self.created.append(kw)
            return SimpleNamespace(voice_id=f"voice-{kw['generated_voice_id']}")

    client = Client()
    path = tmp_path / "voices.json"
    entries = asyncio.run(vd.design_all(client, specs=(spec,), path=path))
    assert client.created[0]["generated_voice_id"] == "g1", "the clearest preview is kept"
    assert set(client.created[0]["played_not_selected_voice_ids"]) == {"g0", "g2"}
    saved = json.loads(path.read_text())["voices"][0]
    assert saved["id"] == "voice-g1" and saved["airlines"] == list(spec.airlines) and "audio" not in saved
    again = asyncio.run(vd.design_all(client, specs=(spec,), path=path))
    assert client.designs == 1 and again[0]["id"] == "voice-g1", "re-running never re-designs or wastes a slot"


def test_offline_radio_effect_band_limits_and_adds_a_squelch_tail():
    import numpy as np
    from server.voice.voice_design import radio
    rate = 22050
    t = np.arange(rate) / rate
    low, mid = np.sin(2 * np.pi * 100 * t), np.sin(2 * np.pi * 1000 * t)
    out = radio(low * 0.3 + mid * 0.3, rate)
    assert len(out) == rate + int(0.15 * rate) and np.max(np.abs(out)) <= 1
    spectrum = np.abs(np.fft.rfft(out[:rate]))
    freqs = np.fft.rfftfreq(rate, 1 / rate)
    assert spectrum[np.argmin(np.abs(freqs - 1000))] > 50 * spectrum[np.argmin(np.abs(freqs - 100))]



def test_design_reuses_a_voice_already_in_the_library(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from server.voice import voice_design as vd
    spec = vd.SPECS[2]

    class Client:
        def __init__(self):
            self.voices = SimpleNamespace(search=self.search)
            self.text_to_voice = SimpleNamespace(design=self.fail, create=self.fail)

        async def search(self, **kw):
            return SimpleNamespace(voices=[SimpleNamespace(name=f"Sweatbox {spec.slug}", voice_id="lib-1")])

        async def fail(self, **kw):
            raise AssertionError("must not design or create again")

    entries = asyncio.run(vd.design_all(Client(), specs=(spec,), path=tmp_path / "voices.json"))
    assert entries[0]["id"] == "lib-1" and entries[0]["airlines"] == list(spec.airlines)
