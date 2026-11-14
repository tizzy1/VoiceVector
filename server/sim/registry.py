"""Instruction registry: how each InstrType changes the autopilot (SIM_SPEC §2.1).

`apply(w, ac, ins)` returns True if the instruction changed something worth an
`instruction_applied` event. `evaluate` (unable / query) is added here next.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

from server.contracts import Instruction
from server.sim.aircraft import AircraftState
from server.sim.geo import dist, norm_hdg
from server.sim.modes import enter_hold

if TYPE_CHECKING:
    from server.sim.world import World

Applier = Callable[["World", AircraftState, Instruction], bool]
APPLY: dict[str, Applier] = {}


def register(*types: str) -> Callable[[Applier], Applier]:
    def deco(fn: Applier) -> Applier:
        for t in types:
            APPLY[t] = fn
        return fn
    return deco


def apply(w: "World", ac: AircraftState, ins: Instruction) -> bool:
    fn = APPLY.get(ins.type)
    return bool(fn and fn(w, ac, ins))


# ----------------------------------------------------------------- lateral
def _to_heading(w: "World", ac: AircraftState, hdg: float, turn_dir: str | None) -> None:
    """Common exit from LNAV / HOLD / LOC onto an assigned heading."""
    if ac.lat_mode == "LOC":            # broken off the approach: clearance no longer valid
        _disarm_approach(ac)
    ac.lat_mode, ac.tgt_hdg, ac.turn_dir = "HDG", norm_hdg(hdg), turn_dir
    ac.hold = ac.hold_fix = None
    if ac.phase in ("STAR", "HOLD", "GO_AROUND", "APPROACH", "FINAL"):
        ac.phase = "APPROACH" if ac.approach_cleared else "VECTORS"


def _disarm_approach(ac: AircraftState) -> None:
    ac.loc_armed = ac.gs_armed = ac.established = False
    ac.approach_cleared = None
    if ac.vert_mode == "GS":            # stop descending: hold the next 100 ft below
        ac.cleared_alt = math.floor(ac.alt / 100.0) * 100.0
        ac.vert_mode, ac.gp_alt = "LVL_CHG", None


@register("heading")
def _heading(w, ac, ins):
    if ins.value is None:
        return False
    _to_heading(w, ac, float(ins.value), ins.turn_dir)
    return True


@register("turn_by")
def _turn_by(w, ac, ins):
    if ins.value is None or not ins.turn_dir:
        return False
    sign = 1.0 if ins.turn_dir == "R" else -1.0
    _to_heading(w, ac, ac.hdg + sign * float(ins.value), ins.turn_dir)
    return True


@register("present_heading")
def _present_heading(w, ac, ins):
    _to_heading(w, ac, ac.hdg, None)
    return True


def _procedure_route(w: "World", ac: AircraftState) -> list[str]:
    kind = "STAR" if ac.flow == "ARR" else "SID"
    return list(w.egkk["procedures"][kind].get(w.runway, {}).get(ac.route_name or "", {})
                .get("route", []))


def _start_lnav(ac: AircraftState, route: list[str], on_procedure: bool) -> None:
    if ac.lat_mode == "LOC":
        _disarm_approach(ac)
    ac.lat_mode, ac.route, ac.route_idx, ac.hold, ac.turn_dir = "LNAV", route, 0, None, None
    ac.nfc_called = False
    if ac.phase in ("STAR", "HOLD", "VECTORS", "GO_AROUND", "SID"):
        if ac.flow == "ARR":
            ac.phase = "STAR" if on_procedure else "VECTORS"


@register("direct")
def _direct(w, ac, ins):
    fix = ins.fix
    if not fix or fix not in w.egkk["fixes"]:
        return False
    full = _procedure_route(w, ac)
    if fix in ac.route[ac.route_idx:]:
        route, on_proc = ac.route[ac.route.index(fix, ac.route_idx):], fix in full
    elif fix in full:
        route, on_proc = full[full.index(fix):], True
    else:
        route, on_proc = [fix], False
    ac.hold_fix = None
    _start_lnav(ac, route, on_proc)
    return True


@register("resume_nav")
def _resume_nav(w, ac, ins):
    remaining = ac.route[ac.route_idx:] or _procedure_route(w, ac)[-1:]
    if not remaining:
        return False
    nearest = min(range(len(remaining)),
                  key=lambda i: dist(ac.x, ac.y, *w.fix_xy(remaining[i])))
    _start_lnav(ac, remaining[nearest:], True)
    return True


@register("hold")
def _hold(w, ac, ins):
    fix = ins.fix or (ac.route[-1] if ac.route else None)
    if not fix or fix not in w.egkk["fixes"]:
        return False
    if dist(ac.x, ac.y, *w.fix_xy(fix)) < 1.0:
        enter_hold(w, ac, fix)
        return True
    _start_lnav(ac, [fix], fix in _procedure_route(w, ac))
    ac.hold_fix = fix
    return True


# ---------------------------------------------------------------- vertical
def _set_level(ac: AircraftState, alt_ft: float) -> bool:
    ac.cleared_alt, ac.vs_constraint, ac.expedite = alt_ft, None, False
    if ac.vert_mode == "GS":
        ac.vert_mode, ac.gp_alt = "LVL_CHG", None
    return True


@register("altitude")
def _altitude(w, ac, ins):
    return ins.value is not None and _set_level(ac, float(ins.value))


@register("flight_level")
def _flight_level(w, ac, ins):
    return ins.value is not None and _set_level(ac, float(ins.value) * 100.0)


@register("maintain_level")
def _maintain_level(w, ac, ins):
    if ins.value is None:
        return _set_level(ac, round(ac.alt / 100.0) * 100.0)
    v = float(ins.value)
    return _set_level(ac, v if v >= 1000 else v * 100.0)


@register("vertical_rate")
def _vertical_rate(w, ac, ins):
    if ins.value is None:
        return False
    kind = ins.constraint if ins.constraint in ("max", "min") else "exact"
    ac.vs_constraint = (kind, int(float(ins.value)))
    return True


@register("expedite")
def _expedite(w, ac, ins):
    ac.expedite, ac.vs_constraint = True, None
    return True


# ------------------------------------------------------------------- speed
@register("speed")
def _speed(w, ac, ins):
    if ins.value is None:
        return False
    ac.tgt_ias, ac.spd_constraint = float(ins.value), ins.constraint
    return True


@register("speed_min_clean")
def _speed_min_clean(w, ac, ins):
    ac.tgt_ias, ac.spd_constraint = float(w.perf[ac.actype]["min_clean_kt"]), None
    return True


@register("speed_cancel")
def _speed_cancel(w, ac, ins):
    ac.tgt_ias, ac.spd_constraint, ac.no_speed_restriction = None, None, True
    return True


# ---------------------------------------------------------------- approach
@register("approach_clearance")
def _approach(w, ac, ins):
    if ins.runway not in (None, w.runway):
        return False
    ac.approach_cleared = {"type": ins.approach_type or "ILS", "runway": w.runway}
    ac.loc_armed = ac.gs_armed = True
    if ac.phase in ("STAR", "HOLD", "VECTORS", "GO_AROUND"):
        ac.phase = "APPROACH"
    return True


@register("intercept_localiser")
def _intercept(w, ac, ins):
    if ins.runway not in (None, w.runway):
        return False
    ac.loc_armed = True
    return True


@register("descend_glidepath")
def _glidepath(w, ac, ins):
    ac.gs_armed = True
    if not ac.approach_cleared:
        ac.approach_cleared = {"type": "ILS", "runway": w.runway}
    if ac.phase in ("STAR", "HOLD", "VECTORS", "GO_AROUND"):
        ac.phase = "APPROACH"
    return True


@register("cancel_approach")
def _cancel_approach(w, ac, ins):
    was_loc = ac.lat_mode == "LOC"
    _disarm_approach(ac)
    if was_loc:
        ac.lat_mode, ac.tgt_hdg, ac.turn_dir = "HDG", ac.hdg, None
    if ac.phase in ("APPROACH", "FINAL"):
        ac.phase = "VECTORS"
    return True


@register("go_around")
def _go_around(w, ac, ins):
    w.go_around(ac, reason="instructed", call=False)
    return True


# ----------------------------------------------------------- comms / admin
@register("contact")
def _contact(w, ac, ins):
    ac.on_frequency, ac.freq_unit = False, ins.unit or "tower"
    if ac.flow == "DEP" and ins.unit == "london":
        ac.phase = "HANDED_OFF"
    w.emit("handoff", ac.callsign, {"unit": ac.freq_unit, "frequency": ins.frequency})
    return True


@register("squawk")
def _squawk(w, ac, ins):
    if ins.value is None or str(ins.value) == "ident":
        return False
    ac.squawk = str(ins.value).split(".")[0].zfill(4)
    return True


@register("qnh")
def _qnh(w, ac, ins):
    if ins.value is None:
        return False
    ac.qnh = int(float(ins.value))
    return True


@register("expect")
def _expect(w, ac, ins):
    ac.expect = {"what": ins.expect_what}
    return False                        # stored, nothing to show


@register("report")
def _report(w, ac, ins):
    if ins.report_what == "established":
        if ac.established:
            w.pilot_call(ac, "established", runway=w.runway)
        else:
            ac.report_established = True
    return False
