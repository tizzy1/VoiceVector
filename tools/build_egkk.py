"""Build data/egkk.json for VoiceVector from the VATSIM UK sector file + OurAirports.

Usage: python build_egkk.py <UK-Sector-File dir> <ourairports-data dir> <out.json>
"""
import csv
import json
import math
import re
import sys
from pathlib import Path

SF, OA, OUT = map(Path, sys.argv[1:4])

COORD = re.compile(r"([NS])(\d{3})\.(\d{2})\.(\d{2}\.\d+)\s+([EW])(\d{3})\.(\d{2})\.(\d{2}\.\d+)")


def dms(h, d, m, s):
    v = int(d) + int(m) / 60 + float(s) / 3600
    return -v if h in "SW" else v


def parse_coords(text):
    return [(dms(*g[0:4]), dms(*g[4:8])) for g in COORD.findall(text)]


# ---------- runways (landing thresholds from sector file) ----------
rwy_lines = (SF / "Airports/EGKK/Runway.txt").read_text().split("\n")
runways = {}
for line in rwy_lines:
    parts = line.split()
    if len(parts) < 4:
        continue
    a, b, hdg_a, hdg_b = parts[:4]
    (lat_a, lon_a), (lat_b, lon_b) = parse_coords(line)
    runways[a] = {"thr": (lat_a, lon_a), "far": (lat_b, lon_b), "mag_hdg": int(hdg_a)}
    runways[b] = {"thr": (lat_b, lon_b), "far": (lat_a, lon_a), "mag_hdg": int(hdg_b)}

# OurAirports physical ends + elevations for cross-check
oa = {}
with open(OA / "runways.csv") as f:
    for r in csv.DictReader(f):
        if r["airport_ident"] == "EGKK":
            for end in ("le", "he"):
                oa[r[f"{end}_ident"]] = {
                    "lat": float(r[f"{end}_latitude_deg"]),
                    "lon": float(r[f"{end}_longitude_deg"]),
                    "elev_ft": int(r[f"{end}_elevation_ft"]),
                    "disp_thr_ft": int(r[f"{end}_displaced_threshold_ft"] or 0),
                    "hdg_true": float(r[f"{end}_heading_degT"]),
                }

ORIGIN = runways["26L"]["thr"]  # local projection origin: RWY 26L landing threshold
COSLAT = math.cos(math.radians(ORIGIN[0]))


def to_xy(lat, lon):
    return (round((lon - ORIGIN[1]) * 60 * COSLAT, 4), round((lat - ORIGIN[0]) * 60, 4))


def bearing(p, q):
    """True initial bearing p->q (deg), small-area approximation is fine but use spherical."""
    la1, lo1, la2, lo2 = map(math.radians, (*p, *q))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def dist_nm(p, q):
    la1, lo1, la2, lo2 = map(math.radians, (*p, *q))
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def dist_to_oa_end_ft(rwy):
    """Distance from sector-file landing threshold to OurAirports physical runway end."""
    e = oa[rwy]
    return dist_nm(runways[rwy]["thr"], (e["lat"], e["lon"])) * 6076.12


rwy_out = {}
for name, r in runways.items():
    course = bearing(r["thr"], r["far"])  # direction of landing
    rwy_out[name] = {
        "threshold": {"lat": round(r["thr"][0], 6), "lon": round(r["thr"][1], 6), "xy_nm": to_xy(*r["thr"])},
        "landing_course_true": round(course, 1),
        "localiser_course_true": round(course, 1),
        "magnetic_heading_published": r["mag_hdg"],
        "elevation_ft": oa.get(name, {}).get("elev_ft"),
        "threshold_to_threshold_m": round(dist_nm(r["thr"], r["far"]) * 1852),
        "crosscheck": {
            "ourairports_physical_end": {"lat": oa[name]["lat"], "lon": oa[name]["lon"]},
            "ourairports_displaced_threshold_ft": oa[name]["disp_thr_ft"],
            "sectorfile_thr_to_oa_end_ft": round(dist_to_oa_end_ft(name)),
        },
    }

# ---------- fixes / navaids ----------
fix_sources = [
    SF / "Airports/EGKK/Fixes.txt",
    SF / "Navaids/FIXES_UK.txt",
    SF / "Navaids/VOR_UK.txt",
]
fixes = {}
for src in fix_sources:
    for line in src.read_text(errors="ignore").splitlines():
        if not line or line.startswith(";"):
            continue
        name = line.split()[0]
        c = parse_coords(line)
        if c and name not in fixes:
            fixes[name] = c[0]

# ---------- STARs (26L only, unique routes) ----------
stars = {}
for line in (SF / "Airports/EGKK/Stars.txt").read_text().splitlines():
    if line.startswith("STAR:EGKK:26L:"):
        _, _, _, name, route = line.split(":", 4)
        stars[name] = route.split()

needed = sorted({f for r in stars.values() for f in r} | {"FI26L", "FI08R", "WILLO", "TIMBA"})
fix_out = {}
missing = []
for n in needed:
    if n in fixes:
        lat, lon = fixes[n]
        fix_out[n] = {"lat": round(lat, 6), "lon": round(lon, 6), "xy_nm": to_xy(lat, lon)}
    else:
        missing.append(n)

# ---------- airspace (video map) ----------
def segments(path, label_filter=None):
    segs, current = {}, None
    for line in Path(path).read_text().splitlines():
        if line.startswith(";"):
            current = line[1:].strip()
            continue
        c = parse_coords(line)
        if len(c) == 2 and current:
            segs.setdefault(current, []).append([to_xy(*c[0]), to_xy(*c[1])])
    return segs

airspace = segments(SF / "Airports/EGKK/Airspace.txt")
centrelines = segments(SF / "Airports/EGKK/Centreline.txt")

# ---------- positions ----------
positions = {}
for line in (SF / "Airports/EGKK/Positions.txt").read_text().splitlines():
    p = line.split(":")
    if len(p) > 3 and p[0] in ("EGKK_APP", "EGKK_F_APP", "EGKK_TWR"):
        positions[p[0]] = {"name": p[1], "freq": p[2]}

data = {
    "_meta": {
        "airport": "EGKK London Gatwick",
        "projection": "flat local: x = NM east, y = NM north of RWY 26L landing threshold",
        "origin": {"lat": round(ORIGIN[0], 6), "lon": round(ORIGIN[1], 6)},
        "sources": [
            "VATSIM UK Sector File (github.com/VATSIM-UK/UK-Sector-File, GPL-3.0) - fixes, STAR routes, airspace, runway thresholds",
            "OurAirports open data (public domain) - runway physical ends, elevations, displaced thresholds",
        ],
        "warning": "SIMULATION USE ONLY. Not for real-world navigation. Verify against current UK AIP (NATS) before relying on any value.",
        "missing_fixes": missing,
    },
    "runways": rwy_out,
    "glideslope_deg": 3.0,
    "glideslope_ft_per_nm": round(math.tan(math.radians(3.0)) * 6076.12),
    "fixes": fix_out,
    "stars_26L": stars,
    "holds": {
        "WILLO": {"note": "west stack; verify inbound course/turn direction/levels in AIP"},
        "TIMBA": {"note": "east stack; verify inbound course/turn direction/levels in AIP"},
    },
    "video_map": {"airspace": airspace, "centrelines": centrelines},
    "positions": positions,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=1))
print("wrote", OUT, "| fixes:", len(fix_out), "| missing:", missing, "| stars:", len(stars))
for k, v in rwy_out.items():
    print(k, v["landing_course_true"], "T | thr->thr", v["threshold_to_threshold_m"], "m | crosscheck", v["crosscheck"])
