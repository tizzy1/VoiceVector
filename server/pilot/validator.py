"""Readback validator: the gate that guarantees zero unplanned errors (VOICE_PIPELINE §11).

A composed readback is accepted only if
  1. the speech text contains no digits (TTS must never see them);
  2. every element phrase, re-parsed with the controller grammar, equals the plan's element
     (value, turn direction, runway, fix, approach type, unit, frequency, constraint) — planned
     errors must therefore be present and nothing else may differ;
  3. the spoken callsign decodes back to exactly ``plan.callsign``;
  4. the multiset of number words and left/right words in the whole text equals what the slots
     put there — so a stray literal, a dropped slot or a double fill is caught.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from server.contracts import Instruction
from server.voice.normalise import normalise_tokens
from server.voice.parser.spelled import join_spelled_points
from server.voice.numbers import (DECIMAL_WORDS, DIGIT_WORDS, MULTIPLIERS, NUM_RE, TENS_WORDS)
from server.voice.parser import default_fixes
from server.voice.parser.callsign import PHONETIC
from server.voice.parser.grammar import Clause, classify

FAMILY = {
    "heading": "heading", "turn_by": "heading", "present_heading": "heading",
    "altitude": "level", "flight_level": "level", "maintain_level": "level",
    "vertical_rate": "level", "descend_glidepath": "level",
    "speed": "speed", "speed_cancel": "speed", "speed_min_clean": "speed",
    "expedite": "expedite", "direct": "direct", "hold": "hold", "resume_nav": "resume",
    "approach_clearance": "cleared", "intercept_localiser": "intercept",
    "cancel_approach": "cancelapp", "go_around": "goaround", "contact": "contact",
    "squawk": "squawk", "qnh": "qnh", "expect": "expect", "report": "report",
    "confirm": "confirm", "traffic_info": "traffic", "cleared_to_land": "clearland",
    "cleared_takeoff": "cleartakeoff",
}
LEVEL_TYPES = {"altitude", "flight_level", "maintain_level"}
COMPARED = ("turn_dir", "runway", "fix", "approach_type", "unit", "frequency", "constraint",
            "report_what")


@dataclass
class Piece:
    """One spoken piece of a readback with what it is allowed to contain."""
    role: str                                   # element | callsign | filler | reply
    speech: str
    element: Optional[Instruction] = None
    slot_speeches: list[str] = field(default_factory=list)


def is_numberish(tok: str) -> bool:
    return (tok in DIGIT_WORDS or tok in TENS_WORDS or tok in MULTIPLIERS
            or tok in DECIMAL_WORDS or bool(NUM_RE.match(tok)))


def _counts(tokens: list[str]) -> tuple[Counter, Counter]:
    nums = Counter(t for t in tokens if is_numberish(t))
    dirs = Counter(t for t in tokens if t in ("left", "right"))
    return nums, dirs


def _is_fl(i: Instruction) -> bool:
    return i.type == "flight_level" or (i.type == "maintain_level" and i.value is not None
                                        and float(i.value) < 1000)


def _same_value(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def element_matches(plan: Instruction, got: Instruction) -> bool:
    if plan.type in LEVEL_TYPES and got.type in LEVEL_TYPES:
        if _is_fl(plan) != _is_fl(got) or not _same_value(plan.value, got.value):
            return False
    elif plan.type != got.type or not _same_value(plan.value, got.value):
        return False
    for name in COMPARED:
        if getattr(plan, name) != getattr(got, name):
            return False
    want = None if (plan.expect_what or "").lower() == "vectors" else plan.expect_what
    return want == got.expect_what


def parse_element(plan: Instruction, speech: str) -> list[Instruction]:
    fam = FAMILY.get(plan.type)
    if fam is None:
        return []
    fixes = set(default_fixes())
    tokens = join_spelled_points(normalise_tokens(speech), fixes)     # "kilo kilo sierra two five" -> kks25
    return classify(Clause(fam, tokens), fixes, None)


def check_element(plan: Instruction, speech: str) -> list[str]:
    parsed = parse_element(plan, speech)
    group = LEVEL_TYPES if plan.type in LEVEL_TYPES else {plan.type}
    for got in parsed:
        if got.type in group and element_matches(plan, got):
            return []
    return [f"element {plan.type} {plan.value!r} not reproduced by '{speech}' "
            f"(parsed {[(i.type, i.value, i.turn_dir) for i in parsed]})"]


# --------------------------------------------------------------------------------------
# callsign decoding (independent of the composer's speech generator)
# --------------------------------------------------------------------------------------
_TELE_CACHE: dict[Path, dict[str, str]] = {}


def telephony_map(path: Path) -> dict[str, str]:
    """telephony words (spoken, lowercase) -> ICAO 3-letter designator."""
    if path not in _TELE_CACHE:
        data = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, str] = {}
        for icao, name in data.items():
            if icao.startswith("_") or not isinstance(name, str):
                continue
            out[name.lower()] = icao
            out[name.lower().replace(" ", "")] = icao   # the normaliser joins "wizz air" -> "wizzair"
        _TELE_CACHE[path] = out
    return _TELE_CACHE[path]


def spoken_to_callsign(speech: str, tele: dict[str, str]) -> Optional[str]:
    tokens = normalise_tokens(speech)
    for n in range(min(3, len(tokens)), 0, -1):
        words = " ".join(tokens[:n])
        icao = tele.get(words) or tele.get(words.replace(" ", ""))
        if icao:
            rest = tokens[n:]
            break
    else:
        icao, rest = "", tokens
    out = []
    for t in rest:
        if t in PHONETIC:
            out.append(PHONETIC[t].upper())
        elif t in DIGIT_WORDS:
            out.append(DIGIT_WORDS[t])
        elif NUM_RE.match(t) or t.isalnum():
            out.append(t.upper())
        else:
            return None
    return icao + "".join(out) if (icao or out) else None


def validate(callsign: str, pieces: list[Piece], speech: str, tele: dict[str, str],
             expected_callsigns: int) -> list[str]:
    problems: list[str] = []
    if any(c.isdigit() for c in speech):
        problems.append("digits in speech text")
    tokens = normalise_tokens(speech)
    got_nums, got_dirs = _counts(tokens)
    want_nums: Counter = Counter()
    want_dirs: Counter = Counter()
    seen_callsigns = 0
    for piece in pieces:
        for slot in piece.slot_speeches:
            n, d = _counts(normalise_tokens(slot))
            want_nums.update(n)
            want_dirs.update(d)
        if piece.role == "element" and piece.element is not None:
            problems += check_element(piece.element, piece.speech)
        elif piece.role == "callsign":
            seen_callsigns += 1
            if spoken_to_callsign(piece.speech, tele) != callsign:
                problems.append(f"callsign '{piece.speech}' does not decode to {callsign}")
    if seen_callsigns != expected_callsigns:
        problems.append(f"callsign spoken {seen_callsigns}x, expected {expected_callsigns}x")
    if got_nums != want_nums:
        problems.append(f"number words differ: said {dict(got_nums)} planned {dict(want_nums)}")
    if got_dirs != want_dirs:
        problems.append(f"left/right differ: said {dict(got_dirs)} planned {dict(want_dirs)}")
    return problems
