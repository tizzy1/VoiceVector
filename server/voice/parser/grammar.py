"""Clause segmentation and per-clause grammar -> Instruction (VOICE_PIPELINE §7 steps 3-5)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

from server.contracts import Condition, Instruction
from server.voice.numbers import is_number_token, parse_frequency, parse_run

FILLERS = {"uh", "um", "er", "ah", "the", "please", "then", "and", "sorry", "now", "you",
           "roger", "wilco", "ok", "okay", "to", "for", "of", "with", "from", "on", "when",
           "it", "that", "just", "so"}
INTENT_WORDS = {"negative", "say", "again", "i", "readback", "all", "stations"}

HEADING_WORDS = {"turn", "fly", "heading", "head", "continue", "presenthdg", "left", "right"}
SPEED_WORDS = {"reduce", "increase", "speed", "slow", "minclean", "speedcancel"}
LEVEL_WORDS = {"climb", "descend", "down", "maintain", "stopdesc", "stopclimb", "glide"}
SINGLE_STARTERS = {"expedite", "direct", "hold", "resume", "clearland", "cleartakeoff", "cleared",
                   "intercept", "cancelapp", "goaround", "expect", "contact", "squawk", "qnh",
                   "report", "confirm", "traffic", "correction"}
ABSORB = {"report", "confirm", "traffic"}   # swallow the rest of the transmission
VERB_WORDS = {"climb", "descend", "down"}
SIDE = {"left": "L", "l": "L", "right": "R", "r": "R", "center": "C", "c": "C"}
RUNWAY_TOKEN = re.compile(r"^(\d{2})([lrc])$")


@dataclass
class Clause:
    family: str
    tokens: list[str]
    condition: Optional[dict] = None


ORPHAN = "_orphan"     # pseudo-family: meaningful tokens that belong to no clause (never silently dropped)


def _family_of(tok: str) -> Optional[str]:
    if tok in HEADING_WORDS:
        return "heading"
    if tok in SPEED_WORDS:
        return "speed"
    if tok in LEVEL_WORDS:
        return "level"
    return tok if tok in SINGLE_STARTERS else None


def _has_number(tokens: list[str]) -> bool:
    return any(is_number_token(t) for t in tokens)


def build_clauses(tokens: list[str], fixes: set[str]) -> list[Clause]:
    clauses: list[Clause] = []
    cur: Optional[Clause] = None
    pending: Optional[dict] = None
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < n else ""
        if t == "after":
            if nxt.upper() in fixes:
                pending = {"after_fix": nxt.upper()}
                i += 2
            else:
                i += 1
            continue
        if cur is not None and cur.family in ABSORB:
            cur.tokens.append(t)
            i += 1
            continue
        if t in FILLERS or t in INTENT_WORDS:
            i += 1
            continue
        fam = _family_of(t)
        if t in ("left", "right") and not (is_number_token(nxt) or nxt in ("turn", "heading")):
            fam = None   # "from the left", runway side
        if fam is None:
            if cur is not None:
                cur.tokens.append(t)
            elif is_number_token(t) or t in ("left", "right"):
                if clauses and clauses[-1].family == ORPHAN:
                    clauses[-1].tokens.append(t)
                else:
                    clauses.append(Clause(ORPHAN, [t]))
        elif (cur is not None and fam == cur.family and fam in ("heading", "speed", "level")
              and not _has_number(cur.tokens)):
            cur.tokens.append(t)
        elif cur is not None and cur.family == "expedite" and t in VERB_WORDS:
            cur.tokens.append(t)
        else:
            cur = Clause(fam, [t], pending)
            pending = None
            clauses.append(cur)
        i += 1
    return clauses


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _first_run(toks: list[str]):
    for k, t in enumerate(toks):
        if is_number_token(t):
            run = parse_run(toks, k)
            if run:
                return k, run
    return None, None


def _turn_dir(toks: list[str]) -> Optional[str]:
    for t in toks:
        if t == "left":
            return "L"
        if t == "right":
            return "R"
    return None


def _runway(toks: list[str]) -> Optional[str]:
    for idx, t in enumerate(toks):
        m = RUNWAY_TOKEN.match(t)
        if m:
            return m.group(1) + m.group(2).upper()
        if t == "runway":
            run = parse_run(toks, idx + 1)
            if run:
                _, digits, end = run
                side = SIDE.get(toks[end], "") if end < len(toks) else ""
                return digits + side
    return None


def _frequency(toks: list[str]) -> Optional[str]:
    for k in range(len(toks)):
        got = parse_frequency(toks, k)
        if got:
            return got[0]
    return None


def _level_instrs(toks: list[str], verb: Optional[str]) -> list[Instruction]:
    k, run = _first_run(toks)
    if run is None:
        return []
    value = run[0]
    if verb == "maintain":
        return [Instruction(type="maintain_level", value=value, verb="maintain")]
    if "fl" in toks:
        return [Instruction(type="flight_level", value=value, verb=verb)]
    return [Instruction(type="altitude", value=value, verb=verb)]


def _classify_level(toks: list[str]) -> list[Instruction]:
    if "glide" in toks:
        return [Instruction(type="descend_glidepath")]
    if "climb" in toks:
        verb = "climb"
    elif any(w in toks for w in ("descend", "down")):
        verb = "descend"
    elif any(w in toks for w in ("maintain", "stopdesc", "stopclimb")):
        verb = "maintain"
    else:
        verb = None
    if "perminute" not in toks:
        return _level_instrs(toks, verb)
    perm = toks.index("perminute")
    ats = [x for x in range(perm) if toks[x] == "at"]
    at = ats[-1] if ats else None
    rate_toks = toks[at + 1:perm] if at is not None else toks[:perm]
    level_toks = toks[:at] if at is not None else []
    after = toks[perm:]
    constraint = "max" if "less" in after else ("min" if "greater" in after else None)
    out = _level_instrs(level_toks, verb)
    _, rate = _first_run(rate_toks)
    if rate:
        out.append(Instruction(type="vertical_rate", value=rate[0],
                               verb=verb if verb in ("climb", "descend") else "descend",
                               constraint=constraint))
    return out


FUZZY_FIX_MIN = 88
_NOT_FIXES = HEADING_WORDS | SPEED_WORDS | LEVEL_WORDS | SINGLE_STARTERS | FILLERS | {
    "direct", "published", "hold", "proceed", "route", "going", "over"}


@lru_cache(maxsize=1)
def navaid_aliases() -> dict[str, str]:
    """Spoken station name -> ident ("seaford" -> SFD), from Tudor's data/spoken_names.json."""
    path = Path(__file__).resolve().parents[3] / "data" / "spoken_names.json"
    try:
        names = json.loads(path.read_text(encoding="utf-8")).get("navaids", {})
    except (OSError, ValueError):
        return {}
    return {str(n).lower(): ident for ident, n in names.items()}


def find_fix(toks: list[str], fixes: set[str]) -> tuple[Optional[str], float]:
    """Exact fix name or navaid station name ("seaford" -> SFD), else a close STT misspelling
    ("willow" -> WILLO) at reduced confidence."""
    aliases = navaid_aliases()
    for t in toks:
        if t.upper() in fixes:
            return t.upper(), 1.0
        ident = aliases.get(t)
        if ident and ident in fixes:
            return ident, 1.0
    names = sorted(fixes)
    for t in toks:
        if len(t) < 4 or t in _NOT_FIXES or not t.isalpha():
            continue
        hit = process.extractOne(t.upper(), names, scorer=fuzz.ratio, score_cutoff=FUZZY_FIX_MIN)
        if hit:
            return hit[0], 0.85
    return None, 0.0


def _report_what(toks: list[str]) -> Optional[str]:
    for word, what in (("established", "established"), ("heading", "heading"),
                       ("speed", "speed"), ("level", "level"), ("altitude", "level"),
                       ("fl", "level"), ("passing", "passing")):
        if word in toks:
            return what
    return None


def classify(clause: Clause, fixes: set[str], last_verb: Optional[str]) -> list[Instruction]:
    fam, toks = clause.family, clause.tokens
    if fam == "heading":
        k, run = _first_run(toks)
        if "presenthdg" in toks or ("continue" in toks and run is None):
            return [Instruction(type="present_heading")]
        if run is None:
            return []
        direction = _turn_dir(toks[:k])
        # A relative turn needs a direction ("turn left 20 degrees"). "heading 120 degrees"
        # or "turn 120 degrees" with no left/right is an absolute heading (shortest turn).
        relative = "degrees" in toks and direction is not None and "heading" not in toks
        kind = "turn_by" if relative else "heading"
        return [Instruction(type=kind, value=run[0], turn_dir=direction)]
    if fam == "speed":
        if "minclean" in toks:
            return [Instruction(type="speed_min_clean")]
        if "speedcancel" in toks:
            return [Instruction(type="speed_cancel")]
        _, run = _first_run(toks)
        if run is None:
            return []
        constraint = "or_greater" if "greater" in toks else ("or_less" if "less" in toks else None)
        return [Instruction(type="speed", value=run[0], constraint=constraint)]
    if fam == "level":
        return _classify_level(toks)
    if fam == "expedite":
        verb = "climb" if "climb" in toks else ("descend" if "descend" in toks or "down" in toks
                                                else last_verb)
        return [Instruction(type="expedite", verb=verb)]
    if fam in ("direct", "hold"):
        fix, confidence = find_fix(toks, fixes)
        return [Instruction(type=fam, fix=fix, confidence=confidence)] if fix else []
    if fam == "resume":
        return [Instruction(type="resume_nav")]
    if fam == "cleared":
        for word, kind in (("ils", "ILS"), ("loc", "LOC"), ("localiser", "LOC"),
                           ("rnp", "RNP"), ("visual", "VISUAL")):
            if word in toks:
                return [Instruction(type="approach_clearance", approach_type=kind,
                                    runway=_runway(toks))]
        return []
    if fam == "clearland":
        return [Instruction(type="cleared_to_land", runway=_runway(toks))]
    if fam == "cleartakeoff":
        return [Instruction(type="cleared_takeoff", runway=_runway(toks))]
    if fam == "intercept":
        return [Instruction(type="intercept_localiser", runway=_runway(toks))]
    if fam == "cancelapp":
        return [Instruction(type="cancel_approach")]
    if fam == "goaround":
        return [Instruction(type="go_around")]
    if fam == "expect":
        rwy = _runway(toks)
        what = None
        if "ils" in toks:
            what = f"ILS {rwy}" if rwy else "ILS"
        elif "fl" in toks:
            _, run = _first_run(toks)
            what = f"FL{run[0]}" if run else None
        return [Instruction(type="expect", expect_what=what)]
    if fam == "contact":
        unit = next((u for u in ("tower", "london", "director") if u in toks), None)
        return [Instruction(type="contact", unit=unit, frequency=_frequency(toks))]
    if fam == "squawk":
        if "ident" in toks:
            return [Instruction(type="squawk", value="ident")]
        _, run = _first_run(toks)
        return [Instruction(type="squawk", value=run[1])] if run else []
    if fam == "qnh":
        _, run = _first_run(toks)
        return [Instruction(type="qnh", value=run[0])] if run else []
    if fam == "report":
        return [Instruction(type="report", report_what=_report_what(toks))]
    if fam == "confirm":
        return [Instruction(type="confirm", report_what=_report_what(toks))]
    if fam == "traffic":
        return [Instruction(type="traffic_info")]
    return []


_TYPE_FAMILY = {"heading": "heading", "turn_by": "heading", "altitude": "level",
                "flight_level": "level", "maintain_level": "level", "speed": "speed"}


def _infer_family(toks: list[str], instrs: list[Instruction]) -> Optional[str]:
    if any(t in toks for t in ("fl", "altitude", "climb", "descend", "down", "feet")):
        return "level"
    if any(t in toks for t in ("heading", "turn", "left", "right", "degrees")):
        return "heading"
    if any(t in toks for t in ("speed", "knots")):
        return "speed"
    return _TYPE_FAMILY.get(instrs[-1].type) if instrs else None


def number_runs(tokens: list[str]) -> int:
    """Maximal runs of number words/digits ("decimal" joins the two halves of a frequency)."""
    runs, inside = 0, False
    for t in tokens:
        numeric = is_number_token(t) or (inside and t in ("decimal", "point"))
        if numeric and not inside:
            runs += 1
        inside = numeric
    return runs


def expected_runs(clause: Clause) -> Optional[int]:
    """How many numbers a clause of this family legitimately carries (None = unchecked)."""
    fam, toks = clause.family, clause.tokens
    if fam in ("heading", "speed", "qnh", "expect", "contact", "cleared", "intercept", "clearland",
               "cleartakeoff"):
        return 1
    if fam == "squawk":
        return 0 if "ident" in toks else 1
    if fam == "level":
        if "perminute" in toks and "at" in toks:
            return 2 if number_runs(toks[:toks.index("at")]) else 1
        return 1
    if fam in ("expedite", "direct", "hold", "resume", "cancelapp", "goaround", "report", "confirm"):
        return 0
    return None                                   # traffic info and corrections carry free numbers


def build_instructions(clauses: list[Clause], fixes: set[str]
                       ) -> tuple[list[Instruction], int, list[str]]:
    """-> (instructions, self-corrections, unparsed clause texts)."""
    instrs: list[Instruction] = []
    corrections = 0
    unparsed: list[str] = []
    last_verb: Optional[str] = None
    for clause in clauses:
        if clause.family == ORPHAN:
            unparsed.append(" ".join(clause.tokens))
            continue
        allowed = expected_runs(clause)
        if allowed is not None and number_runs(clause.tokens) > allowed:
            unparsed.append(" ".join(clause.tokens))          # e.g. "...then set the transponder to 4521"
        correcting = clause.family == "correction"
        if correcting:
            fam = _infer_family(clause.tokens, instrs)
            if fam is None:
                continue
            clause = Clause(fam, clause.tokens, clause.condition)
        new = classify(clause, fixes, last_verb)
        if not new:
            unparsed.append(" ".join(clause.tokens))      # e.g. "turn left" / "direct willow"
        if new and clause.condition:
            new[0].condition = Condition(**clause.condition)
        for ins in new:
            if ins.verb in ("climb", "descend", "maintain"):
                last_verb = ins.verb
            if not correcting:
                instrs.append(ins)
                continue
            corrections += 1
            idx = next((j for j in range(len(instrs) - 1, -1, -1)
                        if instrs[j].type == ins.type), None)
            if idx is None:
                instrs.append(ins)
                continue
            old = instrs[idx]
            ins.turn_dir = ins.turn_dir or old.turn_dir
            ins.verb = ins.verb or old.verb
            instrs[idx] = ins
    return instrs, corrections, unparsed


RANGES = {"heading": (1, 360), "flight_level": (10, 450), "altitude": (1000, 20000),
          "speed": (120, 350), "qnh": (900, 1100)}


def out_of_range(ins: Instruction) -> bool:
    """VOICE §7 step 4 range checks. Out-of-range values are kept (the pilot queries them)."""
    limits = RANGES.get(ins.type)
    if ins.type == "maintain_level" and ins.value is not None:
        limits = RANGES["flight_level"] if float(ins.value) < 1000 else RANGES["altitude"]
    if limits is None or ins.value is None:
        return False
    try:
        return not (limits[0] <= float(ins.value) <= limits[1])
    except (TypeError, ValueError):
        return False
