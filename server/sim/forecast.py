"""Probabilistic conflict forecast (SIM_SPEC §3.6) — ADVISORY ONLY.

Deterministic STCA (stca.py) stays the authoritative alert. This module estimates how likely
each nearby pair is to lose separation in the next 120 s, given uncertainty in pilot reaction
time, wind, turn / climb rates, speed and level-off overshoot. It never changes aircraft
state, legality, pilot errors or clearances, and it emits no events.

Method: seeded Monte Carlo. Every candidate aircraft is rolled forward R times (rollout r
uses the same wind sample for every aircraft, so both members of a pair share it — "paired"
rollouts) with a simplified, vectorised copy of the flight model: heading/LNAV/LOC/hold
guidance, rate-limited turns, climbs and descents, speed changes, glidepath and landing.
Pending clearances are rolled in at their scheduled time with the controller's INTENDED
values — planned pilot errors are never inspected. Aircraft already flying a wrong value
are forecast as they are flying (that is what the radar shows).

p_conflict = fraction of rollouts in which the pair is ever below BOTH 3 NM and 1,000 ft
(same-localiser pairs at >= 2.5 NM in trail excepted, as in STCA).

Seeding: per aircraft from (session seed, sim tick, callsign) and per rollout wind from
(session seed, sim tick). One simulation serves every pair, so a replay is identical. (The
spec suggested seeding per pair; seeding per aircraft gives the same reproducibility at a
fraction of the cost, because each aircraft is simulated once instead of once per pair.)
"""
from __future__ import annotations

import math
import time
import zlib
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from server.sim.aircraft import AircraftState

if TYPE_CHECKING:
    from server.sim.world import World

# ---- forecast parameters (⚠ VERIFY: simulation assumptions, see data/VERIFY.md) ----------
ROLLOUTS = 64
HORIZON_S = 120
STEP_S = 5
RECOMPUTE_S = 2.0
CONFLICT_NM = 3.0
CONFLICT_FT = 1000.0
IN_TRAIL_NM = 2.5
PUBLISH_MIN_P = 0.01            # pairs below this are dropped from the output
PATHS_MIN_P = 0.15              # sampled trajectories only for pairs at/above this
PATH_SAMPLES = 8                # rollouts drawn per published pair
PATH_EVERY_S = 15
# candidate prefilter
NEAR_NM = 6.0
FAR_NM = 20.0
FAR_FT = 3000.0
# perturbations
DELAY_MEAN_S, DELAY_SD_S, DELAY_MIN_S, DELAY_MAX_S = 0.5, 1.0, -0.5, 4.0   # added to pending
WIND_SD_KT = 5.0                # per component, shared by all aircraft in a rollout
TURN_SD, TURN_MIN, TURN_MAX = 0.10, 0.70, 1.20                             # x turn rate
VERT_SD, VERT_MIN, VERT_MAX = 0.15, 0.60, 1.40                             # x climb/descent
SPEED_SD = 0.02                 # x true airspeed
SPEED_TGT_SD_KT = 5.0           # offset on any speed target
OVERSHOOT_MAX_FT = 150.0        # level-off overshoot, uniform 0..max
# flight-model constants mirrored from modes.py / kinematics.py
LNAV_LEAD_NM = 1.5
LOC_CAPTURE_NM, LOC_MAX_ANGLE = 2.0, 45.0
LOC_GAIN, LOC_MAX_CORR = 20.0, 30.0
GS_BELOW_FT, GS_ABOVE_FT, GS_MAX_FPM = 30.0, 200.0, 1800.0
HOLD_IAS = 220.0
GO_AROUND_ALT = 3000.0

HDG, LNAV, LOC, HOLD, RWY, DIRECT = range(6)
_MODE = {"HDG": HDG, "LNAV": LNAV, "LOC": LOC, "HOLD": HOLD, "RWY_TRACK": RWY}
GROUND = ("LANDED", "QUEUED", "TAKEOFF")


def _seed(*parts: Any) -> list[int]:
    return [zlib.crc32(str(p).encode()) for p in parts]


def _wrap180(d: np.ndarray) -> np.ndarray:
    return (d + 180.0) % 360.0 - 180.0


class Forecaster:
    """Owned by World. update() is cheap to call every tick; it recomputes every 2 s."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.last_t: Optional[float] = None
        self.last_ms: float = 0.0
        self.enabled = True

    def reset(self) -> None:
        self.results, self.last_t, self.last_ms = [], None, 0.0

    def update(self, w: "World", force: bool = False) -> None:
        if not self.enabled:
            return
        if not force and self.last_t is not None and w.t - self.last_t < RECOMPUTE_S - 1e-9:
            return
        t0 = time.perf_counter()
        self.results = forecast(w)
        self.last_t = w.t
        self.last_ms = (time.perf_counter() - t0) * 1000.0

    def for_callsign(self, cs: str) -> list[dict[str, Any]]:
        return [f for f in self.results if cs in f["callsigns"]]


# =========================================================================================
def candidate_pairs(acs: list[AircraftState]) -> list[tuple[int, int]]:
    pairs = []
    for i, a in enumerate(acs):
        for j in range(i + 1, len(acs)):
            b = acs[j]
            d = math.hypot(a.x - b.x, a.y - b.y)
            if d > FAR_NM:
                continue
            if d <= NEAR_NM or abs(a.alt - b.alt) <= FAR_FT or _levels_cross(a, b):
                pairs.append((i, j))
    return pairs


def _levels_cross(a: AircraftState, b: AircraftState) -> bool:
    lo_a, hi_a = sorted((a.alt, a.cleared_alt))
    lo_b, hi_b = sorted((b.alt, b.cleared_alt))
    return lo_a - CONFLICT_FT < hi_b and lo_b - CONFLICT_FT < hi_a


def forecast(w: "World", extra: Optional[list[tuple[float, str, Any]]] = None,
             only: Optional[str] = None) -> list[dict[str, Any]]:
    """All candidate pairs (or only pairs with `only`). `extra` = what-if clearances
    [(seconds from now, callsign, Instruction)], used by rule B9; never applied."""
    airborne = [a for a in w.aircraft.values() if a.phase not in GROUND]
    pairs = candidate_pairs(airborne)
    if only is not None:
        pairs = [p for p in pairs if only in (airborne[p[0]].callsign, airborne[p[1]].callsign)]
    if not pairs:
        return []
    used = sorted({k for p in pairs for k in p})
    acs = [airborne[k] for k in used]
    local = {k: n for n, k in enumerate(used)}
    traj = _simulate(w, acs, extra or [])
    out = []
    for i, j in pairs:
        f = _pair_result(w, traj, acs, local[i], local[j])
        if f is not None:
            out.append(f)
    out.sort(key=lambda f: (-f["p_conflict"], f["callsigns"]))
    return out


# =========================================================================================
def _simulate(w: "World", acs: list[AircraftState],
              extra: list[tuple[float, str, Any]]) -> dict[str, np.ndarray]:
    N, R = len(acs), ROLLOUTS
    S = HORIZON_S // STEP_S
    dt = float(STEP_S)
    tick = int(round(w.t))
    seed = int(w.seed)

    # ---------------- per-aircraft perturbations (seeded by callsign) ----------------
    turnf = np.empty((N, R)); vertf = np.empty((N, R)); spdf = np.empty((N, R))
    spd_off = np.empty((N, R)); overshoot = np.empty((N, R)); delay = np.empty((N, R))
    for n, ac in enumerate(acs):
        rng = np.random.default_rng(_seed(seed, tick, ac.callsign))
        turnf[n] = np.clip(rng.normal(1.0, TURN_SD, R), TURN_MIN, TURN_MAX)
        vertf[n] = np.clip(rng.normal(1.0, VERT_SD, R), VERT_MIN, VERT_MAX)
        spdf[n] = rng.normal(1.0, SPEED_SD, R)
        spd_off[n] = rng.normal(0.0, SPEED_TGT_SD_KT, R)
        overshoot[n] = rng.uniform(0.0, OVERSHOOT_MAX_FT, R)
        delay[n] = np.clip(rng.normal(DELAY_MEAN_S, DELAY_SD_S, R), DELAY_MIN_S, DELAY_MAX_S)
    wrng = np.random.default_rng(_seed(seed, tick, "wind"))
    wx = w.wind[0] + wrng.normal(0.0, WIND_SD_KT, R)[None, :]      # (1, R): shared = paired
    wy = w.wind[1] + wrng.normal(0.0, WIND_SD_KT, R)[None, :]

    # ---------------- initial state, broadcast over rollouts ----------------
    def col(vals: list[float]) -> np.ndarray:
        return np.repeat(np.asarray(vals, dtype=float)[:, None], R, axis=1)

    x = col([a.x for a in acs]); y = col([a.y for a in acs]); alt = col([a.alt for a in acs])
    hdg = col([a.hdg for a in acs]); ias = col([a.ias for a in acs])
    lvl = col([a.cleared_alt for a in acs])
    tgt_hdg = col([a.tgt_hdg if a.tgt_hdg is not None else a.hdg for a in acs])
    tdir = col([{"R": 1.0, "L": -1.0}.get(a.turn_dir or "", 0.0) for a in acs])
    tgt_ias = col([a.tgt_ias if a.tgt_ias is not None else np.nan for a in acs]) + spd_off
    nsr = col([1.0 if a.no_speed_restriction else 0.0 for a in acs]) > 0
    expd = col([1.0 if a.expedite else 0.0 for a in acs]) > 0
    mode = col([_MODE.get(a.lat_mode, HDG) for a in acs]).astype(np.int8)
    loc_arm = col([1.0 if a.loc_armed else 0.0 for a in acs]) > 0
    gs_arm = col([1.0 if a.gs_armed else 0.0 for a in acs]) > 0
    gs_on = col([1.0 if a.vert_mode == "GS" else 0.0 for a in acs]) > 0
    appr = col([1.0 if a.approach_cleared else 0.0 for a in acs]) > 0
    active = np.ones((N, R), dtype=bool)
    overshot = np.zeros((N, R), dtype=bool)
    vdir = np.sign(lvl - alt)                                     # direction of level change

    # performance per aircraft (N, 1)
    def perf(key: str) -> np.ndarray:
        return np.asarray([float(w.perf[a.actype][key]) for a in acs])[:, None]
    climb_lo, climb_hi = perf("climb_fpm_low"), perf("climb_fpm_mid")
    desc_lo, desc_hi = perf("descent_fpm_low"), perf("descent_fpm")
    vapp = perf("vapp_kt")
    accel = float(w.rules["accel_kt_per_s"]); decel = float(w.rules["decel_kt_per_s_level"])
    max_low = float(w.rules["max_ias_below_fl100_kt"])
    bank_k = 1091.0 * math.tan(math.radians(float(w.rules["max_bank_deg"])))

    # routes: padded fix coordinates (N, L, 2) + per-rollout index; DIRECT target per rollout
    routes = [a.route[a.route_idx:] if a.lat_mode == "LNAV" else [] for a in acs]
    L = max(1, max(len(r) for r in routes))
    rxy = np.full((N, L, 2), np.nan)
    rlen = np.zeros(N, dtype=int)
    end_hold = np.zeros(N, dtype=bool)
    for n, r in enumerate(routes):
        pts = [w.fix_xy(f) for f in r if f in w.egkk["fixes"]]
        rlen[n] = len(pts)
        if pts:
            rxy[n, :len(pts)] = pts
            end_hold[n] = r[-1] in w.hold_fixes
    ridx = np.zeros((N, R), dtype=int)
    dxy = np.full((N, R, 2), np.nan)                              # DIRECT / HOLD centre
    for n, a in enumerate(acs):
        if a.lat_mode == "HOLD" and a.hold:
            dxy[n, :] = w.fix_xy(a.hold["fix"])

    # runway geometry
    c = math.radians(w.loc_course)
    sc, cc = math.sin(c), math.cos(c)
    thx, thy = w.thr_xy
    elev, gsft = w.rwy_elev, w.gs_ft_per_nm

    # pending clearances, INTENDED values only
    index = {a.callsign: n for n, a in enumerate(acs)}
    pend = []
    for at, _, cs, el in sorted(w._pending, key=lambda p: p[:2]):
        if cs in index:
            pend.append((at + el.delay_s - w.t, index[cs], el.intended))
    for t_rel, cs, ins in extra:
        if cs in index:
            pend.append((float(t_rel), index[cs], ins))
    applied = np.zeros((len(pend), R), dtype=bool)

    S1 = S + 1
    X = np.empty((S1, N, R)); Y = np.empty((S1, N, R)); A = np.empty((S1, N, R))
    ACT = np.empty((S1, N, R), dtype=bool); ONLOC = np.empty((S1, N, R), dtype=bool)
    X[0], Y[0], A[0], ACT[0], ONLOC[0] = x, y, alt, active, mode == LOC

    for s in range(1, S1):
        t = s * dt
        # ---- pending clearances due this step (per rollout reaction delay) ----
        for e, (t_app, n, ins) in enumerate(pend):
            due = (~applied[e]) & (t_app + delay[n] <= t)
            if not due.any():
                continue
            applied[e] |= due
            _apply(ins, n, due, w, locals())
        # ---- along / cross-track for LOC logic ----
        px, py = x - thx, y - thy
        along = -(px * sc + py * cc)
        xte = px * cc - py * sc
        trk_err = np.abs(_wrap180(hdg - w.loc_course))
        cap = loc_arm & (mode != LOC) & (along > 0) & (np.abs(xte) < LOC_CAPTURE_NM) \
            & (trk_err <= LOC_MAX_ANGLE) & active
        mode = np.where(cap, LOC, mode).astype(np.int8)
        tdir = np.where(cap, 0.0, tdir)
        gp = elev + gsft * np.maximum(along, 0.0)
        gcap = (gs_arm & (mode == LOC) & ~gs_on
                & (alt >= gp - GS_BELOW_FT) & (alt <= gp + GS_ABOVE_FT))
        gs_on = gs_on | gcap
        # ---- lateral guidance: desired track per mode ----
        desired = tgt_hdg.copy()
        # LNAV
        lnav = mode == LNAV
        if lnav.any():
            for _ in range(2):                                   # sequence up to 2 fixes a step
                ii = np.minimum(ridx, L - 1)
                fx = np.take_along_axis(rxy[:, :, 0], ii, axis=1)
                fy = np.take_along_axis(rxy[:, :, 1], ii, axis=1)
                dist = np.hypot(fx - x, fy - y)
                seq = lnav & (dist < LNAV_LEAD_NM) & (ridx < rlen[:, None])
                ridx = ridx + seq
            done = lnav & (ridx >= rlen[:, None])
            to_hold = done & end_hold[:, None]
            if to_hold.any():
                last = np.maximum(rlen - 1, 0)
                for n in np.nonzero(to_hold.any(axis=1))[0]:
                    dxy[n, to_hold[n]] = rxy[n, last[n]]
                mode = np.where(to_hold, HOLD, mode).astype(np.int8)
            mode = np.where(done & ~to_hold, HDG, mode).astype(np.int8)
            tgt_hdg = np.where(done & ~to_hold, hdg, tgt_hdg)
            lnav = mode == LNAV
            ii = np.minimum(ridx, L - 1)
            fx = np.take_along_axis(rxy[:, :, 0], ii, axis=1)
            fy = np.take_along_axis(rxy[:, :, 1], ii, axis=1)
            desired = np.where(lnav, np.degrees(np.arctan2(fx - x, fy - y)) % 360.0, desired)
        dmask = mode == DIRECT
        if dmask.any():
            brg = np.degrees(np.arctan2(dxy[..., 0] - x, dxy[..., 1] - y)) % 360.0
            arrived = dmask & (np.hypot(dxy[..., 0] - x, dxy[..., 1] - y) < LNAV_LEAD_NM)
            desired = np.where(dmask, brg, desired)
            mode = np.where(arrived, HDG, mode).astype(np.int8)
            tgt_hdg = np.where(arrived, hdg, tgt_hdg)
        locm = mode == LOC
        corr = np.clip(LOC_GAIN * xte, -LOC_MAX_CORR, LOC_MAX_CORR)
        desired = np.where(locm, (w.loc_course - corr) % 360.0, desired)
        desired = np.where(mode == RWY, w.loc_course, desired)
        holdm = mode == HOLD
        desired = np.where(holdm, (hdg + 90.0) % 360.0, desired)   # simplified: orbit right
        tdir = np.where(holdm, 1.0, tdir)
        steer = lnav | dmask | locm | (mode == RWY)
        tdir = np.where(steer, 0.0, tdir)
        # wind correction for track-following modes
        tas_now = ias * (1.0 + 0.02 * alt / 1000.0) * spdf
        r = np.radians(desired)
        cross = wx * np.cos(r) - wy * np.sin(r)
        wca = np.degrees(np.arcsin(np.clip(cross / np.maximum(tas_now, 1.0), -0.5, 0.5)))
        desired = np.where(steer, (desired - wca) % 360.0, desired)
        # ---- turn ----
        d = _wrap180(desired - hdg)
        d = np.where((tdir > 0) & (d < 0), d + 360.0, d)
        d = np.where((tdir < 0) & (d > 0), d - 360.0, d)
        rate = np.minimum(3.0, bank_k / np.maximum(tas_now, 1.0)) * turnf * dt
        reached = np.abs(d) <= rate
        hdg = np.where(reached, desired, hdg + np.clip(d, -rate, rate)) % 360.0
        tdir = np.where(reached & ~holdm, 0.0, tdir)
        # ---- speed ----
        tgt = np.where(np.isnan(tgt_ias), ias, tgt_ias)
        tgt = np.where(holdm, np.minimum(tgt, HOLD_IAS), tgt)
        sched = np.where(along > 8.0, 180.0, np.where(along > 4.0, 160.0, vapp))
        tgt = np.where(locm & appr, np.minimum(tgt, sched), tgt)
        tgt = np.where((alt < 10000) & ~nsr, np.minimum(tgt, max_low), tgt)
        ias = ias + np.clip(tgt - ias, -decel * dt, accel * dt)
        # ---- vertical ----
        low = alt < 10000
        climb = np.where(low, climb_lo, climb_hi) * vertf
        desc = np.where(low, desc_lo, desc_hi) * vertf
        climb = np.where(expd, np.minimum(climb * 1.5, 3500.0), climb)
        desc = np.where(expd, np.minimum(desc * 1.5, 3500.0), desc)
        err = lvl - alt
        stepft = np.clip(err, -desc * dt / 60.0, climb * dt / 60.0)
        new_alt = alt + stepft
        crossed = ~overshot & (vdir != 0) & (np.sign(lvl - new_alt) != vdir) & (np.abs(err) > 1)
        new_alt = np.where(crossed, lvl + vdir * overshoot, new_alt)
        overshot = overshot | crossed
        gstep = np.clip(gp - alt, -GS_MAX_FPM * dt / 60.0, 0.0)
        alt = np.where(gs_on, alt + gstep, new_alt)
        # ---- position ----
        tas = ias * (1.0 + 0.02 * alt / 1000.0) * spdf
        hr = np.radians(hdg)
        vx = tas * np.sin(hr) + wx
        vy = tas * np.cos(hr) + wy
        x = x + vx * dt / 3600.0
        y = y + vy * dt / 3600.0
        # ---- landing ----
        px, py = x - thx, y - thy
        landed = gs_on & (-(px * sc + py * cc) <= 0.05)
        active = active & ~landed
        X[s], Y[s], A[s], ACT[s], ONLOC[s] = x, y, alt, active, mode == LOC
    return {"x": X, "y": Y, "alt": A, "active": ACT, "loc": ONLOC}


def _apply(ins: Any, n: int, m: np.ndarray, w: "World", st: dict[str, Any]) -> None:
    """Roll one intended clearance into rollouts `m` of aircraft row n (mutates arrays)."""
    kind, v = ins.type, ins.value
    mode, tgt_hdg, tdir, hdg = st["mode"], st["tgt_hdg"], st["tdir"], st["hdg"]
    lvl, alt, vdir, overshot = st["lvl"], st["alt"], st["vdir"], st["overshot"]
    tgt_ias, spd_off = st["tgt_ias"], st["spd_off"]

    def set_level(ft: float) -> None:
        lvl[n, m] = ft
        vdir[n, m] = np.sign(ft - alt[n, m])
        overshot[n, m] = False
        st["gs_on"][n, m] = False

    def to_heading(h: float, d: float) -> None:
        broke_off = m & (mode[n] == LOC)              # leaving the localiser cancels the ILS
        st["loc_arm"][n, broke_off] = st["gs_arm"][n, broke_off] = False
        st["appr"][n, broke_off] = st["gs_on"][n, broke_off] = False
        mode[n, m] = HDG
        tgt_hdg[n, m] = h % 360.0
        tdir[n, m] = d

    sign = {"R": 1.0, "L": -1.0}.get(ins.turn_dir or "", 0.0)
    if kind == "heading" and v is not None:
        to_heading(float(v), sign)
    elif kind == "turn_by" and v is not None:
        if sign:
            to_heading(0.0, sign)
            tgt_hdg[n, m] = (hdg[n, m] + sign * float(v)) % 360.0
        else:
            to_heading(float(v), 0.0)
    elif kind == "present_heading":
        to_heading(0.0, 0.0)
        tgt_hdg[n, m] = hdg[n, m]
    elif kind in ("direct", "hold") and ins.fix and ins.fix in w.egkk["fixes"]:
        mode[n, m] = DIRECT
        st["dxy"][n, m] = w.fix_xy(ins.fix)
    elif kind == "altitude" and v is not None:
        set_level(float(v))
    elif kind == "flight_level" and v is not None:
        set_level(float(v) * 100.0)
    elif kind == "maintain_level" and v is not None:
        f = float(v)
        set_level(f if f >= 1000 else f * 100.0)
    elif kind == "expedite":
        st["expd"][n, m] = True
    elif kind == "speed" and v is not None:
        tgt_ias[n, m] = float(v) + spd_off[n, m]
    elif kind == "speed_min_clean":
        tgt_ias[n, m] = float(w.perf[st["acs"][n].actype]["min_clean_kt"]) + spd_off[n, m]
    elif kind == "speed_cancel":
        tgt_ias[n, m] = np.nan
        st["nsr"][n, m] = True
    elif kind == "approach_clearance":
        st["loc_arm"][n, m] = st["gs_arm"][n, m] = st["appr"][n, m] = True
    elif kind == "intercept_localiser":
        st["loc_arm"][n, m] = True
    elif kind == "descend_glidepath":
        st["gs_arm"][n, m] = st["appr"][n, m] = True
    elif kind == "cancel_approach":
        st["loc_arm"][n, m] = st["gs_arm"][n, m] = st["appr"][n, m] = False
        st["gs_on"][n, m] = False
        mode[n, m] = np.where(mode[n, m] == LOC, HDG, mode[n, m])
        tgt_hdg[n, m] = np.where(mode[n, m] == HDG, hdg[n, m], tgt_hdg[n, m])
    elif kind == "go_around":
        mode[n, m] = RWY
        st["loc_arm"][n, m] = st["gs_arm"][n, m] = st["appr"][n, m] = False
        set_level(GO_AROUND_ALT)
    elif kind == "resume_nav":
        mode[n, m] = np.where(st["rlen"][n] > 0, LNAV, mode[n, m])


# =========================================================================================
def _pair_result(w: "World", tr: dict[str, np.ndarray], acs: list[AircraftState],
                 i: int, j: int) -> Optional[dict[str, Any]]:
    X, Y, A, ACT, LOCF = tr["x"], tr["y"], tr["alt"], tr["active"], tr["loc"]
    d = np.hypot(X[1:, i] - X[1:, j], Y[1:, i] - Y[1:, j])          # (S, R)
    dv = np.abs(A[1:, i] - A[1:, j])
    both = ACT[1:, i] & ACT[1:, j]
    trail_ok = LOCF[1:, i] & LOCF[1:, j] & (d >= IN_TRAIL_NM)
    hit = both & (d < CONFLICT_NM) & (dv < CONFLICT_FT) & ~trail_ok
    any_hit = hit.any(axis=0)                                       # (R,)
    p = float(any_hit.mean())
    if p < PUBLISH_MIN_P:
        return None
    dm = np.where(both, d, np.inf)
    k = dm.argmin(axis=0)
    cols = np.arange(d.shape[1])
    min_nm = dm[k, cols]
    min_ft = dv[k, cols]
    first = (hit.argmax(axis=0) + 1) * STEP_S
    t_med = float(np.median(first[any_hit])) if any_hit.any() else None
    a, b = acs[i], acs[j]
    result: dict[str, Any] = {
        "callsigns": sorted((a.callsign, b.callsign)),
        "p_conflict": round(p, 3),
        "t_median_s": t_med,
        "min_sep_nm": round(float(np.median(min_nm[np.isfinite(min_nm)])), 2)
        if np.isfinite(min_nm).any() else None,
        "min_sep_ft": round(float(np.median(min_ft[np.isfinite(min_nm)])), -1)
        if np.isfinite(min_nm).any() else None,
        "rollouts": ROLLOUTS, "horizon_s": HORIZON_S, "step_s": STEP_S,
        "threshold": {"nm": CONFLICT_NM, "ft": CONFLICT_FT},
        "seed": int(w.seed), "computed_t": w.t, "advisory": True,
        "paths": {},
    }
    if p >= PATHS_MIN_P:
        every = max(1, PATH_EVERY_S // STEP_S)
        pick = np.linspace(0, ROLLOUTS - 1, PATH_SAMPLES).round().astype(int)
        for idx, cs in ((i, a.callsign), (j, b.callsign)):
            result["paths"][cs] = [
                [[round(float(X[s, idx, r]), 2), round(float(Y[s, idx, r]), 2)]
                 for s in range(0, X.shape[0], every)]
                for r in pick]
    # Client samples include the simulator's actual forecast altitude (FORECAST_PANEL.md).
    result["samples"] = []
    if p >= PATHS_MIN_P:
        for idx, cs in ((i, a.callsign), (j, b.callsign)):
            for r in pick:
                result["samples"].append({"callsign": cs, "points": [
                    (*w.proj.to_latlon(float(X[s, idx, r]), float(Y[s, idx, r])),
                     round(float(A[s, idx, r]), 1))
                    for s in range(0, X.shape[0], every)]})
    return result
