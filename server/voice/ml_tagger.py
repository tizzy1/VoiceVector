"""Trained instruction tagger (CRF) — optional primary parser behind ML_PARSER=1.

The model only proposes SPANS over the normalised tokens ("which words are the callsign, the
heading, the level …"). Every span is then turned into an Instruction by the EXISTING grammar
(build_clauses / build_instructions) and range-checked by the existing out_of_range check; the
callsign span goes through the existing resolver. So the model can never put an unvalidated
value into a Transmission. If anything fails, the regex result is used unchanged and the
existing Haiku fallback decides as before.

ML_PARSER=0 (default) never imports sklearn_crfsuite and changes nothing.
A missing model file or dependency logs one warning and the regex parser runs as before.

Training: tools/train/{fetch_uwb,weak_label,train_crf}.py (models/crf.pkl, data/train/report.json).
"""
from __future__ import annotations

import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any, Optional

from server.contracts import CallsignInfo, Instruction, Transmission

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "models" / "crf.pkl"

PHONETIC_WORDS = {"alfa", "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
                  "hotel", "india", "juliet", "juliett", "kilo", "lima", "mike", "november",
                  "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform", "victor",
                  "whiskey", "whisky", "xray", "x-ray", "yankee", "zulu"}
CUES = ("heading", "left", "right", "turn", "climb", "descend", "altitude", "flight", "level",
        "fl", "speed", "knots", "qnh", "direct", "squawk", "contact", "ils", "approach",
        "cleared", "hold", "maintain", "reduce", "increase", "degrees", "localiser", "localizer",
        "runway", "expedite", "report", "confirm", "expect", "resume", "proceed", "fly")
CUE_SET = set(CUES)
NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "niner", "tree", "fife", "fower", "ten", "hundred", "thousand", "decimal",
                "point"}


# ------------------------------------------------------------------ features (train = infer)
class Lexicon:
    """Word lists the features look at (airline telephony words, fix names)."""

    def __init__(self, airline_words: set[str], fixes: set[str]) -> None:
        self.airline = {w.lower() for w in airline_words}
        self.fixes = {f.lower() for f in fixes}


def load_lexicon() -> Lexicon:
    import json
    data = ROOT / "data"
    tele = json.loads((data / "telephony.json").read_text())
    words: set[str] = set()
    for k, v in tele.items():
        if not k.startswith("_") and isinstance(v, str):
            words |= set(v.lower().replace("-", " ").split())
    fixes = set(json.loads((data / "egkk.json").read_text())["fixes"])
    return Lexicon(words, fixes)


def _word_feats(w: str, lex: Lexicon, p: str) -> dict[str, Any]:
    lw = w.lower()
    return {
        f"{p}w": lw, f"{p}digit": lw.isdigit() or lw in NUMBER_WORDS,
        f"{p}len": min(len(lw), 8), f"{p}suf2": lw[-2:],
        f"{p}phon": lw in PHONETIC_WORDS, f"{p}air": lw in lex.airline,
        f"{p}fix": lw in lex.fixes, f"{p}cue": lw if lw in CUE_SET else "-",
    }


def token_features(tokens: list[str], lex: Lexicon) -> list[dict[str, Any]]:
    feats = []
    n = len(tokens)
    for i, w in enumerate(tokens):
        f: dict[str, Any] = {"bias": 1.0, **_word_feats(w, lex, "")}
        for off in (-2, -1, 1, 2):
            j = i + off
            if 0 <= j < n:
                f.update(_word_feats(tokens[j], lex, f"{off}:"))
            else:
                f[f"{off}:BOS" if off < 0 else f"{off}:EOS"] = True
        feats.append(f)
    return feats


def bio_to_spans(tags: list[str]) -> list[tuple[str, int, int]]:
    spans, cur, start = [], None, 0
    for i, t in enumerate(tags + ["O"]):
        if t.startswith("B-") or t == "O" or (t.startswith("I-") and t[2:] != cur):
            if cur is not None:
                spans.append((cur, start, i))
                cur = None
            if t.startswith("B-") or t.startswith("I-"):
                cur, start = t[2:], i
    return spans


# ------------------------------------------------------------------------- the tagger
class MLTagger:
    def __init__(self, path: Path | str | None = None) -> None:
        self.ok = False
        self.path = Path(path or os.environ.get("ML_MODEL", DEFAULT_MODEL))
        try:
            with self.path.open("rb") as fh:
                bundle = pickle.load(fh)
            self.crf = bundle["crf"] if isinstance(bundle, dict) else bundle
            self.labels = bundle.get("labels", []) if isinstance(bundle, dict) else []
            self.lex = load_lexicon()
            self.ok = True
        except Exception as e:                      # missing file, missing sklearn_crfsuite …
            logger.warning("ML parser disabled: cannot load %s (%s: %s); using the regex parser",
                           self.path, type(e).__name__, e)

    def tag(self, tokens: list[str]) -> tuple[list[tuple[str, int, int]], float, list[float]]:
        """(spans [(label, start, end_exclusive)], line_confidence, per-token confidence).
        line_confidence = min over tokens of the max marginal probability."""
        if not self.ok or not tokens:
            return [], 0.0, []
        feats = token_features(tokens, self.lex)
        marg = self.crf.predict_marginals_single(feats)
        tags = [max(m, key=m.get) for m in marg]
        conf = [max(m.values()) for m in marg]
        return bio_to_spans(tags), (min(conf) if conf else 0.0), conf


_TAGGER: Optional[MLTagger] = None


def enabled() -> bool:
    return os.environ.get("ML_PARSER", "0").strip().lower() in ("1", "true", "yes", "on")


def threshold() -> float:
    try:
        return float(os.environ.get("ML_CONF", "0.80"))
    except ValueError:
        return 0.80


def get_tagger() -> Optional[MLTagger]:
    global _TAGGER
    if _TAGGER is None:
        _TAGGER = MLTagger()
    return _TAGGER if _TAGGER.ok else None


def reset() -> None:
    """Tests: forget the loaded model (e.g. after changing ML_MODEL)."""
    global _TAGGER
    _TAGGER = None


# --------------------------------------------------------- spans -> validated instructions
def spans_to_parse(tokens: list[str], spans: list[tuple[str, int, int]],
                   callsigns: list[CallsignInfo], fixes: set[str]
                   ) -> tuple[bool, Optional[str], list[Instruction]]:
    """Convert spans with the EXISTING resolver and grammar. (valid, receiver, instructions)."""
    from server.voice.parser.callsign import RESOLVED, resolve_callsign
    from server.voice.parser.grammar import build_clauses, build_instructions, out_of_range
    receiver: Optional[str] = None
    instructions: list[Instruction] = []
    for label, a, b in spans:
        piece = tokens[a:b]
        if label == "CALLSIGN":
            hit = resolve_callsign(piece, callsigns)
            if hit is None or hit.ratio < RESOLVED or (receiver and receiver != hit.info.icao):
                return False, None, []
            receiver = hit.info.icao
            continue
        clauses = build_clauses(piece, fixes)
        got, corrections, unparsed = build_instructions(clauses, fixes)
        if unparsed or corrections or not got:
            return False, None, []
        if any(out_of_range(i) for i in got) or any(i.type.upper() != label for i in got):
            return False, None, []
        instructions += got
    if receiver is None or not instructions:
        return False, None, []
    return True, receiver, instructions


def _key(i: Instruction) -> tuple:
    return (i.type, i.value, i.turn_dir, i.fix, i.runway, i.unit, i.frequency, i.verb,
            i.constraint, i.approach_type)


def same(receiver: Optional[str], instrs: list[Instruction], tx: Transmission) -> bool:
    return receiver == tx.receiver and sorted(map(_key, instrs), key=repr) == \
        sorted(map(_key, tx.instructions), key=repr)


RESOLVE_BLOCKERS = ("callsign_unresolved", "callsign_low_conf", "unparsed_clause", "out_of_range")


def decide(rx: Transmission, callsigns: list[CallsignInfo], fixes: set[str]) -> Transmission:
    """ML_PARSER=1 decision (CUSTOM_PARSER_MODEL §4.2). Returns the regex result unchanged
    unless the model's spans validate; records the outcome in rx.timings:
    parser_ml (1 = model result used), ml_conf, ml_ms, ml_valid."""
    tagger = get_tagger()
    if tagger is None:
        return rx
    if rx.intent not in ("instruction",) or rx.self_corrections:
        return rx                                   # corrections, requests: regex owns these
    started = time.perf_counter()
    tokens = rx.norm_text.split()
    spans, conf, _ = tagger.tag(tokens)
    valid, receiver, instrs = spans_to_parse(tokens, spans, callsigns, fixes)
    ms = (time.perf_counter() - started) * 1000.0
    timings = {**rx.timings, "ml_conf": round(conf, 3), "ml_ms": round(ms, 2),
               "ml_valid": float(valid)}
    resolved = not any(a in rx.ambiguities for a in RESOLVE_BLOCKERS) and bool(rx.instructions)
    if valid and resolved and same(receiver, instrs, rx):
        return rx.model_copy(update={"timings": {**timings, "parser_ml": 1.0}})   # agree
    if valid and conf >= threshold() and not resolved:
        # model primary where the rules could not resolve the line; values already validated
        from server.voice.parser import _dedupe
        keep = [a for a in rx.ambiguities if a not in RESOLVE_BLOCKERS]
        return rx.model_copy(update={
            "receiver": receiver, "receiver_confidence": max(rx.receiver_confidence, 0.9),
            "instructions": _dedupe(instrs), "ambiguities": keep,
            "timings": {**timings, "parser_ml": 1.0}})
    return rx.model_copy(update={"timings": {**timings, "parser_ml": 0.0}})
