"""Autopilot guidance (SIM_SPEC §1.4–1.5): LNAV, HOLD, LOC, GS, final speed schedule, landing.

`guide(w, ac)` runs once per tick BEFORE kinematics and only sets targets / modes.
`w` is the World (fix coordinates, runway geometry, wind, event emitters).
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from server.sim.aircraft import AircraftState
from server.sim.geo import bearing, dist, hdg_diff, heading_for_track, norm_hdg, turn_radius_nm

if TYPE_CHECKING:
    from server.sim.world import World

LNAV_MIN_LEAD_NM = 1.0
LNAV_MAX_LEAD_NM = 5.0
NFC_CALL_NM = 3.0               # "approaching WILLO, no further clearance"
HOLD_LEG_S = 60.0
HOLD_MAX_IAS = 220.0
LOC_CAPTURE_XTE_NM = 1.0
LOC_MAX_INTERCEPT_DEG = 45.0
LOC_GAIN_DEG_PER_NM = 20.0
LOC_MAX_CORRECTION_DEG = 30.0
GS_CAPTURE_BELOW_FT = 30.0
GS_CAPTURE_ABOVE_FT = 200.0
FINAL_SPEEDS = ((8.0, 180.0), (4.0, 160.0))     # (beyond this many NM, at most this IAS)
TOUCHDOWN_NM = 0.05
MISSED_NM = -0.5                # passed the threshold without landing -> go around
GO_AROUND_ALT_FT = 3000.0
GO_AROUND_IAS = 180.0


def guide(w: "World", ac: AircraftState) -> None:
    if ac.phase == "LANDED":
        return
    ac.auto_ias = None
    if ac.lat_mode == "LNAV":
        _lnav(w, ac)
    elif ac.lat_mode == "HOLD":
        _hold(w, ac)
    elif ac.lat_mode == "RWY_TRACK":
        ac.tgt_hdg = heading_for_track(w.loc_course, ac.tas, w.wind)
    if ac.loc_armed and ac.lat_mode != "LOC":
        _try_loc_capture(w, ac)
    if ac.lat_mode == "LOC":
        _loc(w, ac)


# ------------------------------------------------------------------ LNAV
def _lnav(w: "World", ac: AircraftState) -> None:
    while True:
        if ac.route_idx >= len(ac.route):
            _end_of_route(w, ac)
            return
        fix = ac.route[ac.route_idx]
        fx, fy = w.fix_xy(fix)
        d = dist(ac.x, ac.y, fx, fy)
        brg = bearing(ac.x, ac.y, fx, fy)
        lead = LNAV_MIN_LEAD_NM
        last = ac.route_idx == len(ac.route) - 1
        if not last:
            nx, ny = w.fix_xy(ac.route[ac.route_idx + 1])
            delta = abs(hdg_diff(brg, bearing(fx, fy, nx, ny)))
            radius = turn_radius_nm(ac.tas, w.max_bank)
            lead = max(LNAV_MIN_LEAD_NM,
                       min(LNAV_MAX_LEAD_NM, radius * math.tan(math.radians(min(delta, 150)) / 2)))
        if (last and ac.flow == "ARR" and ac.phase == "STAR" and d < NFC_CALL_NM
                and not ac.nfc_called and not ac.approach_cleared and ac.on_frequency):
            ac.nfc_called = True
            w.pilot_call(ac, "no_further_clearance", fix=fix)
        if d >= lead:
            ac.tgt_hdg, ac.turn_dir = heading_for_track(brg, ac.tas, w.wind), None
            return
        ac.route_idx += 1
        w.fire_conditionals(ac, after_fix=fix)
        if ac.lat_mode != "LNAV":       # a conditional changed the lateral mode
            return


def _end_of_route(w: "World", ac: AircraftState) -> None:
    fix = ac.route[-1] if ac.route else None
    if fix and (ac.hold_fix == fix or fix in w.hold_fixes):
        enter_hold(w, ac, fix)
    else:                               # nothing published: keep the present heading
        ac.lat_mode, ac.tgt_hdg, ac.turn_dir = "HDG", ac.hdg, None
        if ac.phase == "STAR":
            ac.phase = "VECTORS"


# ------------------------------------------------------------------ HOLD
def enter_hold(w: "World", ac: AircraftState, fix: str) -> None:
    """Simplified racetrack: inbound = arrival track, right turns, 1-minute legs."""
    ac.lat_mode, ac.phase, ac.hold_fix = "HOLD", "HOLD", None
    ac.hold = {"fix": fix, "inbound": ac.track, "leg": "turn_out", "t": w.t, "prev_d": 0.0}
    w.emit("hold_entered", ac.callsign, {"fix": fix})


def _hold(w: "World", ac: AircraftState) -> None:
    h = ac.hold
    if not h:
        ac.lat_mode = "HDG"
        return
    if (ac.tgt_ias if ac.tgt_ias is not None else ac.ias) > HOLD_MAX_IAS:
        ac.auto_ias = HOLD_MAX_IAS
    fx, fy = w.fix_xy(h["fix"])
    d = dist(ac.x, ac.y, fx, fy)
    outbound = heading_for_track(norm_hdg(h["inbound"] + 180.0), ac.tas, w.wind)
    to_fix = heading_for_track(bearing(ac.x, ac.y, fx, fy), ac.tas, w.wind)
    leg = h["leg"]
    if leg == "turn_out":
        _turn_right_to(ac, outbound)
        if abs(hdg_diff(ac.hdg, outbound)) < 3.0:
            h["leg"], h["t"] = "outbound", w.t
    elif leg == "outbound":
        ac.tgt_hdg, ac.turn_dir = outbound, None
        if w.t - h["t"] >= HOLD_LEG_S:
            h["leg"] = "turn_in"
    elif leg == "turn_in":
        _turn_right_to(ac, to_fix)
        if abs(hdg_diff(ac.hdg, to_fix)) < 30.0:
            h["leg"] = "inbound"
    else:                               # inbound
        ac.tgt_hdg, ac.turn_dir = to_fix, None
        if d < 0.7 or (d < 2.5 and d > h["prev_d"]):
            h["leg"] = "turn_out"
    h["prev_d"] = d


def _turn_right_to(ac: AircraftState, heading: float) -> None:
    ac.tgt_hdg = heading
    ac.turn_dir = "R" if abs(hdg_diff(ac.hdg, heading)) > 5.0 else None


# ------------------------------------------------------------- LOC / GS
def loc_geometry(w: "World", ac: AircraftState) -> tuple[float, float]:
    """(distance to threshold along the approach in NM, cross-track NM: + = right of course)."""
    c = math.radians(w.loc_course)
    px, py = ac.x - w.thr_xy[0], ac.y - w.thr_xy[1]
    along = -(px * math.sin(c) + py * math.cos(c))
    xte = px * math.cos(c) - py * math.sin(c)
    return along, xte


def glidepath_alt(w: "World", along_nm: float) -> float:
    return w.rwy_elev + w.gs_ft_per_nm * max(along_nm, 0.0)


def _try_loc_capture(w: "World", ac: AircraftState) -> None:
    along, xte = loc_geometry(w, ac)
    if along <= 0 or abs(xte) >= LOC_CAPTURE_XTE_NM:
        return
    if abs(hdg_diff(ac.track, w.loc_course)) > LOC_MAX_INTERCEPT_DEG:
        return                          # too steep: flies through the localiser
    ac.lat_mode, ac.established, ac.hold, ac.turn_dir = "LOC", True, None, None
    if ac.phase in ("STAR", "HOLD", "VECTORS", "GO_AROUND"):
        ac.phase = "APPROACH"
    w.emit("loc_captured", ac.callsign, {"runway": w.runway, "dist_nm": round(along, 1)})
    if ac.report_established and ac.on_frequency:
        ac.report_established = False
        w.pilot_call(ac, "established", runway=w.runway)


def _loc(w: "World", ac: AircraftState) -> None:
    along, xte = loc_geometry(w, ac)
    if along < MISSED_NM:
        w.go_around(ac, reason="not_landed")
        return
    correction = max(-LOC_MAX_CORRECTION_DEG, min(LOC_MAX_CORRECTION_DEG,
                                                  LOC_GAIN_DEG_PER_NM * xte))
    ac.tgt_hdg = heading_for_track(norm_hdg(w.loc_course - correction), ac.tas, w.wind)
    ac.turn_dir = None

    gp = glidepath_alt(w, along)
    if ac.vert_mode != "GS" and ac.gs_armed:
        if gp - GS_CAPTURE_BELOW_FT <= ac.alt <= gp + GS_CAPTURE_ABOVE_FT:   # from at/below only
            ac.vert_mode, ac.phase = "GS", "FINAL"
            w.emit("gs_captured", ac.callsign, {"runway": w.runway, "dist_nm": round(along, 1)})
    if ac.vert_mode == "GS":
        ac.gp_alt = gp

    if ac.approach_cleared:             # final approach speed schedule
        limit = float(w.perf[ac.actype]["vapp_kt"])
        for beyond_nm, ias in FINAL_SPEEDS:
            if along > beyond_nm:
                limit = ias
                break
        current = ac.tgt_ias if ac.tgt_ias is not None else ac.ias
        ac.auto_ias = limit if along <= FINAL_SPEEDS[-1][0] else min(limit, current)

    if ac.vert_mode == "GS" and along <= TOUCHDOWN_NM:
        w.land(ac)
