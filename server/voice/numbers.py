"""Spoken <-> digit conversion shared by the parser and the composer (VOICE_PIPELINE §6, §11)."""
from __future__ import annotations

import re

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "tree": "3", "three": "3",
    "four": "4", "fower": "4", "five": "5", "fife": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "niner": "9",
}
TENS_WORDS = {
    "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90",
}
MULTIPLIERS = {"thousand": 1000, "hundred": 100}
DECIMAL_WORDS = {"decimal", "point"}
NUM_RE = re.compile(r"^\d+(\.\d+)?$")

# What the TTS says for each digit. "niner" per ICAO; never digits (TTS must not see them).
SPEECH_DIGIT = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "niner"]
RUNWAY_SIDE_SPEECH = {"L": "left", "R": "right", "C": "centre"}


def is_number_token(tok: str) -> bool:
    return tok in DIGIT_WORDS or tok in TENS_WORDS or tok in MULTIPLIERS or bool(NUM_RE.match(tok))


def parse_run(tokens: list[str], start: int) -> tuple[int, str, int] | None:
    """Parse a run of number words / digit tokens beginning at ``start``.

    Returns ``(value, digits, end)`` or None when ``tokens[start]`` is not a number.
    ``value`` honours thousand/hundred ("three thousand five hundred" -> 3500; "two four zero"
    -> 240). ``digits`` is the plain digit concatenation, keeping leading zeros ("zero four two
    one" -> "0421") for squawks and runways. A token containing a decimal point ends the run.
    """
    i = start
    total = 0
    cur = ""
    digits = ""
    while i < len(tokens):
        t = tokens[i]
        if t in MULTIPLIERS:
            total += int(cur or "1") * MULTIPLIERS[t]
            cur = ""
            i += 1
            continue
        piece = DIGIT_WORDS.get(t) or TENS_WORDS.get(t)
        if piece is None and NUM_RE.match(t) and "." not in t:
            piece = t
        if piece is None:
            break
        cur += piece
        digits += piece
        i += 1
    if i == start:
        return None
    return total + (int(cur) if cur else 0), digits, i


def parse_frequency(tokens: list[str], start: int) -> tuple[str, int] | None:
    """Parse "one two four decimal two two five" / "124.225" -> ("124.225", end)."""
    s = ""
    seen_dec = False
    i = start
    while i < len(tokens):
        t = tokens[i]
        if t in DECIMAL_WORDS:
            if seen_dec or not s:
                break
            s += "."
            seen_dec = True
        elif NUM_RE.match(t):
            s += t
        elif t in DIGIT_WORDS:
            s += DIGIT_WORDS[t]
        else:
            break
        i += 1
    if "." not in s or s.endswith(".") or s.startswith("."):
        return None
    return s, i


# --------------------------------------------------------------------------------------
# digits -> speech (composer / TTS side)
# --------------------------------------------------------------------------------------
def digits_to_speech(s: str) -> str:
    """"240" -> "two four zero"; "119.6" -> "one one niner decimal six"."""
    return " ".join("decimal" if c == "." else SPEECH_DIGIT[int(c)] for c in s if c == "." or c.isdigit())


def heading_to_speech(deg: float | int) -> str:
    return digits_to_speech(f"{int(deg) % 360 or 360:03d}")


def altitude_to_speech(feet: int) -> str:
    """4000 -> "four thousand"; 3500 -> "three thousand five hundred"."""
    feet = int(feet)
    thousands, rest = divmod(feet, 1000)
    parts = []
    if thousands:
        parts.append(f"{digits_to_speech(str(thousands))} thousand")
    if rest:
        hundreds, below = divmod(rest, 100)
        if below == 0:
            parts.append(f"{digits_to_speech(str(hundreds))} hundred")
        else:
            parts.append(digits_to_speech(str(rest)))
    return " ".join(parts) or "zero"


def flight_level_to_speech(fl: int) -> str:
    return f"flight level {digits_to_speech(str(int(fl)))}"


def speed_to_speech(kt: int) -> str:
    return digits_to_speech(str(int(kt)))


def runway_to_speech(rwy: str) -> str:
    """"26L" -> "two six left"."""
    rwy = rwy.upper()
    side = RUNWAY_SIDE_SPEECH.get(rwy[-1]) if rwy[-1:] in RUNWAY_SIDE_SPEECH else None
    num = rwy[:-1] if side else rwy
    return f"{digits_to_speech(num)} {side}" if side else digits_to_speech(num)


def frequency_to_speech(freq: str) -> str:
    return digits_to_speech(freq)


def squawk_to_speech(code: str) -> str:
    return digits_to_speech(code)


def qnh_to_speech(hpa: int) -> str:
    return digits_to_speech(str(int(hpa)))


def speech_to_number(text: str) -> int | None:
    """Inverse of the *_to_speech helpers for round-trip tests."""
    run = parse_run(text.lower().split(), 0)
    return run[0] if run else None
