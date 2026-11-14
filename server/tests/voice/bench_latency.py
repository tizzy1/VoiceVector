"""Latency benchmark against the REAL services (ASSIGNMENT §7; not collected by pytest).

    .venv/bin/python server/tests/voice/bench_latency.py [--n 20]

Controller transmissions are synthesised once with ElevenLabs (16 kHz PCM, cached in runs/bench_audio/,
so re-runs cost nothing) and then streamed into the real pipeline in REAL TIME, 100 ms frames, exactly
like the browser microphone: live Scribe -> parser -> Tudor's World + PilotEngine -> composer ->
ElevenLabs Flash v2.5. Per-stage p50/p90 are printed and saved to runs/bench_latency.json.

Fails (exit 1) if the regex-path machine latency p90 (PTT release -> first pilot audio at the server)
exceeds 1.2 s, the spec target.
"""
import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from server.contracts import SessionSettings  # noqa: E402
from server.pilot.engine import PilotEngine  # noqa: E402
from server.pilot.readback import Composer  # noqa: E402
from server.sim.world import World  # noqa: E402
from server.voice.metrics import percentile  # noqa: E402
from server.voice.parser import parse  # noqa: E402
from server.voice.pipeline import VoicePipeline  # noqa: E402
from server.voice.tts import ElevenLabsTTS  # noqa: E402

CONTROLLER_VOICE = "onwK4e9ZLuTAKqWW03F9"          # Daniel: a steady British broadcaster
SCRIPT = [
    "Speedbird one one seven, turn left heading two four zero, descend altitude four thousand feet",
    "Easy four five alpha bravo, descend flight level seven zero",
    "Lufthansa four kilo alpha, reduce speed two one zero knots",
    "Emirates one five, turn right heading three three zero",
    "Wizz Air four four seven one, descend flight level eight zero",
    "Speedbird one one seven, reduce speed one eight zero knots",
    "Easy four five alpha bravo, turn left heading two seven zero, descend altitude five thousand feet",
    "Lufthansa four kilo alpha, proceed direct Willo",
    "Emirates one five, descend flight level niner zero",
    "Wizz Air four four seven one, turn left heading two niner zero",
    "Speedbird one one seven, turn left heading two six zero, cleared ILS approach runway two six left",
    "Easy four five alpha bravo, QNH one zero one three",
    "Lufthansa four kilo alpha, descend altitude six thousand feet",
    "Emirates one five, speed two two zero knots",
    "Wizz Air four four seven one, squawk four five two one",
    "Speedbird one one seven, contact Gatwick Tower one two four decimal two two five",
    "Easy four five alpha bravo, reduce speed two one zero knots",
    "Lufthansa four kilo alpha, turn right heading zero niner zero",
    "Emirates one five, hold at Timba",
    "Wizz Air four four seven one, descend altitude five thousand feet",
]


class Hub:
    def __init__(self):
        self.messages, self.tasks = [], set()

    def spawn(self, coroutine):
        task = asyncio.get_running_loop().create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def send(self, msg, roles=None):
        self.messages.append((time.monotonic(), msg))


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


async def controller_audio(tts: ElevenLabsTTS, text: str) -> bytes:
    cache = ROOT / "runs" / "bench_audio"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (hashlib.sha1(text.encode()).hexdigest()[:16] + ".pcm")
    if path.exists():
        return path.read_bytes()
    pcm = b"".join([c async for c in tts.stream(text, CONTROLLER_VOICE)])
    path.write_bytes(pcm)
    return pcm


def key(tx) -> tuple:
    return (tx.receiver, tuple((i.type, i.value, i.turn_dir, i.fix, i.runway, i.frequency) for i in tx.instructions))


async def main(n: int) -> int:
    if not os.environ.get("ELEVENLABS_API_KEY"):
        print("ELEVENLABS_API_KEY missing")
        return 2
    synth = ElevenLabsTTS(os.environ["ELEVENLABS_API_KEY"], output_format="pcm_16000", timeout=20)
    bus, hub = Bus(), Hub()
    world = World(bus, ROOT / "data")
    bus.clock = lambda: world.t
    world.load_scenario("demo", 42)
    world.configure(SessionSettings(error_rate="off"))
    pilot = PilotEngine(world, bus, ROOT / "data")
    pilot.set_error_rate("off")
    voice = VoicePipeline(world, pilot, Composer(ROOT / "data"), bus, hub)
    if voice.tts is None or voice.stt_factory is None:
        print("pipeline has no live TTS/STT")
        return 2
    await asyncio.sleep(2.0)                                          # let the warm-ups finish
    replies: dict[str, dict] = {}
    bus.subscribe(lambda e: replies.__setitem__(e.data.get("in_reply_to"), e.data)
                  if e.type == "pilot_transmission" else None)

    rows = []
    for i, text in enumerate(SCRIPT[:n]):
        pcm = await controller_audio(synth, text)
        pcm += b"\x00\x00" * 3200                                   # 200 ms of trailing silence, like a real key-up
        await voice.on_ptt("down")
        tx_id = voice._ptt.tx_id
        start = time.monotonic()
        for k in range(0, len(pcm), 3200):                          # real time: 100 ms per frame
            await voice.on_audio_in(pcm[k:k + 3200])
            await asyncio.sleep(max(0.0, start + (k // 3200 + 1) * 0.1 - time.monotonic()))
        await voice.on_ptt("up")
        deadline = time.monotonic() + 20
        while tx_id not in replies and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        reply = replies.get(tx_id)
        final = next((m for _, m in hub.messages if getattr(m, "type", "") == "caption"
                      and m.stage == "final" and m.tx_id == tx_id and m.speaker == "CTRL"), None)
        heard = final.text if final else ""
        correct = bool(final) and key(parse(heard)) == key(parse(text))
        row = {"said": text, "heard": heard, "correct_parse": correct}
        if reply:
            t = reply["timings"]
            row.update({
                "stt_commit_s": t["t_final"] - t["t_ptt_up"],
                "parse_s": t["t_parsed"] - t["t_final"],
                "tts_first_s": t["t_tts_first"] - t["t_tts_req"],
                "machine_s": t["t_tts_first"] - t["t_ptt_up"],
                "perceived_s": t["t_play"] - t["t_ptt_up"],
                "readback": reply["text"],
            })
        rows.append(row)
        status = f"machine {row['machine_s']:.2f}s perceived {row['perceived_s']:.2f}s" if reply else "NO REPLY"
        print(f"{i + 1:2}. {'OK ' if correct else 'MISS'} {status} | heard: {heard!r}")
        await asyncio.sleep(0.6)
    await synth.aclose()
    await voice.aclose()

    stages = ("stt_commit_s", "parse_s", "tts_first_s", "machine_s", "perceived_s")
    summary = {}
    print(f"\n{'stage':14} {'p50':>7} {'p90':>7}   (n={sum('machine_s' in r for r in rows)})")
    for s in stages:
        vals = [r[s] for r in rows if s in r]
        summary[s] = {"p50": percentile(vals, 50), "p90": percentile(vals, 90), "n": len(vals)}
        if vals:
            print(f"{s:14} {summary[s]['p50']:7.3f} {summary[s]['p90']:7.3f}")
    accuracy = sum(r["correct_parse"] for r in rows) / len(rows)
    print(f"\nSTT -> parse exact match: {accuracy:.0%} ({sum(r['correct_parse'] for r in rows)}/{len(rows)})")
    out = ROOT / "runs" / "bench_latency.json"
    out.write_text(json.dumps({"summary": summary, "accuracy": accuracy, "rows": rows}, indent=1))
    print(f"saved {out}")
    p90 = summary["machine_s"]["p90"]
    return 0 if p90 is not None and p90 <= 1.2 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    raise SystemExit(asyncio.run(main(parser.parse_args().n)))
