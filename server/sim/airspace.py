"""Airspace lookups shared by the sim (evaluate) and the rules engine (A4/B1, B6).

Placeholder values (SIM_SPEC §9 ⚠ VERIFY): the RMA polygons in egkk.json carry no
altitudes, so we use 3,000 ft generally and 2,000 ft inside the final approach area.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.sim.modes import loc_geometry

if TYPE_CHECKING:
    from server.sim.aircraft import AircraftState
    from server.sim.world import World

TRANSITION_ALT_FT = 6000
MIN_ALT_GENERAL_FT = 3000
MIN_ALT_FINAL_AREA_FT = 2000
FINAL_AREA_MAX_NM = 15.0        # along the extended centreline from the threshold
FINAL_AREA_HALF_WIDTH_NM = 3.0


def in_final_area(w: "World", ac: "AircraftState") -> bool:
    along, xte = loc_geometry(w, ac)
    return 0.0 < along <= FINAL_AREA_MAX_NM and abs(xte) <= FINAL_AREA_HALF_WIDTH_NM


def area_minimum_ft(w: "World", ac: "AircraftState") -> int:
    return MIN_ALT_FINAL_AREA_FT if in_final_area(w, ac) else MIN_ALT_GENERAL_FT


def transition_level_fl(qnh: int) -> int:
    """Lowest usable flight level above a 6,000 ft TA (⚠ VERIFY: simplified UK table)."""
    if qnh >= 1013:
        return 70
    if qnh >= 978:
        return 75
    return 80


# ---- controlled airspace (A5 / B2) ------------------------------------------------------
# egkk.json airspace = boundary line segments per volume (CTR, CTA, London TMA, Worthing CTA)
# with base/top in ft. Point-in-volume uses ray casting in four directions and a majority
# vote, because some outer TMA boundaries are clipped at 70 NM and not perfectly closed.
_RAYS = ((1.0, 0.0003), (-1.0, 0.0007), (0.0005, 1.0), (0.0002, -1.0))


def _crossings(segs: list, x: float, y: float, dx: float, dy: float) -> int:
    n = 0
    for (x1, y1), (x2, y2) in segs:
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((x1 - x) * ey - (y1 - y) * ex) / den
        u = ((x1 - x) * dy - (y1 - y) * dx) / den
        if t > 0 and 0 <= u < 1:
            n += 1
    return n


def inside_volume(vol: dict[str, Any], x: float, y: float) -> bool:
    votes = sum(_crossings(vol["segments"], x, y, dx, dy) % 2 for dx, dy in _RAYS)
    return votes >= 3


# Adjacent TMA parts share boundaries that the sector file draws only once, which leaves thin
# slivers covered by no volume. A point counts as covered if it, or a point 1.5 NM away in
# any of 8 directions, is inside the volume (⚠ VERIFY: tolerance hides gaps ≤ ~3 NM wide).
GAP_TOLERANCE_NM = 1.5
_PROBES = [(0.0, 0.0)] + [(GAP_TOLERANCE_NM * c, GAP_TOLERANCE_NM * s) for c, s in (
    (1, 0), (-1, 0), (0, 1), (0, -1), (0.7071, 0.7071), (0.7071, -0.7071),
    (-0.7071, 0.7071), (-0.7071, -0.7071))]


def _covers(vol: dict[str, Any], x: float, y: float) -> bool:
    return any(inside_volume(vol, x + dx, y + dy) for dx, dy in _PROBES)


_GRID_NM = 0.25                 # lateral lookups cached on a 0.25 NM grid
_cache: dict[tuple[int, int, int], tuple[int, ...]] = {}


def _lateral(w: "World", x: float, y: float) -> list[dict[str, Any]]:
    """Volumes covering (x, y) laterally (any altitude), cached per grid cell."""
    vols = w.egkk.get("airspace", [])
    key = (id(vols), round(x / _GRID_NM), round(y / _GRID_NM))
    hit = _cache.get(key)
    if hit is None:
        gx, gy = key[1] * _GRID_NM, key[2] * _GRID_NM
        hit = tuple(i for i, v in enumerate(vols) if _covers(v, gx, gy))
        if len(_cache) > 200_000:
            _cache.clear()
        _cache[key] = hit
    return [vols[i] for i in hit]


def cas_volumes_at(w: "World", x: float, y: float, alt: float) -> list[str]:
    """Names of the controlled-airspace volumes containing this point (vertical limits incl.)."""
    return [v["name"] for v in _lateral(w, x, y)
            if (v.get("base_ft") or 0) <= alt <= (v.get("top_ft") or 0)]


def in_cas(w: "World", x: float, y: float, alt: float) -> bool:
    return bool(cas_volumes_at(w, x, y, alt))


def cas_base_ft(w: "World", x: float, y: float) -> int | None:
    """Lowest base of controlled airspace over this point (None = none overhead)."""
    bases = [vol.get("base_ft") or 0 for vol in _lateral(w, x, y)]
    return min(bases) if bases else None
