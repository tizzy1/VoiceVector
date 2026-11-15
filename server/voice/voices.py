"""callsign -> voice, deterministic from seed + persona (ASSIGNMENT §5 req. 7).

A fixed pool of eight diverse ElevenLabs premade voices is the MUST tier; ``data/voices.json`` may
override the pool (e.g. with Voice-Design generated personas, the SHOULD tier). Assignment spreads
callsigns across the persona-compatible voices so two aircraft on frequency rarely sound alike.
"""
from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    gender: str
    personas: tuple[str, ...] = ()
    airlines: tuple[str, ...] = ()       # ICAO prefixes whose crews sound like this (accent realism)
    accent: str = ""
    natural_wps: float = 0.0             # measured words/s at speed 1.0 (0 = unknown)
    max_wps: float = 0.0                 # measured words/s at the top speed (1.2)


# Premade voices available on every ElevenLabs plan (verified against the account's /v2/voices).
DEFAULT_POOL: tuple[Voice, ...] = (
    Voice("onwK4e9ZLuTAKqWW03F9", "Daniel", "male", ("crisp_airline", "calm_longhaul"), ("BAW", "TOM"), "British"),
    Voice("Xb7hH8MSUJpSbSDYk0k2", "Alice", "female", ("crisp_airline", "calm_longhaul"), ("EZY", "BAW"), "British"),
    Voice("JBFqnCBsd6RMkjVDRZzb", "George", "male", ("calm_longhaul", "crisp_airline"), ("BAW", "LOG"), "British"),
    Voice("pqHfZKP75CvOlQylNhV4", "Bill", "male", ("calm_longhaul", "crisp_airline"), ("DAL", "WJA"), "American"),
    Voice("IKne3meq5aSn9XLyUdCD", "Charlie", "male", ("rushed", "student", "stressed"), ("UAE", "QTR"), "Australian"),
    Voice("bIHbv24MWmeRgasZH58o", "Will", "male", ("student", "rushed"), ("RYR",), "American"),
    Voice("EXAVITQu4vr4xnSDxMaL", "Sarah", "female", ("student", "stressed", "crisp_airline"), ("VLG", "EZY"), "American"),
    Voice("cjVigY5qzO86Huf0OWal", "Eric", "male", ("rushed", "crisp_airline", "stressed"), ("WZZ", "DLH"), "American"),
)

# ElevenLabs voice_settings per persona. Flash v2.5 honours stability/similarity/style/speed.
PERSONA_SETTINGS: dict[str, dict[str, float]] = {
    "crisp_airline": {"stability": 0.60, "similarity_boost": 0.80, "style": 0.0, "speed": 1.05},
    "calm_longhaul": {"stability": 0.70, "similarity_boost": 0.80, "style": 0.0, "speed": 0.97},
    "student": {"stability": 0.35, "similarity_boost": 0.75, "style": 0.15, "speed": 0.92},
    "rushed": {"stability": 0.45, "similarity_boost": 0.80, "style": 0.1, "speed": 1.15},
    "stressed": {"stability": 0.25, "similarity_boost": 0.75, "style": 0.5, "speed": 1.1},
}
DEFAULT_PERSONA = "crisp_airline"

# Experienced crews on a busy frequency. Real pilot readbacks (UWB-ATCC, 776 pilot transmissions of
# 7+ words): p50 3.23, p75 3.89, p90 4.37 words/s. Students are deliberately slower.
TARGET_WPS = {"student": 2.7, "calm_longhaul": 3.5, "crisp_airline": 3.9, "stressed": 4.1, "rushed": 4.4}
SPEED_RANGE = (0.7, 1.2)                 # ElevenLabs voice_settings.speed limits


def target_wps(persona: str, stressed: bool = False) -> float:
    """The real-pilot pace (words/s) a persona aims for."""
    if stressed:
        return TARGET_WPS["stressed"]
    return TARGET_WPS.get(persona, TARGET_WPS[DEFAULT_PERSONA])


def settings_for(persona: str, stressed: bool = False, voice: "Voice | None" = None) -> dict[str, float]:
    """Voice settings for a persona; with a calibrated voice the speed hits the real-pilot pace."""
    base = dict(PERSONA_SETTINGS.get(persona, PERSONA_SETTINGS[DEFAULT_PERSONA]))
    if stressed:
        base.update(PERSONA_SETTINGS["stressed"])
    if voice is not None and voice.natural_wps > 0:
        target = target_wps(persona, stressed)
        if voice.max_wps > voice.natural_wps:      # two measured points: speed is not linear in pace
            speed = 1.0 + (SPEED_RANGE[1] - 1.0) * (target - voice.natural_wps) / (voice.max_wps - voice.natural_wps)
        else:
            speed = target / voice.natural_wps
        base["speed"] = round(min(max(speed, SPEED_RANGE[0]), SPEED_RANGE[1]), 3)
    return base


@dataclass
class Voices:
    """Deterministic assignment; the same (seed, callsign, persona) always gets the same voice."""
    pool: tuple[Voice, ...] = DEFAULT_POOL
    seed: int = 42
    assigned: dict[str, Voice] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path | str, seed: int = 42) -> "Voices":
        path = Path(data_dir) / "voices.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                pool = tuple(Voice(v["id"], v.get("name", v["id"]), v.get("gender", "unknown"),
                                   tuple(v.get("personas", ())), tuple(v.get("airlines", ())),
                                   v.get("accent", ""), float(v.get("natural_wps") or 0.0),
                                   float(v.get("max_wps") or 0.0))
                             for v in raw.get("voices", []) if v.get("id"))
                if pool:
                    return cls(pool=pool, seed=seed)
            except (OSError, ValueError, KeyError):
                pass
        return cls(seed=seed)

    def voice_for(self, callsign: str, persona: str = DEFAULT_PERSONA) -> Voice:
        known = self.assigned.get(callsign)
        if known is not None:
            return known
        prefix = callsign[:3].upper() if callsign[:3].isalpha() else ""
        airline = [v for v in self.pool if prefix and prefix in v.airlines]
        fits = [v for v in self.pool if persona in v.personas]
        both = [v for v in airline if persona in v.personas]
        # the accent follows the airline (a Ryanair crew sounds Irish); a student sounds like a student
        tiers = [both, fits, airline] if persona == "student" else [both, airline, fits]
        compatible = next((t for t in tiers if t), list(self.pool))
        used = {v.id for v in self.assigned.values()}
        fresh = [v for v in compatible if v.id not in used] or compatible
        index = zlib.crc32(f"{self.seed}|{callsign}|{persona}".encode()) % len(fresh)
        self.assigned[callsign] = fresh[index]
        return fresh[index]

    def to_json(self) -> str:
        return json.dumps({
            "_meta": {"what": "Pilot voice pool (owner: Sanju). Premade ElevenLabs voices; "
                              "replace with generated personas via Voice Design."},
            "voices": [{"id": v.id, "name": v.name, "gender": v.gender, "personas": list(v.personas),
                        "airlines": list(v.airlines), "accent": v.accent} for v in self.pool],
            "persona_settings": PERSONA_SETTINGS,
        }, indent=1) + "\n"


if __name__ == "__main__":
    out: Optional[Path] = Path(__file__).resolve().parents[2] / "data" / "voices.json"
    out.write_text(Voices().to_json(), encoding="utf-8")
    print("wrote", out)
