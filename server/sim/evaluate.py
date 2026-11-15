"""World.evaluate: OK / UNABLE / QUERY per instruction (SIM_SPEC §2.1–2.3). Pure: reads only."""
from __future__ import annotations

from typing import TYPE_CHECKING

from server.contracts import Evaluation, Instruction, Transmission
from server.sim.airspace import TRANSITION_ALT_FT, area_minimum_ft, transition_level_fl
from server.sim.geo import hdg_diff, norm_hdg
from server.sim.modes import glidepath_alt, loc_geometry

if TYPE_CHECKING:
    from server.sim.aircraft import AircraftState
    from server.sim.world import World

MAX_IAS_FT_BELOW = 10000
MAX_IAS_LOW_KT = 250
MAX_IAS_KT = 300
LOC_MAX_INTERCEPT_DEG = 45.0
GS_ABOVE_TOL_FT = 200.0
EXPEDITE_FACTOR = 1.5
APPROACH_TYPES_MODELLED = (None, "ILS", "LOC")


def _ok(i: int) -> Evaluation:
    return Evaluation(instr_idx=i, status="OK")


def _no(i: int, status: str, code: str, **values) -> Evaluation:
    return Evaluation(instr_idx=i, status=status, reason_code=code, reason_values=values)


def _has(tx: Transmission, *types: str) -> bool:
    return any(ins.type in types for ins in tx.instructions)


def _cleared_level(tx: Transmission, ac: "AircraftState") -> float:
    for ins in tx.instructions:
        if ins.value is None:
            continue
        if ins.type in ("altitude", "maintain_level"):
            v = float(ins.value)
            return v if v >= 1000 else v * 100.0
        if ins.type == "flight_level":
            return float(ins.value) * 100.0
    return ac.cleared_alt


def _intercept_heading(tx: Transmission, ac: "AircraftState") -> float:
    for ins in tx.instructions:
        if ins.type == "heading" and ins.value is not None:
            return norm_hdg(float(ins.value))
        if ins.type == "turn_by" and ins.value is not None and ins.turn_dir:
            return norm_hdg(ac.hdg + (1 if ins.turn_dir == "R" else -1) * float(ins.value))
    if ac.lat_mode == "HDG" and ac.tgt_hdg is not None:
        return ac.tgt_hdg
    return ac.track


def _level(w, ac, tx, i, ins: Instruction) -> Evaluation:
    if ins.value is None:
        return _ok(i)
    v = float(ins.value)
    ceiling = float(w.perf[ac.actype]["ceiling_ft"])
    if ins.type == "flight_level":
        ft = v * 100.0
        if ft > ceiling:
            return _no(i, "UNABLE", "UNABLE_CEILING", level_fl=int(v), ceiling_ft=int(ceiling))
        tl = transition_level_fl(w.qnh)
        if v < tl:
            return _no(i, "QUERY", "QUERY_LEVEL_TYPE", level_fl=int(v), transition_level=tl)
        return _ok(i)
    ft = v if (ins.type == "altitude" or v >= 1000) else v * 100.0
    if ft > ceiling:
        return _no(i, "UNABLE", "UNABLE_CEILING", alt=int(ft), ceiling_ft=int(ceiling))
    if ins.type == "altitude" and ft > TRANSITION_ALT_FT:
        return _no(i, "QUERY", "QUERY_LEVEL_TYPE", alt=int(ft),
                   transition_alt=TRANSITION_ALT_FT)
    cleared_approach = (ac.approach_cleared or ac.established
                        or _has(tx, "approach_clearance", "descend_glidepath"))
    min_alt = area_minimum_ft(w, ac)
    if ft < min_alt and not cleared_approach:
        return _no(i, "QUERY", "QUERY_LEVEL_BELOW_MIN", alt=int(ft), min_alt=min_alt)
    return _ok(i)


def _rate(w, ac, tx, i, ins: Instruction) -> Evaluation:
    if ins.value is None:
        return _ok(i)
    p = w.perf[ac.actype]
    max_rate = int(max(p["climb_fpm_low"], p["descent_fpm"]) * EXPEDITE_FACTOR)
    if ins.constraint != "max" and float(ins.value) > max_rate:
        return _no(i, "UNABLE", "UNABLE_RATE", rate=int(float(ins.value)), max_rate=max_rate)
    return _ok(i)


def _speed(w, ac, tx, i, ins: Instruction) -> Evaluation:
    if ins.value is None:
        return _ok(i)
    v = float(ins.value)
    p = w.perf[ac.actype]
    configured = ac.established or ac.phase == "FINAL"
    min_speed = int(p["vapp_kt"]) + 5 if configured else int(p["min_clean_kt"])
    if v < min_speed and ins.constraint not in ("or_greater", "min"):
        return _no(i, "UNABLE", "UNABLE_SPEED_LOW", speed=int(v), min_speed=min_speed)
    unrestricted = ac.no_speed_restriction or _has(tx, "speed_cancel")
    low = ac.alt < MAX_IAS_FT_BELOW and _cleared_level(tx, ac) < MAX_IAS_FT_BELOW
    max_speed = MAX_IAS_LOW_KT if (low and not unrestricted) else MAX_IAS_KT
    if v > max_speed and ins.constraint not in ("or_less", "max"):
        return _no(i, "UNABLE", "UNABLE_SPEED_HIGH", speed=int(v), max_speed=max_speed)
    return _ok(i)


def _fix(w, ac, tx, i, ins: Instruction) -> Evaluation:
    if ins.fix and ins.fix not in w.egkk["fixes"]:
        return _no(i, "QUERY", "QUERY_UNKNOWN_FIX", fix=ins.fix)
    if ins.type == "hold" and not ins.fix and not ac.route:
        return _no(i, "QUERY", "QUERY_AMBIGUOUS", what="hold_fix")
    if ins.type == "direct" and ins.fix and _passed_on_own_procedure(w, ac, ins.fix):
        # SIM_SPEC §2.1.1: the pilot questions a direct to a fix it has already flown past.
        return _no(i, "QUERY", "QUERY_FIX_PASSED", fix=ins.fix, what="fix_passed")
    return _ok(i)


def _passed_on_own_procedure(w, ac, fix: str) -> bool:
    """True if `fix` is on the SID/STAR the aircraft is flying and lies behind its next fix."""
    if ac.lat_mode != "LNAV" or not ac.route_name or ac.route_idx >= len(ac.route):
        return False
    kind = "STAR" if ac.flow == "ARR" else "SID"
    proc = w.egkk["procedures"][kind].get(w.runway, {}).get(ac.route_name, {}).get("route", [])
    nxt = ac.route[ac.route_idx]
    if fix not in proc or nxt not in proc:
        return False
    return proc.index(fix) < proc.index(nxt)


def _resume(w, ac, tx, i, ins: Instruction) -> Evaluation:
    kind = "STAR" if ac.flow == "ARR" else "SID"
    proc = w.egkk["procedures"][kind].get(w.runway, {}).get(ac.route_name or "", {})
    if not ac.route[ac.route_idx:] and not proc.get("route"):
        return _no(i, "QUERY", "QUERY_AMBIGUOUS", what="procedure")
    return _ok(i)


def _approach(w, ac, tx, i, ins: Instruction) -> Evaluation:
    if ins.runway not in (None, w.runway):
        return _no(i, "QUERY", "QUERY_UNKNOWN_RUNWAY", runway=ins.runway, active=w.runway)
    if ins.approach_type not in APPROACH_TYPES_MODELLED:
        return _no(i, "UNABLE", "UNABLE_NOT_MODELLED", approach_type=ins.approach_type)
    if ac.established:
        return _ok(i)
    angle = abs(hdg_diff(_intercept_heading(tx, ac), w.loc_course))
    if angle > LOC_MAX_INTERCEPT_DEG:
        return _no(i, "UNABLE", "UNABLE_INTERCEPT_ANGLE", angle=round(angle),
                   max_angle=int(LOC_MAX_INTERCEPT_DEG))
    if ins.type == "approach_clearance" and ins.approach_type in (None, "ILS"):
        along, _ = loc_geometry(w, ac)
        if along > 0:
            gp = glidepath_alt(w, along)
            lowest = min(ac.alt, _cleared_level(tx, ac))
            if lowest > gp + GS_ABOVE_TOL_FT:
                return _no(i, "UNABLE", "UNABLE_TOO_HIGH_GS", alt=int(round(lowest, -2)),
                           gp_alt=int(round(gp, -2)))
    return _ok(i)


def _glidepath(w, ac, tx, i, ins: Instruction) -> Evaluation:
    along, _ = loc_geometry(w, ac)
    if along > 0 and ac.alt > glidepath_alt(w, along) + GS_ABOVE_TOL_FT:
        return _no(i, "UNABLE", "UNABLE_TOO_HIGH_GS", alt=int(round(ac.alt, -2)),
                   gp_alt=int(round(glidepath_alt(w, along), -2)))
    return _ok(i)


def _wrong_position(w, ac, tx, i, ins: Instruction) -> Evaluation:
    return _no(i, "QUERY", "QUERY_WRONG_POSITION", what=ins.type)


RULES = {
    "altitude": _level, "flight_level": _level, "maintain_level": _level,
    "vertical_rate": _rate,
    "speed": _speed,
    "direct": _fix, "hold": _fix,
    "resume_nav": _resume,
    "approach_clearance": _approach, "intercept_localiser": _approach,
    "descend_glidepath": _glidepath,
    "cleared_to_land": _wrong_position, "cleared_takeoff": _wrong_position,
}


def evaluate(w: "World", tx: Transmission) -> list[Evaluation]:
    ac = w.aircraft.get(tx.receiver or "")
    out = []
    for i, ins in enumerate(tx.instructions):
        rule = RULES.get(ins.type)
        out.append(rule(w, ac, tx, i, ins) if (rule and ac is not None) else _ok(i))
    return out
