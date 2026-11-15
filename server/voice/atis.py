"""Spoken ATIS (15 Nov, Sanju): Gatwick's current information, built from the simulator's own
SIMULATED METAR (world.metar()) and runway, read by a fixed ATIS voice through the radio sound.

The same letter the arrivals quote on check-in ("information Kilo") is used, so what the judge hears
matches what the pilots say. ATIS is its own frequency: it never takes the Director frequency.
"""
from __future__ import annotations

from typing import Any

from server.voice import numbers as N
from server.voice.pronounce import ICAO_WORD, respell

ATIS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"     # "George": premade, present in every ElevenLabs account
ATIS_SETTINGS = {"stability": 0.85, "similarity_boost": 0.75, "style": 0.0, "speed": 1.02}
COVER = {"FEW": "few", "SCT": "scattered", "BKN": "broken", "OVC": "overcast"}


def _num(n: int) -> str:
    return N.digits_to_speech(str(int(n)))


def build(metar: dict[str, Any], runway: str, letter: str) -> tuple[str, str]:
    """(display text, speech text) for the current ATIS."""
    letter = (letter or "K").upper()[:1]
    word = ICAO_WORD.get(letter, letter).capitalize()
    time_z = str(metar.get("observed", "")).replace(":", "").replace("Z", "")
    shown = [f"Gatwick information {word}, time {time_z}." if time_z else f"Gatwick information {word}.",
             f"Runway {runway} in use. ILS approach."]
    said = [f"Gatwick information {word}, time {_num(int(time_z))}." if time_z.isdigit() else f"Gatwick information {word}.",
            f"Runway {N.runway_to_speech(runway)} in use. I L S approach."]
    if metar.get("calm") or not metar.get("wind_kt"):
        shown.append("Wind calm."); said.append("Wind calm.")
    else:
        d, kt = int(metar.get("wind_dir_deg") or 0), int(metar["wind_kt"])
        shown.append(f"Wind {d:03d} degrees {kt} knots.")
        said.append(f"Wind {_num(f'{d:03d}')} degrees, {_num(kt)} knots.")
    if metar.get("cavok"):
        shown.append("CAVOK."); said.append("Cav ok.")
    else:
        vis = int(metar.get("visibility_m") or 9999)
        shown.append("Visibility 10 kilometres or more." if vis >= 9999 else f"Visibility {vis} metres.")
        said.append("Visibility one zero kilometres or more." if vis >= 9999 else f"Visibility {_num(vis)} metres.")
        for c in metar.get("cloud") or []:
            cover = COVER.get(str(c.get("cover")), str(c.get("cover", "")).lower())
            base = int(c.get("base_ft") or 0)
            shown.append(f"Cloud {cover} {base:,} feet.")
            said.append(f"Cloud {cover}, {N.altitude_to_speech(base)} feet.")
    if metar.get("temp_c") is not None:
        t, dp = int(metar["temp_c"]), int(metar.get("dewpoint_c") or 0)
        shown.append(f"Temperature {t}, dew point {dp}.")
        said.append(f"Temperature {'minus ' if t < 0 else ''}{_num(abs(t))}, dew point {'minus ' if dp < 0 else ''}{_num(abs(dp))}.")
    q = int(metar.get("qnh_hpa") or 1013)
    shown.append(f"QNH {q}.")
    said.append(f"Q N H {N.qnh_to_speech(q)}.")
    shown.append(f"Acknowledge receipt of information {word} and advise aircraft type on first contact.")
    said.append(f"Acknowledge receipt of information {word}, and advise aircraft type on first contact.")
    return " ".join(shown), respell(" ".join(said))
