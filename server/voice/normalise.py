"""Transcript normaliser (VOICE_PIPELINE §6). Runs on partials and on the final transcript."""
from __future__ import annotations

import re

from server.voice.numbers import DIGIT_WORDS, MULTIPLIERS, NUM_RE, TENS_WORDS

# Multi-word phrases collapsed to the single tokens the grammar keys on.
_PHRASES: dict[tuple[str, ...], tuple[str, ...]] = {
    ("speed", "bird"): ("speedbird",),
    ("easy", "jet"): ("easyjet",),
    ("wizz", "air"): ("wizzair",),
    ("no", "atc", "speed", "restriction"): ("speedcancel",),
    ("no", "speed", "restriction"): ("speedcancel",),
    ("resume", "normal", "speed"): ("speedcancel",),
    ("minimum", "clean", "speed"): ("minclean",),
    ("maintain", "speed"): ("speed",),
    ("slow", "down"): ("slow",),
    ("stop", "descent"): ("stopdesc",),
    ("stop", "descend"): ("stopdesc",),   # "descent" is rewritten to "descend" before phrases
    ("stop", "climb"): ("stopclimb",),
    ("own", "navigation"): ("ownnav",),
    ("present", "heading"): ("presenthdg",),
    ("go", "around"): ("goaround",),
    ("cancel", "approach", "clearance"): ("cancelapp",),
    ("cancel", "approach"): ("cancelapp",),
    ("cleared", "to", "land"): ("clearland",),
    ("cleared", "for", "take", "off"): ("cleartakeoff",),
    ("cleared", "for", "takeoff"): ("cleartakeoff",),
    ("intercept", "the", "localiser"): ("intercept",),
    ("intercept", "the", "localizer"): ("intercept",),
    ("intercept", "localiser"): ("intercept",),
    ("on", "the", "glidepath"): ("glide",),
    ("on", "the", "ils"): ("glide",),
    ("flight", "level"): ("fl",),
    ("per", "minute"): ("perminute",),
    ("read", "back"): ("readback",),
    ("proceed", "direct"): ("direct",),
    ("route", "direct"): ("direct",),
}
_MAX_PHRASE = max(len(p) for p in _PHRASES)

_WORD_FIXES = {
    "descent": "descend", "descending": "descend", "climbing": "climb",
    "localizer": "localiser", "centre": "center",
    # pilot -ing forms, so readbacks re-parse with the controller grammar
    "turning": "turn", "reducing": "reduce", "increasing": "increase", "maintaining": "maintain",
    "expediting": "expedite", "resuming": "resume", "proceeding": "proceed", "holding": "hold",
    "intercepting": "intercept", "squawking": "squawk", "expecting": "expect",
    "cancelling": "cancel", "contacting": "contact",
}
_HOMOPHONES = {"for": "four", "won": "one", "ate": "eight"}


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, keep decimal points inside numbers, drop thousands commas."""
    t = text.lower()
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    t = re.sub(r"[^a-z0-9.]+", " ", t)
    return [w for w in (w.strip(".") for w in t.split()) if w]


def _is_num(tok: str) -> bool:
    return (tok in DIGIT_WORDS or tok in TENS_WORDS or tok in MULTIPLIERS
            or bool(NUM_RE.match(tok)))


def _join_letters(tokens: list[str]) -> list[str]:
    """"q n h" -> "qnh"; "i l s" -> "ils". A lone letter ("i say again") is left alone."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        j = i
        while j < len(tokens) and len(tokens[j]) == 1 and tokens[j].isalpha():
            j += 1
        if j - i >= 2:
            out.append("".join(tokens[i:j]))
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return out


def _collapse_phrases(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        for n in range(min(_MAX_PHRASE, len(tokens) - i), 1, -1):
            repl = _PHRASES.get(tuple(tokens[i:i + n]))
            if repl is not None:
                out.extend(repl)
                i += n
                break
        else:
            out.append(tokens[i])
            i += 1
    return out


def _fix_homophones(tokens: list[str]) -> list[str]:
    """for/won/ate only next to a number word; to/too only right after one."""
    out = []
    for k, t in enumerate(tokens):
        prev = tokens[k - 1] if k else ""
        nxt = tokens[k + 1] if k + 1 < len(tokens) else ""
        if t in _HOMOPHONES and (_is_num(prev) or _is_num(nxt)):
            out.append(_HOMOPHONES[t])
        elif t in ("to", "too") and _is_num(prev):
            out.append("two")
        else:
            out.append(t)
    return out


_GLUED_KEYWORDS = re.compile(r"(qnh|ils|dme)")
_RUNWAY = re.compile(r"^\d{2}[lrc]$")


def _split_glued(tokens: list[str]) -> list[str]:
    """STT sometimes glues a whole transmission: "ez45abqnh1013" -> ez 45 ab qnh 1013.

    Splits embedded keywords and letter/digit boundaries, but keeps runway tokens ("26l") and
    decimals ("124.225"). Callsign matching joins tokens anyway, so "45ab" -> "45 ab" is harmless.
    """
    out: list[str] = []
    for t in tokens:
        glued = any(c.isdigit() for c in t) and not _RUNWAY.match(t) and not NUM_RE.match(t)
        if not glued:                     # plain words ("fails", "details") are never split
            out.append(t)
            continue
        pieces = [p for p in _GLUED_KEYWORDS.split(t) if p]
        for piece in pieces:
            out.extend(p for p in re.findall(r"\d+(?:\.\d+)?|[a-z]+", piece) if p)
    return out


def normalise_tokens(text: str) -> list[str]:
    tokens = tokenize(text)
    tokens = _split_glued(tokens)
    tokens = _join_letters(tokens)
    tokens = [_WORD_FIXES.get(t, t) for t in tokens]
    tokens = _collapse_phrases(tokens)
    return _fix_homophones(tokens)


def normalise(text: str) -> str:
    return " ".join(normalise_tokens(text))
