"""Short-term conflict alert: current loss of separation and a 120 s linear prediction."""
from __future__ import annotations

import math
from typing import Iterable, Optional

from server.sim.aircraft import AircraftState

PREDICT_S = 120
PREDICT_STEP_S = 5
IN_TRAIL_MIN_NM = 2.5           # both established on the same localiser


def _pos_at(ac: AircraftState, t: float) -> tuple[float, float, float]:
    d = ac.gs * t / 3600.0
    r = math.radians(ac.track)
    alt = ac.alt + ac.vs * t / 60.0
    if ac.vert_mode != "GS":            # a climb/descent stops at the cleared level
        if ac.vs < 0:
            alt = max(alt, ac.cleared_alt)
        elif ac.vs > 0:
            alt = min(alt, ac.cleared_alt)
    return ac.x + d * math.sin(r), ac.y + d * math.cos(r), alt


def separation(a: AircraftState, b: AircraftState, t: float = 0.0) -> tuple[float, float]:
    ax, ay, aalt = _pos_at(a, t)
    bx, by, balt = _pos_at(b, t)
    return math.hypot(ax - bx, ay - by), abs(aalt - balt)


def _exempt(a: AircraftState, b: AircraftState) -> bool:
    ground = ("LANDED", "QUEUED", "TAKEOFF")      # runway traffic is Tower's job, not STCA's
    return a.phase in ground or b.phase in ground


def _in_trail_ok(a: AircraftState, b: AircraftState, d: float) -> bool:
    return a.lat_mode == "LOC" and b.lat_mode == "LOC" and d >= IN_TRAIL_MIN_NM


def predicted_conflict(a: AircraftState, b: AircraftState, min_nm: float, min_ft: float,
                       horizon_s: int = PREDICT_S) -> Optional[int]:
    """Seconds until the first predicted loss of separation, or None."""
    for t in range(PREDICT_STEP_S, horizon_s + 1, PREDICT_STEP_S):
        d, dalt = separation(a, b, t)
        if d < min_nm and dalt < min_ft and not _in_trail_ok(a, b, d):
            return t
    return None


def detect(aircraft: Iterable[AircraftState], min_nm: float,
           min_ft: float) -> list[dict]:
    """[{kind, callsigns, dist_nm, dalt_ft, in_s}] — one entry per conflicting pair."""
    acs = [a for a in aircraft if a.phase not in ("LANDED", "QUEUED")]
    out: list[dict] = []
    for i, a in enumerate(acs):
        for b in acs[i + 1:]:
            if _exempt(a, b):
                continue
            d, dalt = separation(a, b)
            pair = sorted((a.callsign, b.callsign))
            if d < min_nm and dalt < min_ft:
                if not _in_trail_ok(a, b, d):
                    out.append({"kind": "STCA_LOS", "callsigns": pair, "dist_nm": d,
                                "dalt_ft": dalt, "in_s": 0})
                continue
            if d > 40:                  # cannot meet within the horizon
                continue
            t = predicted_conflict(a, b, min_nm, min_ft)
            if t is not None:
                out.append({"kind": "STCA_PRED", "callsigns": pair, "dist_nm": d,
                            "dalt_ft": dalt, "in_s": t})
    return out


def wake_on_final(aircraft: Iterable[AircraftState], along_nm, table: dict[str, float]) -> list[dict]:
    """Wake spacing on final: consecutive arrivals established on the localiser, in order
    from the threshold. One entry per leader>follower pair closer than the table minimum
    for "<lead wake>><follower wake>" (pairs not in the table fall back to radar minimum,
    which STCA already covers). along_nm(ac) = distance from threshold along the localiser."""
    final = []
    for a in aircraft:
        if a.flow != "ARR" or not (a.established or a.lat_mode == "LOC"):
            continue
        along = along_nm(a)
        if along > 0:
            final.append((along, a))
    final.sort(key=lambda p: p[0])
    out: list[dict] = []
    for (la, lead), (fa, foll) in zip(final, final[1:]):
        need = table.get(f"{lead.wake}>{foll.wake}")
        if need is not None and fa - la < need:
            out.append({"callsigns": [lead.callsign, foll.callsign], "gap_nm": fa - la,
                        "need_nm": float(need), "cat": f"{lead.wake}>{foll.wake}"})
    return out
