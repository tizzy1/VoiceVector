"""Pilot decision helpers (VOICE_PIPELINE §8–9): who answers, and what a report says."""
from __future__ import annotations

from typing import Any

from server.contracts import ClearanceElement, Evaluation, Instruction
from server.pilot.clearance import family, level_ft

LOW_CONFIDENCE = 0.5
REPORT_ONLY = ("confirm", "report", "traffic_info", "expect")


def first_refusal(evals: list[Evaluation]) -> Evaluation | None:
    return next((e for e in evals if e.status != "OK"), None)


def is_report_only(ins: list[Instruction]) -> bool:
    return bool(ins) and all(i.type in ("confirm",) or
                             (i.type == "report" and i.report_what != "established")
                             for i in ins)


def report_values(what: str | None, believed: list[ClearanceElement],
                  state: dict[str, Any]) -> dict[str, Any]:
    """Answer from the pilot's BELIEVED clearance (readback values: may hold an active error)."""
    by_fam = {family(e.type): e for e in believed}
    out: dict[str, Any] = {}
    lvl = by_fam.get("level")
    lat = by_fam.get("lateral")
    spd = by_fam.get("speed")
    if what in (None, "level"):
        if lvl is not None and lvl.readback.type == "flight_level":
            out["cleared_fl"] = int(float(lvl.readback.value))
        elif lvl is not None and level_ft(lvl.readback) is not None:
            out["cleared_alt"] = int(level_ft(lvl.readback))
        elif what == "level":
            out["cleared_alt"] = int(state.get("cleared_alt", 0))
    if what in (None, "heading"):
        if lat is not None and lat.readback.type == "heading":
            out["heading"] = int(float(lat.readback.value))
        elif what == "heading":
            out["heading"] = int(round(state.get("hdg", 0)))
    if what in (None, "speed"):
        if spd is not None and spd.readback.value is not None:
            out["speed"] = int(float(spd.readback.value))
        elif what == "speed":
            out["speed"] = int(round(state.get("ias", 0)))
    if what == "established":
        out["established"] = bool(state.get("established"))
    return out
