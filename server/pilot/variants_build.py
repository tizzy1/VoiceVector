"""Readback variant bank: hand-written defaults + (optional) Claude-generated additions.

Style is free, content is verified: every template is filled from slots only (numbers never appear
as literals) and the validator re-parses every composed readback. This module writes
``data/readback_variants.json``; ``Composer`` reads it (falling back to ``DEFAULT_BANK``).

Slots (display form / speech form are produced by ``readback.slots_for``):
  DIR HDG N | VERB ING TO ALT FL | SPD CON | RATE | RWY APP FIX UNIT FREQ SQ QNH QNHW HPA WHAT | RW EXPN
Template ``needs`` lists slots that must be non-empty for the template to be usable.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def v(vid: str, tpl: str, style: str = "full", needs: tuple[str, ...] = ()) -> dict:
    return {"id": vid, "t": tpl, "style": style, "needs": list(needs)}


# element type -> templates. styles: full | short | ing
DEFAULT_BANK: dict[str, list[dict]] = {
    "heading": [
        v("hdg.1", "{DIR} heading {HDG}", "full", ("DIR",)),
        v("hdg.2", "heading {HDG}", "full"),
        v("hdg.3", "{DIR} {HDG}", "short", ("DIR",)),
        v("hdg.4", "turning {DIR} heading {HDG}", "ing", ("DIR",)),
        v("hdg.5", "turn {DIR} heading {HDG}", "full", ("DIR",)),
        v("hdg.6", "fly heading {HDG}", "full"),
        v("hdg.7", "{DIR} turn heading {HDG}", "full", ("DIR",)),
    ],
    "turn_by": [
        v("tby.1", "turn {DIR} {N} degrees", "full", ("DIR",)),
        v("tby.2", "{DIR} {N} degrees", "short", ("DIR",)),
        v("tby.3", "turning {DIR} {N} degrees", "ing", ("DIR",)),
        v("tby.4", "{DIR} turn {N} degrees", "full", ("DIR",)),
    ],
    "present_heading": [
        v("pre.1", "continue present heading", "full"),
        v("pre.2", "present heading", "short"),
        v("pre.3", "continuing present heading", "ing"),
    ],
    "altitude": [
        # Tudor: always "altitude"/"flight level" before the value, and "to" only before that word
        # (never directly before a number, where it can be heard as "two"). {TO} is "" for maintain.
        v("alt.1", "{VERB} {TO} altitude {ALT}", "full", ("VERB",)),
        v("alt.2", "{ING} {TO} altitude {ALT}", "ing", ("ING",)),
        v("alt.3", "{VERB} {TO} altitude {ALT} feet", "full", ("VERB",)),
        v("alt.4", "altitude {ALT}", "short"),
        v("alt.5", "{ING} {TO} altitude {ALT} feet", "ing", ("ING",)),
        v("alt.7", "altitude {ALT} feet", "full"),
    ],
    "flight_level": [
        v("fl.1", "{VERB} {TO} flight level {FL}", "full", ("VERB",)),
        v("fl.2", "{ING} {TO} flight level {FL}", "ing", ("ING",)),
        v("fl.3", "flight level {FL}", "short"),
        v("fl.5", "{ING} {TO} flight level {FL} now", "ing", ("ING",)),
    ],
    "vertical_rate": [
        v("vr.1", "{VERB} at {RATE} feet per minute {CON}", "full", ("VERB",)),
        v("vr.2", "{ING} {RATE} feet per minute {CON}", "ing", ("ING",)),
        v("vr.3", "{RATE} feet per minute {CON}", "short"),
    ],
    "speed": [
        v("spd.1", "speed {SPD} {CON}", "full"),
        v("spd.2", "speed {SPD} knots {CON}", "full"),
        v("spd.3", "{SPD} knots {CON}", "short"),
        v("spd.5", "maintain speed {SPD} {CON}", "full"),
        v("spd.6", "speed {SPD}", "short"),   # rejected by the validator when a constraint is present
    ],
    "speed_cancel": [
        v("spc.1", "no speed restriction", "full"),
        v("spc.2", "resuming normal speed", "ing"),
        v("spc.3", "no A T C speed restriction", "short"),
    ],
    "speed_min_clean": [
        v("smc.1", "minimum clean speed", "full"),
        v("smc.2", "reducing minimum clean speed", "ing"),
    ],
    "expedite": [
        v("exp.1", "expediting {EXPN}", "ing", ("EXPN",)),
        v("exp.2", "expedite {EXPN}", "short", ("EXPN",)),
        v("exp.3", "will expedite {EXPN}", "full", ("EXPN",)),
        v("exp.4", "expediting", "ing"),          # only when the instruction itself did not say climb/descent
    ],
    "direct": [
        v("dir.1", "direct {FIX}", "full"),
        v("dir.2", "direct to {FIX}", "full"),
        v("dir.3", "proceeding direct {FIX}", "ing"),
        v("dir.4", "proceeding direct to {FIX}", "ing"),
        v("dir.5", "{FIX} direct", "short"),
        v("dir.6", "going direct {FIX}", "ing"),
    ],
    "hold": [
        v("hld.1", "hold at {FIX}", "full"),
        v("hld.2", "holding at {FIX}", "ing"),
        v("hld.3", "hold at {FIX} as published", "full"),
        v("hld.4", "holding over {FIX}", "ing"),
    ],
    "resume_nav": [
        v("rsm.1", "resuming own navigation", "ing"),
        v("rsm.2", "resume own navigation", "full"),
        v("rsm.3", "own navigation", "short"),
    ],
    "approach_clearance": [
        v("app.1", "cleared {APP} approach runway {RWY}", "full"),
        v("app.2", "cleared {APP} runway {RWY}", "short"),
        v("app.3", "cleared the {APP} approach runway {RWY}", "full"),
        v("app.4", "{APP} approach runway {RWY} cleared", "short"),
        v("app.5", "cleared for the {APP} runway {RWY}", "full"),
        v("app.6", "cleared {APP} approach, runway {RWY}", "full"),
    ],
    "intercept_localiser": [
        v("loc.1", "intercept localiser runway {RWY}", "full"),
        v("loc.2", "intercepting the localiser runway {RWY}", "ing"),
        v("loc.3", "intercept the localiser runway {RWY}", "full"),
    ],
    "descend_glidepath": [
        v("gp.1", "descend on the glidepath", "full"),
        v("gp.2", "descending on the glidepath", "ing"),
    ],
    "cancel_approach": [
        v("can.1", "cancelling approach clearance", "ing"),
        v("can.2", "cancel approach clearance", "full"),
    ],
    "go_around": [
        v("ga.1", "going around", "ing"),
        v("ga.2", "go around", "full"),
    ],
    "contact": [
        v("con.1", "contact {UNIT} {FREQ}", "full", ("FREQ",)),
        v("con.2", "{UNIT} {FREQ}", "short", ("FREQ",)),
        v("con.3", "contact {UNIT} on {FREQ}", "full", ("FREQ",)),
        v("con.4", "contacting {UNIT} {FREQ}", "ing", ("FREQ",)),
        v("con.5", "contact {UNIT}", "full", ("UNIT",)),
        v("con.6", "changing to {UNIT} {FREQ}", "ing", ("FREQ",)),
    ],
    "squawk": [
        v("sqk.1", "squawk {SQ}", "full"),
        v("sqk.2", "squawking {SQ}", "ing"),
        v("sqk.3", "squawk code {SQ}", "full"),
    ],
    "qnh": [
        v("qnh.1", "{QNHW} {QNH}", "full"),
        v("qnh.2", "{QNHW} {QNH} {HPA}", "full", ("HPA",)),
        v("qnh.3", "{QNH} {QNHW}", "short"),
    ],
    "expect": [
        v("exc.1", "expect {WHAT}", "full"),
        v("exc.2", "expecting {WHAT}", "ing"),
    ],
    "cleared_to_land": [v("ctl.1", "cleared to land runway {RWY}", "full")],
    "cleared_takeoff": [v("cto.1", "cleared for takeoff runway {RWY}", "full")],
    "traffic_info": [v("trf.1", "looking for the traffic", "full"), v("trf.2", "looking", "short")],
    # controller asked "report established/speed/..." -> pilot acknowledges (RW = what to report)
    "report": [
        v("rpt.1", "will report {RW}", "full", ("RW",)),
        v("rpt.3", "report {RW}", "short", ("RW",)),
        v("rpt.4", "wilco, report {RW}", "short", ("RW",)),
    ],
}
PERSONA_STYLE_WEIGHTS = {
    "crisp_airline": {"full": 3, "short": 1, "ing": 1},
    "calm_longhaul": {"full": 2, "short": 1, "ing": 3},
    "student": {"full": 4, "short": 0.25, "ing": 1},
    "rushed": {"full": 1, "short": 4, "ing": 1},
    "stressed": {"full": 1, "short": 3, "ing": 1},
}
PERSONA_PREFIX = {
    "crisp_airline": [""],
    "calm_longhaul": ["", "", "okay, ", "roger, "],
    "student": ["uh, ", "okay uh, ", ""],
    "rushed": [""],
    "stressed": [""],
}
PERSONA_SUFFIX = {
    "calm_longhaul": ["", "", ", thanks"],
    "student": ["", ", thank you"],
}
PARTIAL_LEADS = ["", "", "correction, ", "sorry about that, ", "my mistake, "]
CONNECTORS = [", ", ", ", " and "]          # never ", then ": ATC gave no order

# Non-readback replies. {CALLSIGN} is always spoken by the template itself.
REASON_PHRASES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "UNABLE_SPEED_LOW": [("unable to reduce below {min_speed} knots", ("min_speed",)),
                         ("unable, minimum speed is {min_speed} knots", ("min_speed",)),
                         ("unable that speed, too slow", ())],
    "UNABLE_SPEED_HIGH": [("unable to maintain more than {max_speed} knots", ("max_speed",)),
                          ("unable, maximum speed is {max_speed}", ("max_speed",)),
                          ("unable that speed, too fast", ())],
    "UNABLE_CEILING": [("unable, that is above our ceiling", ()), ("unable to climb that high", ())],
    "UNABLE_RATE": [("unable more than {max_rate} feet per minute", ("max_rate",)),
                    ("unable that rate of descent", ()), ("unable to descend that fast", ())],
    "UNABLE_INTERCEPT_ANGLE": [("unable, the intercept angle is too large", ()),
                               ("we are going through the localiser, unable that intercept", ()),
                               ("unable to intercept from this heading", ())],
    "UNABLE_TOO_HIGH_GS": [("unable, we are too high to capture the glideslope", ()),
                           ("unable, too high for the glideslope", ())],
    "UNABLE_NOT_MODELLED": [("unable", ()), ("unable that", ())],
    "QUERY_LEVEL_BELOW_MIN": [("confirm altitude, minimum altitude is {min_alt}", ("min_alt",)),
                              ("confirm that altitude", ()), ("say again the altitude", ())],
    "QUERY_AMBIGUOUS": [("say again", ()), ("say again please", ())],
    "QUERY_UNKNOWN_FIX": [("say again the waypoint", ()), ("confirm the waypoint", ())],
    "QUERY_UNKNOWN_RUNWAY": [("confirm the runway", ()), ("say again the runway", ())],
    "QUERY_WRONG_POSITION": [("say again that clearance", ()), ("confirm that clearance", ())],
    "QUERY_CALLSIGN": [("was that for {CALLSIGN}", ())],
    "QUERY_LEVEL_TYPE": [("confirm altitude or flight level", ())],
}
REPLIES: dict[str, list[str]] = {
    "unable": ["{REASON}, {CALLSIGN}", "{CALLSIGN}, {REASON}"],
    "query": ["{REASON}, {CALLSIGN}", "{CALLSIGN}, {REASON}"],
    "say_again_request": ["station calling Gatwick, say again your callsign",
                          "Gatwick Director, station calling, say again your callsign"],
    "looking": ["looking, {CALLSIGN}", "{CALLSIGN}, looking", "looking for the traffic, {CALLSIGN}"],
    "no_further_clearance": ["{CALLSIGN}, no further clearance", "{CALLSIGN}, any further instructions",
                             "{CALLSIGN}, approaching {FIXV}, no further clearance",
                             "Gatwick Director, {CALLSIGN}, we have no further clearance"],
    "established": ["{CALLSIGN}, established", "{CALLSIGN}, established on the localiser",
                    "established localiser {RWYPHRASE}, {CALLSIGN}"],
    "going_around": ["{CALLSIGN}, going around", "going around, {CALLSIGN}"],
    "recall": ["Gatwick Director, {CALLSIGN}, did you call us", "Gatwick Director, {CALLSIGN}, say again",
               "Gatwick Director, {CALLSIGN}, we were blocked, say again your instruction"],
    "mayday": ["mayday mayday mayday, Gatwick Director, {CALLSIGN}, {NATURE}"],
    "pan": ["pan pan pan pan, Gatwick Director, {CALLSIGN}, {NATURE}"],
    "request": ["Gatwick Director, {CALLSIGN}, request {REQUEST}", "{CALLSIGN}, request {REQUEST}"],
    "standby": ["standby, {CALLSIGN}", "{CALLSIGN}, standby"],
}


def bank_to_json(bank: dict | None = None) -> str:
    payload = {
        "_meta": {
            "what": "Pilot readback template bank (owner: Sanju; Tudor reviews phraseology). "
                    "Numbers only via slots; every readback is re-parsed by validator.py.",
            "slots": "DIR HDG N VERB ING TO ALT FL SPD CON RATE RWY APP FIX UNIT FREQ SQ QNH QNHW HPA WHAT RW EXPN",
        },
        "elements": bank or DEFAULT_BANK,
        "persona_style_weights": PERSONA_STYLE_WEIGHTS,
        "persona_prefix": PERSONA_PREFIX,
        "persona_suffix": PERSONA_SUFFIX,
        "partial_leads": PARTIAL_LEADS,
        "connectors": CONNECTORS,
    }
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def write_default_bank(path: Path) -> None:
    path.write_text(bank_to_json(), encoding="utf-8")
    logger.info("wrote %s", path)


# ======================================================================================
# Claude-generated additions (SHOULD tier): generate -> lint -> property-test -> keep
# ======================================================================================
import asyncio      # noqa: E402  (generation only)
import random       # noqa: E402
import re           # noqa: E402

SLOT_MEANINGS = {
    "DIR": "turn direction word: left / right", "HDG": "heading, spoken digit by digit",
    "N": "number of degrees for a relative turn", "VERB": "climb / descend / maintain",
    "ING": "climbing / descending / maintaining", "ALT": "altitude in feet, e.g. four thousand",
    "FL": "flight level number only; the template itself must say 'flight level' before it",
    "SPD": "speed in knots, digit by digit", "CON": "speed/rate constraint 'or greater'/'or less' (may be empty)",
    "RATE": "vertical rate in feet per minute (template must say 'feet per minute')", "RWY": "runway, e.g. two six left (template must say 'runway' before it)",
    "APP": "approach type, e.g. I L S", "FIX": "waypoint name", "UNIT": "tower / london control / director",
    "FREQ": "radio frequency", "SQ": "transponder code or 'ident'", "QNH": "pressure value",
    "QNHW": "the word QNH", "WHAT": "what to expect, e.g. I L S runway two six left", "RW": "what to report",
    "TO": "'to' for climb/descend, empty for maintain; put it before 'altitude'/'flight level'",
    "EXPN": "what is expedited: descent / climb", "HPA": "'hectopascals' when QNH < 1000, else empty",
}
OPTIONAL_SLOTS = {"DIR", "VERB", "ING", "FREQ", "UNIT", "RW", "EXPN", "HPA"}
GEN_SYSTEM = """You write radio readback phrase templates for simulated airline pilots at London Gatwick.
Each template is the phrase a pilot says to read back ONE instruction element. Variety is good, but every
template must be correct, realistic CAP 413 / ICAO-style pilot phraseology (non-standard but realistic is fine,
incorrect is not). Never include the callsign. Never write a number, digit, or the words left/right:
numbers and directions come ONLY from {SLOTS}. Use only the slot names you are given, each at most once,
and include every slot marked required. Reply with ONLY JSON:
{"templates": [{"t": "template text", "style": "full" | "short" | "ing"}]}
style: full = complete standard phrase, short = clipped, ing = present-participle ("turning ...").
House rules: always say "altitude" before {ALT} and "flight level" before {FL}; put {TO} (not a literal
"to") before "altitude"/"flight level" when {VERB}/{ING} is used; never "cleared" with a level; never
"then"; never "wilco" together with "will …"; no reduce/increase for speed; expedite must say {EXPN}."""


def sample_element(rng: random.Random, etype: str):
    """Random but valid element of one type (used to property-test templates)."""
    from server.contracts import Instruction
    verb = rng.choice(["climb", "descend", None])
    fixes = ["WILLO", "TIMBA", "HOLLY", "BOGNA", "MAY", "SFD"]
    runway = rng.choice(["26L", "26R", "08L", "08R"])
    make = {
        "heading": lambda: Instruction(type="heading", value=rng.randint(1, 360), turn_dir=rng.choice(["L", "R", None])),
        "turn_by": lambda: Instruction(type="turn_by", value=rng.choice([5, 10, 20, 30]), turn_dir=rng.choice(["L", "R"])),
        "altitude": lambda: Instruction(type="altitude", value=rng.choice(range(1000, 12500, 500)), verb=verb),
        "flight_level": lambda: Instruction(type="flight_level", value=rng.choice(range(30, 251, 10)), verb=verb),
        "vertical_rate": lambda: Instruction(type="vertical_rate", value=rng.choice([500, 1000, 1500, 2000]),
                                             verb=rng.choice(["climb", "descend"]), constraint=rng.choice(["max", "min", None])),
        "speed": lambda: Instruction(type="speed", value=rng.randrange(130, 330, 10),
                                     constraint=rng.choice(["or_greater", "or_less", None])),
        "direct": lambda: Instruction(type="direct", fix=rng.choice(fixes)),
        "hold": lambda: Instruction(type="hold", fix=rng.choice(fixes)),
        "approach_clearance": lambda: Instruction(type="approach_clearance", approach_type="ILS", runway=runway),
        "intercept_localiser": lambda: Instruction(type="intercept_localiser", runway=runway),
        "cleared_to_land": lambda: Instruction(type="cleared_to_land", runway=runway),
        "cleared_takeoff": lambda: Instruction(type="cleared_takeoff", runway=runway),
        "contact": lambda: Instruction(type="contact", unit=rng.choice(["tower", "london", "director"]),
                                       frequency=rng.choice(["124.225", "134.125", "119.6", None])),
        "squawk": lambda: Instruction(type="squawk", value=rng.choice(["ident", "4521", "0421", "7350"])),
        "qnh": lambda: Instruction(type="qnh", value=rng.randint(980, 1040)),
        "expect": lambda: Instruction(type="expect", expect_what=rng.choice(["ILS 26L", "ILS 08R", "FL70", "vectors"])),
        "report": lambda: Instruction(type="report", report_what=rng.choice(["established", "heading", "speed", "level"])),
    }
    return make[etype]() if etype in make else Instruction(type=etype)


_NUMBER_WORDS = {"zero", "one", "two", "three", "tree", "four", "five", "six", "seven", "eight", "nine", "niner",
                 "ten", "hundred", "thousand", "left", "right"}


LEVEL_TYPES = ("altitude", "flight_level", "maintain_level")


def phraseology_problem(etype: str, template: str) -> str | None:
    """Tudor's phraseology rules (review of 14 Nov), as code. None = acceptable."""
    low = template.lower()
    words = re.findall(r"[a-z]+", re.sub(r"\{\w+\}", " ", low))
    if "then" in words:
        return "'then' implies an order ATC never gave"
    if "{alt}" in low and not re.search(r"altitude \{alt\}", low):
        return "altitude value without the word 'altitude'"
    if "{fl}" in low and "flight level {fl}" not in low:
        return "flight level value without 'flight level'"
    if etype in LEVEL_TYPES and "cleared" in words:
        return "'cleared' is only for take-off, landing, approach and routes"
    if "wilco" in words and "will" in words:
        return "'wilco' and 'will …' say the same thing twice"
    if etype == "speed" and set(words) & {"reduce", "reducing", "increase", "increasing"}:
        return "speed direction verb (the instruction's direction is not known)"
    if "hectopascals" in words:
        return "'hectopascals' only below 1000 (use {HPA})"
    if etype == "descend_glidepath" and re.search(r"\bi l s\b|\bils\b", low):
        return "say 'glidepath', not 'ILS'"
    if etype == "expedite" and "{expn}" not in low and " ".join(low.split()) != "expediting":
        return "expedite without saying what (climb/descent)"
    if re.search(r"\bto \{(alt|fl|spd|hdg|qnh|n|rate|sq|freq)\}", low):
        return "'to' directly before a number (heard as 'two')"
    if re.search(r"\{(verb|ing)\}", low) and re.search(r"\bto (altitude|flight level)", low):
        return "use {TO}, not a literal 'to' ('maintain to altitude' is wrong)"
    return None


def lint(template: str, allowed: set[str]) -> str | None:
    used = re.findall(r"\{(\w+)\}", template)
    if any(u not in allowed for u in used):
        return "unknown slot"
    if len(used) != len(set(used)):
        return "slot used twice"
    if re.search(r"\}\w|\w\{", template):
        return "slot glued to text"
    literal = re.sub(r"\{\w+\}", " ", template).lower()
    if re.search(r"\d", literal) or set(re.findall(r"[a-z]+", literal)) & _NUMBER_WORDS:
        return "literal number or direction"
    if len(template) > 80 or "{" in literal or "}" in literal:
        return "malformed"
    return None


def property_test(etype: str, template: dict, samples: int = 200, seed: int = 7) -> str | None:
    """Fill with ``samples`` random values; the validator must reproduce every one."""
    from server.contracts import Instruction  # noqa: F401
    from server.pilot import validator as V
    from server.pilot.readback import Composer, _usable
    composer = _composer()
    rng = random.Random(f"{seed}|{etype}|{template['t']}")
    used = 0
    for _ in range(samples):
        el = sample_element(rng, etype)
        slots = composer.slots_for(el)
        if not _usable(template, slots):
            continue
        used += 1
        _, speech, _ = Composer.render_phrase(template["t"], slots)
        problems = V.check_element(el, speech)
        if problems:
            return problems[0]
    return None if used >= 10 else "rarely usable"


_COMPOSER = None


def _composer():
    global _COMPOSER
    if _COMPOSER is None:
        from server.pilot.readback import Composer
        _COMPOSER = Composer(Path(__file__).resolve().parents[2] / "data")
    return _COMPOSER


def _norm(t: str) -> str:
    return " ".join(t.lower().replace(",", " ").split())


async def _generate_type(client, etype: str, existing: list[dict], model: str, count: int) -> list[dict]:
    from server.agents import llm
    allowed = set(re.findall(r"\{(\w+)\}", " ".join(t["t"] for t in existing)))
    required = sorted(allowed - OPTIONAL_SLOTS - {"CON", "QNHW"})
    prompt = {
        "element_type": etype,
        "slots": {s: SLOT_MEANINGS.get(s, s) for s in sorted(allowed)},
        "required_slots": required,
        "existing_templates_do_not_repeat": [t["t"] for t in existing],
        "how_many": count,
    }
    response = await asyncio.wait_for(client.messages.create(
        model=model, max_tokens=1200, system=GEN_SYSTEM, timeout=30,
        messages=[{"role": "user", "content": json.dumps(prompt)}], **llm.model_kwargs(model)), 35)
    out = llm.json_output(response) or {}
    return [t for t in out.get("templates", []) if isinstance(t, dict) and isinstance(t.get("t"), str)]


JUDGE_SYSTEM = """You are a strict UK air traffic phraseology examiner (CAP 413). For each pilot readback template
(slots in braces are filled with correct values), decide if a real airline pilot could say it as the readback
of the given instruction type WITHOUT changing its meaning. Reject anything that changes the instruction
(e.g. "monitoring" for contact), contradicts itself, is ungrammatical, or is phraseology a pilot would not use.
Non-standard but realistic is fine. Reply with ONLY JSON: {"verdicts": [{"i": index, "ok": true|false, "why": "..."}]}"""


async def judge(client, etype: str, templates: list[dict], model: str) -> dict[int, str]:
    """Indices the examiner rejects -> reason. Any failure rejects everything (fail closed)."""
    from server.agents import llm
    if not templates:
        return {}
    try:
        response = await asyncio.wait_for(client.messages.create(
            model=model, max_tokens=1500, system=JUDGE_SYSTEM, timeout=55,
            messages=[{"role": "user", "content": json.dumps(
                {"instruction_type": etype, "templates": [{"i": i, "t": t["t"]} for i, t in enumerate(templates)]})}],
            **llm.model_kwargs(model)), 60)
        verdicts = (llm.json_output(response) or {}).get("verdicts", [])
        ok = {int(v["i"]) for v in verdicts if isinstance(v, dict) and v.get("ok") is True}
        why = {int(v["i"]): str(v.get("why", ""))[:100] for v in verdicts if isinstance(v, dict) and "i" in v}
        return {i: f"judge: {why.get(i, 'no verdict')}" for i in range(len(templates)) if i not in ok}
    except Exception as error:                                       # noqa: BLE001
        return {i: f"judge unavailable: {type(error).__name__}" for i in range(len(templates))}


async def generate_bank(count: int = 10, model: str | None = None, judge_model: str = "claude-sonnet-5-5") -> dict:
    """Ask Claude for extra phrasings per element type; keep only those that pass lint + property test."""
    from server.agents import llm
    client = llm.get_client()
    if client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    model = model or llm.FAST_MODEL
    bank = {k: [dict(t) for t in v] for k, v in DEFAULT_BANK.items()}
    report = {"model": model, "accepted": 0, "rejected": []}
    slots = asyncio.Semaphore(5)

    async def one(etype: str):
        async with slots:
            try:
                return etype, await _generate_type(client, etype, bank[etype], model, count)
            except Exception as error:                               # noqa: BLE001
                logger.warning("generation failed for %s: %r", etype, error)
                return etype, []

    targets = [e for e in bank if e not in ("traffic_info",)]
    passed: dict[str, list[dict]] = {}
    for etype, candidates in await asyncio.gather(*(one(e) for e in targets)):
        allowed = set(re.findall(r"\{(\w+)\}", " ".join(t["t"] for t in bank[etype])))
        seen = {_norm(t["t"]) for t in bank[etype]}
        n = 0
        for cand in candidates:
            text = " ".join(cand["t"].split())
            style = cand.get("style") if cand.get("style") in ("full", "short", "ing") else "full"
            if _norm(text) in seen:
                continue
            used = set(re.findall(r"\{(\w+)\}", text))
            template = {"id": f"gen.{etype}.{n + 1}", "t": text, "style": style,
                        "needs": sorted(used & OPTIONAL_SLOTS)}
            reason = lint(text, allowed) or phraseology_problem(etype, text) or property_test(etype, template)
            if reason:
                report["rejected"].append({"type": etype, "t": text, "why": reason[:120]})
                continue
            passed.setdefault(etype, []).append(template)
            seen.add(_norm(text))
            n += 1

    async def examine(etype: str):
        async with slots:
            return etype, await judge(client, etype, passed[etype], judge_model)

    for etype, verdicts in await asyncio.gather(*(examine(e) for e in passed)):
        for i, template in enumerate(passed[etype]):
            if i in verdicts:
                report["rejected"].append({"type": etype, "t": template["t"], "why": verdicts[i]})
                continue
            template["approved"] = False          # Tudor flips to true after phraseology review
            bank[etype].append(template)
            report["accepted"] += 1
    return {"bank": bank, "report": report}


def refilter_generated(path: Path) -> dict:
    """Re-check stored gen.* templates against the current rules; rewrite literal 'to' to {TO} where safe."""
    data = json.loads(path.read_text(encoding="utf-8"))
    kept, dropped = 0, 0
    rejected = list(data.get("rejected_for_review", []))
    bank = {k: [dict(t) for t in v] for k, v in DEFAULT_BANK.items()}
    for etype, templates in data.get("elements", {}).items():
        for t in templates:
            if not t.get("id", "").startswith("gen."):
                continue
            text = t["t"]
            if re.search(r"\{(VERB|ING)\}", text):
                text = re.sub(r"\bto (altitude|flight level)", r"{TO} \1", text)
            allowed = set(re.findall(r"\{(\w+)\}", " ".join(x["t"] for x in bank.get(etype, [])))) | {"TO", "EXPN", "HPA"}
            used = set(re.findall(r"\{(\w+)\}", text))
            template = {**t, "t": text, "needs": sorted(used & (OPTIONAL_SLOTS | {"EXPN", "HPA"}))}
            reason = lint(text, allowed) or phraseology_problem(etype, text) or property_test(etype, template)
            if reason:
                rejected.append({"type": etype, "t": t["t"], "why": f"rules: {reason}"[:120]})
                dropped += 1
                continue
            bank.setdefault(etype, []).append(template)
            kept += 1
    payload = json.loads(bank_to_json(bank))
    payload["_meta"].update({k: v for k, v in data.get("_meta", {}).items() if k in ("generated", "review")})
    payload["rejected_for_review"] = rejected
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"kept": kept, "dropped": dropped}


def write_generated_bank(path: Path, count: int = 10) -> dict:
    result = asyncio.run(generate_bank(count))
    payload = json.loads(bank_to_json(result["bank"]))
    payload["_meta"]["generated"] = {k: v for k, v in result["report"].items() if k != "rejected"}
    payload["_meta"]["review"] = ("Tudor: gen.* templates passed the validator (200 random values each) and a "
                                  "CAP 413 judge, but are approved:false and NOT used until you set approved:true. "
                                  "Rejected candidates are listed for reference.")
    payload["rejected_for_review"] = result["report"]["rejected"]
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return result["report"]


if __name__ == "__main__":
    import sys
    target = Path(__file__).resolve().parents[2] / "data" / "readback_variants.json"
    if "--refilter" in sys.argv:
        print(refilter_generated(target))
    elif "--generate" in sys.argv:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        logging.basicConfig(level=logging.INFO)
        rep = write_generated_bank(target)
        print(f"accepted {rep['accepted']}, rejected {len(rep['rejected'])} -> {target}")
    else:
        write_default_bank(target)
