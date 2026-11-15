"""Live phraseology coach: feedback on each controller transmission (UK CAP 413 style).

Two layers, both OFF the critical path (they run after the pilot's reply is already scheduled):
  1. ``review()`` - deterministic checks, instant: callsign first, "flight level" vs "altitude"
     around the 6,000 ft transition altitude, QNH with the first clearance to an altitude,
     "heading" / "feet" / "knots" said with the number, runway on approach clearances, frequency
     on transfers, at most three instructions per call, speaking rate, parts not understood.
     Gives a 0-100 score and short notes ("good" / "tip" / "fix").
  2. ``claude_tip()`` - Claude Haiku writes ONE sentence of coaching from the transcript and the
     rule notes, within a short deadline; on any failure there is simply no tip.

Only what the controller said is judged, never the pilot's planned errors, so nothing is given away.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from server.contracts import Transmission
from server.voice.normalise import normalise_tokens
from server.voice.parser.callsign import resolve_callsign

logger = logging.getLogger(__name__)

TRANSITION_ALT_FT = 6000
MAX_INSTRUCTIONS = 3
FAST_WPS = 4.6            # controller words per second that start to sound rushed on the radio
CLAUDE_DEADLINE_S = 4.0

# points off per issue
WEIGHTS = {"fix": 15, "tip": 6}


@dataclass
class Review:
    score: int
    notes: list[dict[str, str]] = field(default_factory=list)    # {"level": good|tip|fix, "text": ...}

    def add(self, level: str, text: str) -> None:
        self.notes.append({"level": level, "text": text})


def _has_word(raw: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", raw, re.I) for w in words)


def review(tx: Transmission, raw: str, *, callsigns: list | None = None, aircraft_alt: Optional[float] = None,
           speech_s: Optional[float] = None) -> Review:
    """Check one controller transmission. ``aircraft_alt`` = the addressed aircraft's altitude."""
    r = Review(score=100)
    tokens = normalise_tokens(raw)
    ins = list(tx.instructions)
    if not ins and tx.intent not in ("instruction", "readback_correction"):
        return r                                                 # "readback correct", "say again": nothing to coach

    # callsign
    if tx.receiver is None:
        r.add("fix", "Start with the callsign, e.g. “Speedbird 117, turn left heading 240”.")
    elif callsigns:
        hit = resolve_callsign(tokens, callsigns)
        if hit is not None and hit.start > 1:
            r.add("tip", "Put the callsign first so the right pilot is listening from the start.")

    for i in ins:
        if i.type == "altitude" and i.value is not None and float(i.value) > TRANSITION_ALT_FT:
            r.add("fix", f"Above the {TRANSITION_ALT_FT:,} ft transition altitude use a flight level: "
                         f"“flight level {int(float(i.value) // 100)}”.")
        if i.type == "flight_level" and i.value is not None and float(i.value) * 100 <= TRANSITION_ALT_FT:
            r.add("fix", f"At or below the transition altitude say “altitude {int(float(i.value) * 100):,} feet”, "
                         "not a flight level.")
        if i.type == "altitude" and i.value is not None and not _has_word(raw, "feet", "ft", "altitude"):
            r.add("tip", "Say “altitude … feet” so it can't be mistaken for a flight level.")
        if i.type == "heading" and "heading" not in tokens:
            r.add("tip", "Include the word “heading”: “turn left heading 240”.")
        if i.type == "speed" and i.value is not None and not _has_word(raw, "knots", "kt", "kts"):
            r.add("tip", "Add “knots” to a speed: “reduce speed 210 knots”.")
        if i.type == "approach_clearance" and not i.runway:
            r.add("fix", "Name the runway in an approach clearance: “cleared ILS approach runway 26L”.")
        if i.type == "contact" and not i.frequency:
            r.add("tip", "Give the frequency when transferring: “contact Gatwick Tower 124.225”.")

    # first clearance from a flight level down to an altitude needs the QNH
    descending_to_alt = any(i.type == "altitude" and i.value is not None and float(i.value) <= TRANSITION_ALT_FT
                            for i in ins)
    if (descending_to_alt and aircraft_alt is not None and aircraft_alt > TRANSITION_ALT_FT + 50
            and not any(i.type == "qnh" for i in ins)):
        r.add("fix", "First clearance down to an altitude: include the QNH, e.g. “… altitude 5,000 feet, QNH 1013”.")

    if len(ins) > MAX_INSTRUCTIONS:
        r.add("tip", f"{len(ins)} instructions in one call: keep it to {MAX_INSTRUCTIONS} or fewer so the readback stays reliable.")
    if "unparsed_clause" in tx.ambiguities:
        r.add("fix", "Part of that wasn't understood. Use standard words: verb, then the value, then the unit.")
    if _has_word(raw, "nine") and not _has_word(raw, "niner"):
        r.add("tip", "Say “niner” for 9 on the radio.")

    words = len(raw.split())
    if speech_s and speech_s > 1.0 and words >= 6 and words / speech_s > FAST_WPS:
        r.add("tip", f"A little fast ({words / speech_s:.1f} words a second). Slow down slightly for clean readbacks.")

    r.score = max(0, 100 - sum(WEIGHTS.get(n["level"], 0) for n in r.notes))
    if not any(n["level"] in ("fix", "tip") for n in r.notes):
        r.add("good", "Clear, standard phraseology.")
    elif not any(n["level"] == "fix" for n in r.notes):
        r.notes.insert(0, {"level": "good", "text": "Nearly there."})
    return r


SYSTEM = (
    "You coach trainee UK approach controllers (Gatwick Director, CAP 413 phraseology). Given one "
    "transmission and automatic notes, reply with ONE short, encouraging sentence (max 25 words) that "
    "gives the single most useful improvement from the notes, quoting the better wording. If the only note "
    "is that it was standard, just say briefly what was good and suggest nothing else. Never invent rules "
    "beyond the notes. Never mention scores. Reply as JSON: {\"tip\": \"...\"}."
)


async def claude_tip(raw: str, rev: Review, *, client: Any = None, deadline_s: float = CLAUDE_DEADLINE_S
                     ) -> Optional[str]:
    """One coaching sentence from Claude Haiku, or None (no key, timeout, bad output)."""
    try:
        from server.agents import llm
        client = client or llm.get_client()
        if client is None:
            return None
        payload = json.dumps({"transmission": raw, "notes": [n["text"] for n in rev.notes]})
        resp = await asyncio.wait_for(client.messages.create(
            model=llm.FAST_MODEL, max_tokens=120, system=SYSTEM,
            messages=[{"role": "user", "content": payload}], **llm.model_kwargs(llm.FAST_MODEL)), deadline_s)
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        m = re.search(r"\{.*\}", text, re.S)
        tip = str(json.loads(m.group(0)).get("tip", "")).strip() if m else ""
        context = raw + " " + " ".join(n["text"] for n in rev.notes)
        if not tip or len(tip) > 260 or (any(c.isdigit() for c in tip) and not _numbers_ok(tip, context)):
            return None
        return tip
    except Exception as error:                                    # noqa: BLE001
        logger.info("coach tip unavailable: %s", error)
        return None


def _numbers_ok(tip: str, raw: str) -> bool:
    """Guard: Claude may quote numbers only if they were in the transmission (or 1013 / 6,000 basics)."""
    allowed = set(re.findall(r"\d+", raw.replace(",", ""))) | {"1013", "6000", "26", "08", "124", "225", "134", "125"}
    return all(n in allowed or n.lstrip("0") in allowed for n in re.findall(r"\d+", tip.replace(",", "")))
