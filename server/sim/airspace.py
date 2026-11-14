"""Airspace lookups shared by the sim (evaluate) and the rules engine (A4/B1, B6).

Placeholder values (SIM_SPEC §9 ⚠ VERIFY): the RMA polygons in egkk.json carry no
altitudes, so we use 3,000 ft generally and 2,000 ft inside the final approach area.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

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
