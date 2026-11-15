"""Flight plans: ADEP/ADES, requested flight level by the semicircular rule, assigned squawk.

SIM_SPEC §1.8. Every aircraft gets a plan when it is added to the World (live imports keep the
plan they arrive with). Choices are seeded per callsign, so a replay produces the same plans.

Origin/destination: airports in data/airports.json whose bearing from EGKK is within
DIRECTION_TOL_DEG of the direction the aircraft arrives from (its STAR's entry fix) or leaves
towards (its SID's last fix). An airline's hub is used when it fits that direction.

RFL (ICAO Annex 2 Appendix 3 cruising levels, IFR): magnetic track ADEP -> ADES
  000-179 -> odd levels  FL70, 90 ... 270, then RVSM 290, 310 ... 410
  180-359 -> even levels FL80, 100 ... 280, then RVSM 300, 320 ... 400
band by great-circle distance (DISTANCE_BANDS), never above the type's ceiling.
"""
from __future__ import annotations

import json
import math
import random
import zlib
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from server.sim.aircraft import AircraftState

if TYPE_CHECKING:
    from server.sim.world import World

HOME = "EGKK"
DIRECTION_TOL_DEG = 40.0
# (max distance NM, lowest FL, highest FL) — ⚠ VERIFY (data/VERIFY.md row 42)
DISTANCE_BANDS = ((100.0, 70, 150), (250.0, 160, 280), (600.0, 290, 360), (1e9, 340, 410))
LONG_HAUL_NM = 2000.0                 # only heavy (H) types fly further than this
SQUAWK_BANK = (0o2601, 0o2677)        # departures — sim bank, not real ORCAM (⚠ VERIFY)
RESERVED_SQUAWKS = {"7000", "7500", "7600", "7700", "2000", "1200", "0000"}


@lru_cache(maxsize=4)
def _airports(path: str) -> dict:
    return json.loads(Path(path).read_text())


def airports(w: "World") -> dict:
    return _airports(str(w.data_dir / "airports.json"))


def great_circle(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """(initial true bearing deg, distance NM)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    brg = (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return brg, 2 * 3440.065 * math.asin(math.sqrt(min(1.0, a)))


def semicircular_levels(mag_track: float, lo: int, hi: int) -> list[int]:
    """Cruising levels (FL numbers) in [lo, hi] for this magnetic track."""
    east = 0.0 <= (mag_track % 360.0) < 180.0
    below = [fl for fl in range(70, 290, 10) if (fl // 10) % 2 == (1 if east else 0)]
    rvsm = list(range(290 if east else 300, 420, 20))
    return [fl for fl in below + rvsm if lo <= fl <= hi]


def requested_level(w: "World", adep: str, ades: str, actype: str, rng: random.Random) -> int:
    ap = airports(w)["airports"]
    a, b = ap.get(adep), ap.get(ades)
    ceiling_fl = int(w.perf[actype]["ceiling_ft"] // 100)
    if not a or not b:
        return min(ceiling_fl, 240)
    true_trk, dist = great_circle(a["lat"], a["lon"], b["lat"], b["lon"])
    mag = (true_trk - a.get("var", 0.0)) % 360.0
    lo, hi = next((lo, hi) for limit, lo, hi in DISTANCE_BANDS if dist <= limit)
    levels = semicircular_levels(mag, lo, min(hi, ceiling_fl))
    if not levels:                                     # low ceiling: highest legal level
        levels = semicircular_levels(mag, 70, ceiling_fl)[-1:] or [70]
    return rng.choice(levels)


def _direction_from_home(w: "World", ac: AircraftState) -> float:
    """Bearing from EGKK the aircraft comes from (ARR) or heads towards (DEP)."""
    kind = "STAR" if ac.flow == "ARR" else "SID"
    route = w.egkk["procedures"][kind].get(w.runway, {}).get(ac.route_name or "", {}) \
        .get("route", [])
    fixes = [f for f in route if f in w.egkk["fixes"] and not f.startswith("FI")]
    if fixes:
        x, y = w.fix_xy(fixes[0] if ac.flow == "ARR" else fixes[-1])
    elif ac.flow == "ARR":
        x, y = ac.x, ac.y
    else:
        r = math.radians(ac.hdg)
        x, y = 30 * math.sin(r), 30 * math.cos(r)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def pick_other_end(w: "World", ac: AircraftState, rng: random.Random) -> str:
    data = airports(w)
    home = data["airports"][HOME]
    want = _direction_from_home(w, ac)

    heavy = w.perf[ac.actype]["wake"] == "H"

    def off(icao: str) -> float:
        a = data["airports"][icao]
        brg, dist = great_circle(home["lat"], home["lon"], a["lat"], a["lon"])
        if dist > LONG_HAUL_NM and not heavy:
            return 999.0                               # out of range for a narrow-body
        return abs((brg - want + 180.0) % 360.0 - 180.0)

    hub = data.get("hubs", {}).get(ac.callsign[:3])
    if hub in data["airports"] and off(hub) <= DIRECTION_TOL_DEG * 1.5:
        return hub
    others = sorted(k for k in data["airports"] if k != HOME)
    fits = [k for k in others if off(k) <= DIRECTION_TOL_DEG]
    return rng.choice(fits) if fits else min(others, key=off)


def squawk_for(w: "World", ac: AircraftState, rng: random.Random) -> str:
    used = {a.assigned_squawk for a in w.aircraft.values()} | {a.squawk for a in w.aircraft.values()}
    for _ in range(200):
        if ac.flow == "DEP":
            code = format(rng.randint(*SQUAWK_BANK), "04o")
        else:
            code = format(rng.randint(0o1000, 0o6777), "04o")
        if code not in used and code not in RESERVED_SQUAWKS:
            return code
    return ac.squawk


def assign(w: "World", ac: AircraftState) -> None:
    """Fill any missing flight-plan fields (ADEP/ADES, RFL, ASSR) deterministically."""
    rng = random.Random(zlib.crc32(f"{w.seed}:{ac.callsign}:fp".encode()))
    if not ac.adep or not ac.ades:
        other = pick_other_end(w, ac, rng)
        if ac.flow == "ARR":
            ac.adep, ac.ades = ac.adep or other, ac.ades or HOME
        else:
            ac.adep, ac.ades = ac.adep or HOME, ac.ades or other
    if not ac.rfl:
        ac.rfl = requested_level(w, ac.adep, ac.ades, ac.actype, rng)
    if not ac.assigned_squawk:
        if ac.squawk in ("2000", "") or ac.flow == "DEP":
            ac.squawk = squawk_for(w, ac, rng)
        ac.assigned_squawk = ac.squawk


def spoken_level(w: "World", fl: int) -> str:
    """'flight level two niner zero' without importing the voice layer."""
    words = w.telephony["_digits"]
    return "flight level " + " ".join(words[c] for c in str(int(fl)))
