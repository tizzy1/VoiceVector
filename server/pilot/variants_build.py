"""Readback variant bank: hand-written defaults + (optional) Claude-generated additions.

Style is free, content is verified: every template is filled from slots only (numbers never appear
as literals) and the validator re-parses every composed readback. This module writes
``data/readback_variants.json``; ``Composer`` reads it (falling back to ``DEFAULT_BANK``).

Slots (display form / speech form are produced by ``readback.slots_for``):
  DIR HDG N | VERB ING ALT FL | SPD CON | RATE | RWY APP FIX UNIT FREQ SQ QNH QNHW WHAT | RW
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
        v("alt.1", "{VERB} altitude {ALT}", "full", ("VERB",)),
        v("alt.2", "{ING} altitude {ALT}", "ing", ("ING",)),
        v("alt.3", "{VERB} {ALT}", "short", ("VERB",)),
        v("alt.4", "{ALT}", "short"),
        v("alt.5", "{ING} {ALT}", "ing", ("ING",)),
        v("alt.6", "{ING} to altitude {ALT}", "ing", ("ING",)),
        v("alt.7", "altitude {ALT}", "full"),
    ],
    "flight_level": [
        v("fl.1", "{VERB} flight level {FL}", "full", ("VERB",)),
        v("fl.2", "{ING} flight level {FL}", "ing", ("ING",)),
        v("fl.3", "flight level {FL}", "short"),
        v("fl.4", "{VERB} to flight level {FL}", "full", ("VERB",)),
        v("fl.5", "{ING} to flight level {FL}", "ing", ("ING",)),
        v("fl.6", "cleared flight level {FL}", "full"),
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
        v("spd.4", "reducing speed {SPD} {CON}", "ing"),
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
        v("exp.1", "expediting", "ing"),
        v("exp.2", "expedite", "short"),
        v("exp.3", "will expedite", "full"),
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
        v("gp.3", "descend on the I L S", "short"),
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
        v("qnh.2", "{QNHW} {QNH} hectopascals", "full"),
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
        v("rpt.2", "wilco, will report {RW}", "full", ("RW",)),
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
CONNECTORS = [", ", ", ", " and ", ", then "]

# Non-readback replies. {CALLSIGN} is always spoken by the template itself.
REASON_PHRASES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "UNABLE_SPEED_LOW": [("unable to reduce below {min_speed} knots", ("min_speed",)),
                         ("unable, minimum speed is {min_speed} knots", ("min_speed",)),
                         ("unable that speed, too slow", ())],
    "UNABLE_SPEED_HIGH": [("unable to maintain more than {max_speed} knots", ("max_speed",)),
                          ("unable, maximum speed is {max_speed}", ("max_speed",)),
                          ("unable that speed, too fast", ())],
    "UNABLE_CEILING": [("unable, our ceiling is {ceiling_ft}", ("ceiling_ft",)),
                       ("unable, that is above our ceiling", ()), ("unable to climb that high", ())],
    "UNABLE_RATE": [("unable more than {max_rate} feet per minute", ("max_rate",)),
                    ("unable that rate of descent", ()), ("unable to descend that fast", ())],
    "UNABLE_INTERCEPT_ANGLE": [("unable, the intercept angle is too large", ()),
                               ("we are going through the localiser, unable that intercept", ()),
                               ("unable to intercept from this heading", ())],
    "UNABLE_TOO_HIGH_GS": [("unable, we are too high to capture the glideslope", ()),
                           ("unable, too high for the glideslope", ())],
    "UNABLE_NOT_MODELLED": [("unable", ()), ("unable that", ())],
    "QUERY_LEVEL_BELOW_MIN": [("confirm altitude, our minimum is {min_alt}", ("min_alt",)),
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
            "slots": "DIR HDG N VERB ING ALT FL SPD CON RATE RWY APP FIX UNIT FREQ SQ QNH QNHW WHAT RW",
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


if __name__ == "__main__":
    write_default_bank(Path(__file__).resolve().parents[2] / "data" / "readback_variants.json")
