"""Generated pilot voices with ElevenLabs Voice Design (ASSIGNMENT §5 req. 12).

    .venv/bin/python -m server.voice.voice_design            # design missing voices -> data/voices.json
    .venv/bin/python -m server.voice.voice_design --audition # runs/voice_audition.wav, through the radio

Each spec is a realistic Gatwick crew: an accent matched to the airlines that fly there, an age, and a
cockpit delivery (clear, efficient, slightly clipped radiotelephony). Voices are designed CLEAN: the
browser's radio chain adds the band-pass, hiss and squelch, so baking radio into the voice would
double it. Voice Design returns several previews per spec; nobody listens during the build, so each
preview is transcribed with Scribe and the most intelligible one (pace as the tie-break) is saved to
the account's voice library. Re-running only designs what is missing.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import sys
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
from rapidfuzz import fuzz

from server.voice.normalise import normalise_tokens

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
VOICES_JSON = ROOT / "data" / "voices.json"
# measured 14 Nov: eleven_ttv_v3 designs 3 previews in ~8 s; eleven_multilingual_ttv_v2 hung past 240 s
DESIGN_MODEL = "eleven_ttv_v3"
DESIGN_ATTEMPTS = 2
NAME_PREFIX = "Sweatbox"

DELIVERY = ("An airline pilot talking to air traffic control on the aircraft radio: clear, efficient "
            "radiotelephony with a steady professional cadence, slightly clipped, brisk but unhurried, "
            "flat intonation, no theatrics. Close headset microphone, dry clean recording, no background "
            "noise and no reverb.")


@dataclass(frozen=True)
class Spec:
    slug: str
    who: str                 # age, gender, accent, temperament
    gender: str
    accent: str
    airlines: tuple[str, ...]
    personas: tuple[str, ...]
    callsign_spoken: str     # used in the sample text, so the voice is designed saying its own callsign
    pace_wps: float = 3.2    # target words per second

    @property
    def description(self) -> str:
        return f"{self.who}. {DELIVERY}"

    @property
    def sample(self) -> str:
        cs = self.callsign_spoken
        return (f"Gatwick Director, good afternoon, {cs}, descending flight level eight zero, information "
                f"kilo. Left heading two four zero, descend to altitude four thousand feet, {cs}.")

    @property
    def seed(self) -> int:
        return zlib.crc32(self.slug.encode()) % 100000


SPECS: tuple[Spec, ...] = (
    Spec("uk-captain", "A calm, authoritative British male airline captain in his fifties, neutral southern "
         "English accent, low warm voice", "male", "British", ("BAW", "TOM", "EZY"),
         ("crisp_airline", "calm_longhaul"), "Speedbird one one seven"),
    Spec("uk-fo", "A confident British female first officer in her early thirties, crisp southern English "
         "accent", "female", "British", ("BAW", "EZY", "TOM", "AUR"),
         ("crisp_airline", "rushed", "calm_longhaul"), "Easy four five alpha bravo"),
    Spec("north-fo", "A friendly British male first officer in his thirties with a Manchester accent, quick "
         "and matter-of-fact", "male", "Northern English", ("EZY", "EXS", "TOM"),
         ("rushed", "crisp_airline", "stressed"), "Channex two two one", 3.5),
    Spec("scottish-captain", "A relaxed Scottish male captain in his mid forties with a soft Edinburgh "
         "accent", "male", "Scottish", ("LOG", "EZY", "BAW"), ("calm_longhaul", "crisp_airline"),
         "Logan four one", 3.0),
    Spec("irish-captain", "A brisk Irish male captain in his forties with a Dublin accent, fast and "
         "economical with words", "male", "Irish", ("RYR", "EZY"), ("rushed", "crisp_airline"),
         "Ryanair four two kilo papa", 3.6),
    Spec("german-captain", "A precise German male captain in his fifties speaking English with a clear "
         "German accent, measured and exact", "male", "German", ("DLH", "SWR", "EZS"),
         ("crisp_airline", "calm_longhaul"), "Lufthansa four kilo alpha", 3.0),
    Spec("spanish-fo", "A Spanish female first officer in her thirties speaking English with a noticeable "
         "Castilian Spanish accent, quick and clear", "female", "Spanish", ("VLG", "IBE", "AEA", "TAP"),
         ("crisp_airline", "rushed"), "Vueling seven two three", 3.4),
    Spec("hungarian-fo", "A young Hungarian male first officer in his late twenties speaking English with a "
         "Central European accent, fast and slightly flat", "male", "Hungarian", ("WZZ", "WUK"),
         ("rushed", "crisp_airline"), "Wizz Air four four seven one", 3.5),
    Spec("dutch-captain", "A composed Dutch female captain in her forties speaking English with a light Dutch "
         "accent, direct and efficient", "female", "Dutch", ("KLM", "NAX"), ("crisp_airline", "calm_longhaul"),
         "KLM one zero three", 3.2),
    Spec("french-fo", "A French male first officer in his thirties speaking English with a French accent, "
         "smooth and quick", "male", "French", ("AFR", "EJU"), ("crisp_airline", "rushed"),
         "Airfrance one two eight one", 3.3),
    Spec("aus-captain", "An Australian male long-haul captain in his forties, relaxed Australian accent, "
         "unhurried and confident", "male", "Australian", ("UAE", "QTR"), ("calm_longhaul", "crisp_airline"),
         "Emirates one five", 2.9),
    Spec("turkish-captain", "A Turkish male captain in his forties speaking English with a Turkish accent, "
         "calm and deliberate", "male", "Turkish", ("THY", "PGT", "QTR"), ("calm_longhaul", "crisp_airline"),
         "Turkish one niner seven", 2.9),
    Spec("student", "A young British female student pilot in her early twenties, slightly hesitant and a "
         "little nervous, careful and slower than an airline pilot", "female", "British", (),
         ("student", "stressed"), "Golf bravo charlie delta echo", 2.5),
    Spec("us-captain", "An American male airline captain in his fifties, general American accent, the "
         "classic calm laid-back airline pilot drawl", "male", "American", ("DAL", "WJA"),
         ("calm_longhaul",), "Delta one two", 2.8),
)


def pcm16_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def score(spec: Spec, heard: str, seconds: float) -> float:
    """Intelligibility (0-100, transcript vs script) minus a penalty for an off-target pace."""
    said = " ".join(normalise_tokens(spec.sample))
    got = " ".join(normalise_tokens(heard))
    words = len(spec.sample.split())
    pace = words / seconds if seconds > 0 else 0.0
    return fuzz.ratio(said, got) - 15 * abs(pace - spec.pace_wps) / spec.pace_wps


def _audio(preview: Any) -> bytes:
    return base64.b64decode(getattr(preview, "audio_base_64", "") or "")


async def _transcribe(client: Any, pcm16k: bytes) -> str:
    result = await client.speech_to_text.convert(model_id="scribe_v1", file=("preview.pcm", pcm16k),
                                                 file_format="pcm_s16le_16", language_code="en")
    return str(getattr(result, "text", "") or "")


async def design_one(client: Any, spec: Spec) -> dict:
    """Design previews, keep the clearest, save it. Returns the voices.json entry (+ 'audio' for audition)."""
    response, last_error = None, None
    for attempt in range(DESIGN_ATTEMPTS):
        try:
            response = await asyncio.wait_for(client.text_to_voice.design(
                voice_description=spec.description, text=spec.sample, model_id=DESIGN_MODEL,
                output_format="pcm_16000", seed=spec.seed + attempt), 60)
            break
        except Exception as error:                                   # noqa: BLE001 - one retry, new seed
            last_error = error
            logger.warning("voice design attempt %d failed for %s: %r", attempt + 1, spec.slug, error)
    if response is None or not response.previews:
        raise RuntimeError(f"no previews for {spec.slug}: {last_error}")
    ranked = []
    for preview in response.previews:
        pcm = _audio(preview)
        seconds = len(pcm) / 32000 if pcm else float(getattr(preview, "duration_secs", 0) or 0)
        heard = await _transcribe(client, pcm) if pcm else ""
        ranked.append((score(spec, heard, seconds), preview, heard, pcm))
    ranked.sort(key=lambda r: r[0], reverse=True)
    best_score, best, heard, pcm = ranked[0]
    voice = await client.text_to_voice.create(
        voice_name=f"{NAME_PREFIX} {spec.slug}", voice_description=spec.description[:1000],
        generated_voice_id=best.generated_voice_id,
        labels={"accent": spec.accent, "gender": spec.gender, "use_case": "sweatbox-atc"},
        played_not_selected_voice_ids=[r[1].generated_voice_id for r in ranked[1:]])
    logger.info("designed %s -> %s (score %.1f, heard %r)", spec.slug, voice.voice_id, best_score, heard)
    return entry_for(spec, voice.voice_id, score=round(best_score, 1), previews=len(ranked), audio=pcm)


def write_pool(entries: list[dict], path: Path = VOICES_JSON) -> None:
    from server.voice.voices import PERSONA_SETTINGS
    payload = {
        "_meta": {"what": "Pilot voice pool (owner: Sanju). Designed with ElevenLabs Voice Design: accents "
                          "matched to Gatwick airlines, cockpit radiotelephony delivery, clean (the browser adds "
                          "the radio). Each kept preview was the most intelligible to Scribe.",
                  "regenerate": "python -m server.voice.voice_design (only missing voices are designed)"},
        "voices": [{k: v for k, v in e.items() if k != "audio"} for e in entries],
        "persona_settings": PERSONA_SETTINGS,
    }
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def entry_for(spec: Spec, voice_id: str, **extra: Any) -> dict:
    return {"id": voice_id, "name": f"{NAME_PREFIX} {spec.slug}", "slug": spec.slug, "gender": spec.gender,
            "accent": spec.accent, "airlines": list(spec.airlines), "personas": list(spec.personas),
            "designed": True, **extra}


async def library(client: Any) -> dict[str, str]:
    """Sweatbox voices already saved in the account (name -> voice id), so nothing is designed twice."""
    try:
        found = await client.voices.search(search=NAME_PREFIX, page_size=100)
    except Exception:                                                 # noqa: BLE001
        logger.warning("could not list the voice library; relying on voices.json only")
        return {}
    return {v.name: v.voice_id for v in getattr(found, "voices", []) or [] if v.name.startswith(NAME_PREFIX)}


def existing(path: Path = VOICES_JSON) -> dict[str, dict]:
    try:
        return {v["slug"]: v for v in json.loads(path.read_text()).get("voices", []) if v.get("slug") and v.get("id")}
    except (OSError, ValueError):
        return {}


async def design_all(client: Any, specs: tuple[Spec, ...] = SPECS, path: Path = VOICES_JSON,
                     concurrency: int = 1) -> list[dict]:   # parallel design requests stalled the service
    have = existing(path)
    saved = await library(client)
    gate = asyncio.Semaphore(concurrency)
    done: dict[str, dict] = {}

    def checkpoint() -> None:                    # saved after every voice: a stopped run loses nothing
        write_pool([done[s.slug] for s in specs if s.slug in done], path)

    async def run(spec: Spec) -> None:
        name = f"{NAME_PREFIX} {spec.slug}"
        if spec.slug in have:
            done[spec.slug] = have[spec.slug]
        elif name in saved:                      # designed earlier (e.g. an interrupted run): reuse it
            done[spec.slug] = entry_for(spec, saved[name])
            logger.info("reusing %s from the voice library", name)
        else:
            async with gate:
                try:
                    done[spec.slug] = await design_one(client, spec)
                except Exception:                                     # noqa: BLE001 - keep the others
                    logger.exception("could not design %s", spec.slug)
                    return
        checkpoint()

    await asyncio.gather(*(run(s) for s in specs))
    checkpoint()
    return [done[s.slug] for s in specs if s.slug in done]


# --------------------------------------------------------------------- audition
def radio(samples: np.ndarray, rate: int, seed: int = 1) -> np.ndarray:
    """Offline copy of the browser radio chain: 300-3000 Hz band, tanh drive, hiss, squelch tail."""
    x = samples.astype(np.float64)
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    spectrum[(freqs < 300) | (freqs > 3000)] = 0
    x = np.fft.irfft(spectrum, len(x))
    peak = np.max(np.abs(x)) or 1.0
    x = np.tanh(2.2 * x / peak) / np.tanh(2.2)
    rng = np.random.default_rng(seed)
    hiss = rng.uniform(-1, 1, len(x)) * 0.012
    tail_n = int(0.15 * rate)
    tail = rng.uniform(-1, 1, tail_n) * np.linspace(0.22, 0, tail_n)
    out = np.concatenate([x + hiss, tail])
    return np.clip(out * 0.9, -1, 1)


READBACKS = ("Left heading two four zero, descend to altitude four thousand feet",
             "Descend to flight level seven zero, speed two one zero knots",
             "Cleared I L S approach runway two six left")


async def audition(tts: Any, entries: list[dict], out: Path) -> Path:
    """Every pilot voice reading its own readback through the radio, 0.6 s apart, as one WAV.

    ``tts`` is the game's ElevenLabsTTS (Flash v2.5), so the audition sounds exactly like the app.
    """
    from server.voice.voices import settings_for
    rate = tts.sample_rate
    gap = np.zeros(int(0.6 * rate))
    parts = []
    for i, entry in enumerate(entries):
        spec = next((s for s in SPECS if s.slug == entry.get("slug")), None)
        callsign = spec.callsign_spoken if spec else "Gatwick"
        prefix = "Uh, " if entry.get("slug") == "student" else ""
        text = f"{prefix}{READBACKS[i % len(READBACKS)]}, {callsign}"
        persona = (entry.get("personas") or ["crisp_airline"])[0]
        pcm = b"".join([c async for c in tts.stream(text, entry["id"], settings_for(persona))])
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(np.float64) / 32768
        parts += [radio(samples, rate, seed=i + 1), gap]
    mix = np.concatenate(parts) if parts else np.zeros(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pcm16_to_wav((mix * 32767).astype("<i2").tobytes(), rate))
    return out


async def main(argv: list[str]) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        print("ELEVENLABS_API_KEY missing")
        return 2
    from elevenlabs.client import AsyncElevenLabs
    client = AsyncElevenLabs(api_key=key, timeout=90)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    entries = await design_all(client)
    print(f"{len(entries)} designed voices in {VOICES_JSON}")
    for e in entries:
        print(f"  {e['slug']:18} {e['accent']:17} {','.join(e['airlines']) or '(any)':22} score {e.get('score')}")
    if "--audition" in argv:
        from server.voice.tts import ElevenLabsTTS
        tts = ElevenLabsTTS(key, timeout=20)
        out = await audition(tts, entries, ROOT / "runs" / "voice_audition.wav")
        await tts.aclose()
        print(f"audition: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
