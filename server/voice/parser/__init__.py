"""Regex/grammar parser: transcript text -> ``Transmission`` (no network, ~ms)."""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

from server.contracts import CallsignInfo, Transmission
from server.voice.normalise import normalise_tokens
from server.voice.parser.callsign import DEMO_CALLSIGNS, RESOLVED, resolve_callsign
from server.voice.parser.grammar import build_clauses, build_instructions, out_of_range
from server.voice.parser.intent import detect_intent

DATA = Path(__file__).resolve().parents[3] / "data"


@lru_cache(maxsize=1)
def _egkk() -> dict:
    return json.loads((DATA / "egkk.json").read_text())


def default_fixes() -> frozenset[str]:
    return frozenset(_egkk()["fixes"])


def default_runways() -> list[str]:
    return sorted(_egkk()["runways"])


def parse(text: str, callsigns: Optional[list[CallsignInfo]] = None,
          fixes: Optional[set[str]] = None, source: str = "voice",
          tx_id: str = "tx", t: float = 0.0) -> Transmission:
    started = time.perf_counter()
    callsigns = DEMO_CALLSIGNS if callsigns is None else callsigns
    fixes = set(default_fixes()) if fixes is None else fixes

    tokens = normalise_tokens(text)
    ambiguities: list[str] = []
    receiver, confidence, rest = None, 0.0, tokens
    if not ("all" in tokens and "stations" in tokens):
        hit = resolve_callsign(tokens, callsigns)
        if hit:
            receiver, confidence = hit.info.icao, hit.ratio
            rest = tokens[:hit.start] + tokens[hit.end:]
            if hit.ratio < RESOLVED:
                ambiguities.append("callsign_low_conf")

    clauses = build_clauses(rest, fixes)
    instructions, corrections, unparsed = build_instructions(clauses, fixes)
    intent = detect_intent(rest, [c.family for c in clauses], bool(instructions))
    if receiver is None and instructions and intent != "broadcast":
        ambiguities.append("callsign_unresolved")
    if unparsed:
        ambiguities.append("unparsed_clause")
    if any(out_of_range(i) for i in instructions):
        ambiguities.append("out_of_range")
    if any(i.fix and i.confidence < 1.0 for i in instructions):
        ambiguities.append("fix_fuzzy_match")
    return Transmission(
        tx_id=tx_id, t=t, receiver=receiver, receiver_confidence=round(confidence, 3),
        intent=intent, instructions=instructions, self_corrections=corrections,
        raw_text=text, norm_text=" ".join(tokens),
        source=source if source in ("voice", "typed") else "voice",
        ambiguities=ambiguities, parse_path="regex",
        timings={"parse_s": time.perf_counter() - started},
    )
