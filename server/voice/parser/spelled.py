"""Spelled RNAV/DME points: "kilo kilo sierra two five" -> "kks25" (SIM_SPEC §2.1.1 item 2).

Runs on the tokens after the callsign is removed. A run of phonetic letters and digits (also STT
forms: "kks 25", "k k s 2 5", "25") is joined only if it spells a KNOWN point containing a digit,
longest match first, so ordinary numbers ("heading two five zero") are never touched.
"""
from __future__ import annotations

from server.voice.numbers import DIGIT_WORDS
from server.voice.parser.callsign import PHONETIC

MAX_TOKENS = 8


def _piece(tok: str) -> str | None:
    """The characters one token contributes to a spelled point, or None if it cannot be part of one."""
    if tok in PHONETIC:
        return PHONETIC[tok]
    if tok in DIGIT_WORDS:
        return DIGIT_WORDS[tok]
    if tok.isdigit() and len(tok) <= 3:
        return tok
    if tok.isalpha() and 1 <= len(tok) <= 3:          # STT letter clusters: "kks", "det"
        return tok
    return None


def join_spelled_points(tokens: list[str], fixes: set[str]) -> list[str]:
    points = {f for f in fixes if any(c.isdigit() for c in f) and any(c.isalpha() for c in f)}
    if not points:
        return tokens
    out: list[str] = []
    i = 0
    while i < len(tokens):
        best = None
        acc = ""
        for j in range(i, min(i + MAX_TOKENS, len(tokens))):
            piece = _piece(tokens[j])
            if piece is None:
                break
            acc += piece
            if acc.upper() in points:
                best = (j, acc)
        if best:
            out.append(best[1].lower())
            i = best[0] + 1
        else:
            out.append(tokens[i])
            i += 1
    return out
