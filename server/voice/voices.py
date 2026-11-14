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


# Premade voices available on every ElevenLabs plan (verified against the account's /v2/voices).
DEFAULT_POOL: tuple[Voice, ...] = (
    Voice("onwK4e9ZLuTAKqWW03F9", "Daniel", "male", ("crisp_airline", "calm_longhaul")),
    Voice("Xb7hH8MSUJpSbSDYk0k2", "Alice", "female", ("crisp_airline", "calm_longhaul")),
    Voice("JBFqnCBsd6RMkjVDRZzb", "George", "male", ("calm_longhaul", "crisp_airline")),
    Voice("pqHfZKP75CvOlQylNhV4", "Bill", "male", ("calm_longhaul", "crisp_airline")),
    Voice("IKne3meq5aSn9XLyUdCD", "Charlie", "male", ("rushed", "student", "stressed")),
    Voice("bIHbv24MWmeRgasZH58o", "Will", "male", ("student", "rushed")),
    Voice("EXAVITQu4vr4xnSDxMaL", "Sarah", "female", ("student", "stressed", "crisp_airline")),
    Voice("cjVigY5qzO86Huf0OWal", "Eric", "male", ("rushed", "crisp_airline", "stressed")),
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


def settings_for(persona: str, stressed: bool = False) -> dict[str, float]:
    base = dict(PERSONA_SETTINGS.get(persona, PERSONA_SETTINGS[DEFAULT_PERSONA]))
    if stressed:
        base.update(PERSONA_SETTINGS["stressed"])
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
                                   tuple(v.get("personas", ()))) for v in raw.get("voices", []))
                if pool:
                    return cls(pool=pool, seed=seed)
            except (OSError, ValueError, KeyError):
                pass
        return cls(seed=seed)

    def voice_for(self, callsign: str, persona: str = DEFAULT_PERSONA) -> Voice:
        known = self.assigned.get(callsign)
        if known is not None:
            return known
        compatible = [v for v in self.pool if persona in v.personas] or list(self.pool)
        used = {v.id for v in self.assigned.values()}
        fresh = [v for v in compatible if v.id not in used] or compatible
        index = zlib.crc32(f"{self.seed}|{callsign}|{persona}".encode()) % len(fresh)
        self.assigned[callsign] = fresh[index]
        return fresh[index]

    def to_json(self) -> str:
        return json.dumps({
            "_meta": {"what": "Pilot voice pool (owner: Sanju). Premade ElevenLabs voices; "
                              "replace with generated personas via Voice Design."},
            "voices": [{"id": v.id, "name": v.name, "gender": v.gender, "personas": list(v.personas)}
                       for v in self.pool],
            "persona_settings": PERSONA_SETTINGS,
        }, indent=1) + "\n"


if __name__ == "__main__":
    out: Optional[Path] = Path(__file__).resolve().parents[2] / "data" / "voices.json"
    out.write_text(Voices().to_json(), encoding="utf-8")
    print("wrote", out)
