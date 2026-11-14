"""Manual live check against the real APIs (NOT collected by pytest; spends a few hundred characters).

    .venv/bin/python server/tests/voice/live_check.py

Controller speech is synthesised with ElevenLabs (16 kHz PCM) and streamed into live Scribe exactly
as the browser microphone would be; the transcript is parsed, a pilot readback is composed, spoken
with ElevenLabs Flash v2.5, and a debrief is requested from Claude. Prints per-stage latency.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from server.agents.narrative import Narrative  # noqa: E402
from server.contracts import Finding, Instruction, ReadbackPlan, Report  # noqa: E402
from server.pilot.readback import Composer  # noqa: E402
from server.voice.parser import parse  # noqa: E402
from server.voice.stt_scribe import STATIC_KEYTERMS, ScribeStream, build_keyterms  # noqa: E402
from server.voice.tts import ElevenLabsTTS  # noqa: E402
from server.voice.voices import DEFAULT_POOL, Voices, settings_for  # noqa: E402

PHRASE = "Speedbird one one seven, turn left heading two four zero, descend altitude four thousand feet"


async def main() -> int:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        print("ELEVENLABS_API_KEY missing")
        return 1
    ok = True
    controller_tts = ElevenLabsTTS(key, output_format="pcm_16000", timeout=15)
    voice = DEFAULT_POOL[1]      # a controller-ish voice
    t0 = time.monotonic()
    audio = b"".join([c async for c in controller_tts.stream(PHRASE, voice.id)])
    print(f"1. controller audio: {len(audio)/32000:.1f}s of speech synthesised in {time.monotonic()-t0:.2f}s")

    partials: list[str] = []
    terms = build_keyterms(["Speedbird", "Easy", "Lufthansa", "Emirates", "Wizz Air"], STATIC_KEYTERMS, ["WILLO", "TIMBA"])
    stream = ScribeStream(key, terms, partials.append, connect_timeout=8, commit_timeout=8)
    stream.start()
    for i in range(0, len(audio), 3200):          # 100 ms frames, as the browser sends them
        stream.feed(audio[i:i + 3200])
    t1 = time.monotonic()
    text = await stream.finish()
    print(f"2. Scribe: {text!r}  (commit -> final {time.monotonic()-t1:.2f}s, {len(partials)} partials)")

    tx = parse(text)
    got = [(i.type, i.value, i.turn_dir) for i in tx.instructions]
    print(f"3. parsed: receiver={tx.receiver} intent={tx.intent} {got}")
    ok &= tx.receiver == "BAW117" and ("heading", 240.0, "L") in got and ("altitude", 4000.0, None) in got

    composer = Composer(ROOT / "data")
    plan = ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="full", seed=7,
                        elements=[i for i in tx.instructions if i.type in ("heading", "altitude")])
    readback = composer.compose(plan)
    print(f"4. readback: {readback.text!r} validated={readback.validated} fallback={readback.fallback_used}")
    ok &= readback.validated and not readback.fallback_used

    pilot_tts = ElevenLabsTTS(key, timeout=10)
    v = Voices(seed=42).voice_for("BAW117", "crisp_airline")
    t2 = time.monotonic()
    first, total = None, 0
    async for chunk in pilot_tts.stream(readback.tts_text, v.id, settings_for("crisp_airline")):
        first = first or time.monotonic() - t2
        total += len(chunk)
    print(f"5. pilot voice {v.name}: first audio {first:.2f}s, {total/(2*pilot_tts.sample_rate):.1f}s of speech")
    t3 = time.monotonic()
    _ = [c async for c in pilot_tts.stream(readback.tts_text, v.id, settings_for("crisp_airline"))]
    print(f"   repeat (cache): {time.monotonic()-t3:.3f}s")
    ok &= first is not None and total > 10000

    if os.environ.get("ANTHROPIC_API_KEY"):
        report = Report(session_id="live", duration_s=600.0, score=80, score_breakdown={"safety": -5},
                        findings=[Finding(id="F1", rule_id="D1", category="D_READBACK", severity="POSITIVE", t=120.0,
                                          actor="CONTROLLER", callsigns=["BAW117"], title="Readback error caught",
                                          detail="Altitude corrected", correct_practice="")],
                        errors=[], stats={"landings": 3}, timeline=[])
        t4 = time.monotonic()
        result = await Narrative().write(report)
        print(f"6. debrief ({time.monotonic()-t4:.1f}s): {result.narrative!r}")
        print(f"   coaching: {result.coaching}")
    await controller_tts.aclose()
    await pilot_tts.aclose()
    print("\nLIVE CHECK", "PASSED" if ok else "FAILED")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
