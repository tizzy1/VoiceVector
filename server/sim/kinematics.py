"""Per-tick flight physics (SIM_SPEC §1.3). Guidance (modes.py) sets the targets first."""
from __future__ import annotations

import math
from typing import Any

from server.sim.aircraft import AircraftState
from server.sim.geo import norm_hdg, turn_delta

MAX_TURN_RATE = 3.0             # deg/s
EXPEDITE_FACTOR = 1.5
EXPEDITE_CAP_FPM = 3500.0
GS_MAX_FPM = 1800.0
LEVEL_OFF_BAND_FT = 300.0
ROLLOUT_DECEL = 4.0             # kt/s on the runway


def tas_of(ias: float, alt: float) -> float:
    return ias * (1.0 + 0.02 * alt / 1000.0)


def speed_target(ac: AircraftState, max_below_fl100: float) -> float | None:
    tgt = ac.auto_ias if ac.auto_ias is not None else ac.tgt_ias
    if ac.alt < 10000 and not ac.no_speed_restriction:
        base = tgt if tgt is not None else ac.ias
        if base > max_below_fl100:
            return max_below_fl100
    return tgt


def step(ac: AircraftState, perf: dict[str, Any], rules: dict[str, Any],
         wind: tuple[float, float], dt: float) -> bool:
    """Advance one aircraft by dt seconds. Returns True when it levels at its cleared level."""
    if ac.phase == "LANDED":
        ac.gs = max(0.0, ac.gs - ROLLOUT_DECEL * dt)
        ac.ias, ac.tas, ac.vs = ac.gs, ac.gs, 0.0
        _move(ac, ac.gs, ac.track, dt)
        return False

    # ---- speed (deceleration is halved while descending fast) ----
    tgt = speed_target(ac, float(rules["max_ias_below_fl100_kt"]))
    if tgt is not None and ac.ias != tgt:
        if tgt > ac.ias:
            rate = float(rules["accel_kt_per_s"])
        else:
            rate = float(rules["decel_kt_per_s_level"]) * (0.5 if ac.vs < -1000 else 1.0)
        ac.ias += math.copysign(min(abs(tgt - ac.ias), rate * dt), tgt - ac.ias)
    ac.tas = tas_of(ac.ias, ac.alt)

    # ---- heading (bank-limited, explicit direction honoured) ----
    if ac.tgt_hdg is not None:
        delta = turn_delta(ac.hdg, ac.tgt_hdg, ac.turn_dir)
        rate = min(MAX_TURN_RATE, 1091.0 * math.tan(math.radians(float(rules["max_bank_deg"])))
                   / max(ac.tas, 1.0))
        if abs(delta) <= rate * dt:
            ac.hdg, ac.turn_dir = norm_hdg(ac.tgt_hdg), None
        else:
            ac.hdg = norm_hdg(ac.hdg + math.copysign(rate * dt, delta))

    # ---- vertical ----
    levelled = False
    if ac.vert_mode == "GS" and ac.gp_alt is not None:
        err = ac.gp_alt - ac.alt
        stepft = max(-GS_MAX_FPM * dt / 60.0, min(0.0, err))      # glidepath only goes down
        ac.alt += stepft
        ac.vs = stepft * 60.0 / dt
    else:
        err = ac.cleared_alt - ac.alt
        if abs(err) < 1.0:
            levelled = ac.vert_mode == "LVL_CHG"
            ac.alt, ac.vs, ac.vert_mode, ac.expedite = ac.cleared_alt, 0.0, "ALT", False
        else:
            low = ac.alt < 10000
            if err > 0:
                fpm = float(perf["climb_fpm_low"] if low else perf["climb_fpm_mid"])
            else:
                fpm = float(perf["descent_fpm_low"] if low else perf["descent_fpm"])
            if ac.expedite:
                fpm = min(fpm * EXPEDITE_FACTOR, EXPEDITE_CAP_FPM)
            if ac.vs_constraint:
                kind, value = ac.vs_constraint
                if kind == "max":
                    fpm = min(fpm, value)
                elif kind == "min":
                    fpm = min(max(fpm, value), fpm * EXPEDITE_FACTOR)
                else:
                    fpm = min(float(value), fpm * EXPEDITE_FACTOR)
            if abs(err) < LEVEL_OFF_BAND_FT:                        # smooth level-off
                fpm *= max(0.25, abs(err) / LEVEL_OFF_BAND_FT)
            stepft = min(abs(err), fpm * dt / 60.0)
            ac.vs = math.copysign(stepft * 60.0 / dt, err)
            ac.alt += math.copysign(stepft, err)
            ac.vert_mode = "LVL_CHG"

    # ---- position: air vector + wind ----
    vx = ac.tas * math.sin(math.radians(ac.hdg)) + wind[0]
    vy = ac.tas * math.cos(math.radians(ac.hdg)) + wind[1]
    ac.gs = math.hypot(vx, vy)
    ac.track = norm_hdg(math.degrees(math.atan2(vx, vy)))
    _move(ac, ac.gs, ac.track, dt)
    return levelled


def _move(ac: AircraftState, gs: float, track: float, dt: float) -> None:
    d = gs * dt / 3600.0
    ac.x += d * math.sin(math.radians(track))
    ac.y += d * math.cos(math.radians(track))
