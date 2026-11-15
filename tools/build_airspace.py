"""Build data/egkk.json (v2): full Gatwick airspace pack for VoiceVector.

Usage: python -I build_airspace.py <UK-Sector-File> <ourairports-data> <existing egkk.json> <out.json>
Adds: SIDs/STARs for 26L + 08R (RNAV and conventional), all referenced fixes,
London TMA / Worthing CTA / Gatwick CTR+CTA with vertical limits,
Gatwick Director sector lines, RMA polygons (display only), runways.
"""
import json
import math
import re
import sys
from pathlib import Path

SF, OA, OLD, OUT = map(Path, sys.argv[1:5])
C = re.compile(r"([NS])(\d{3})\.(\d{2})\.(\d{2}\.\d+)[\s:]+([EW])(\d{3})\.(\d{2})\.(\d{2}\.\d+)")


def dms(h, d, m, s):
    v = int(d) + int(m) / 60 + float(s) / 3600
    return -v if h in "SW" else v


def coords(text):
    return [(dms(*g[:4]), dms(*g[4:])) for g in C.findall(text)]


old = json.loads(OLD.read_text())
O = old["_meta"]["origin"]
COS = math.cos(math.radians(O["lat"]))


def xy(la, lo):
    return [round((lo - O["lon"]) * 60 * COS, 3), round((la - O["lat"]) * 60, 3)]


# ---------- all fixes / navaids ----------
fix_files = [SF / "Airports/EGKK/Fixes.txt"] + sorted((SF / "Navaids").glob("*.txt"))
fix_files += sorted((SF / "Navaids/Fixes_Non-UK").glob("*.txt")) if (SF / "Navaids/Fixes_Non-UK").is_dir() else []
fixes = {}
for f in fix_files:
    for line in f.read_text(errors="ignore").splitlines():
        if not line.strip() or line.startswith(";"):
            continue
        name = line.split()[0]
        c = coords(line)
        if c and name not in fixes:
            fixes[name] = c[0]

# ---------- procedures ----------
procs = {"SID": {}, "STAR": {}}
for kind, fname in (("SID", "Sids.txt"), ("STAR", "Stars.txt")):
    for line in (SF / f"Airports/EGKK/{fname}").read_text().splitlines():
        p = line.strip().split(":", 4)
        if len(p) == 5 and p[0] == kind and p[1] == "EGKK" and p[2] in ("26L", "08R"):
            procs[kind].setdefault(p[2], {})[p[3]] = p[4].split()

used, missing = set(), set()
for kind in procs:
    for rwy in procs[kind]:
        for name, route in list(procs[kind][rwy].items()):
            ok = [f for f in route if f in fixes]
            missing |= {f for f in route if f not in fixes}
            used |= set(ok)
            procs[kind][rwy][name] = {"route": route, "resolved": len(ok) == len(route)}

# ---------- airspace polygons (line segments grouped by ';' header) ----------
def sectioned_segments(path, label_prefix=None):
    out, cur = {}, None
    for line in Path(path).read_text(errors="ignore").splitlines():
        if line.startswith(";"):
            cur = line[1:].strip()
            continue
        c = coords(line)
        if cur and len(c) == 2:
            out.setdefault(cur, []).append([xy(*c[0]), xy(*c[1])])
    return out


LIM = re.compile(r"Class ([A-G])\s+(SFC|FL\d+|\d+)-(FL\d+|\d+)")


def limits(name):
    m = LIM.search(name)
    if not m:
        return None, None, None
    to_ft = lambda v: 0 if v == "SFC" else (int(v[2:]) * 100 if v.startswith("FL") else int(v))
    return m.group(1), to_ft(m.group(2)), to_ft(m.group(3))


airspace = []
for path, group in ((SF / "ARTCC/High/London TMA.txt", "TMA"),
                    (SF / "ARTCC/High/Worthing CTA.txt", "CTA")):
    for name, segs in sectioned_segments(path).items():
        cls, base, top = limits(name)
        airspace.append({"name": name.split(" Class")[0], "type": group, "class": cls,
                         "base_ft": base, "top_ft": top, "segments": segs})
for name, segs in sectioned_segments(SF / "Airports/EGKK/Airspace.txt").items():
    is_ctr = "CTR" in name
    airspace.append({"name": name, "type": "CTR" if is_ctr else "CTA", "class": "D",
                     "base_ft": 0 if is_ctr else 1500, "top_ft": 2500, "segments": segs,
                     "note": "Vertical limits: CTR SFC-2500, CTA base 1500 per sector file; VERIFY vs AIP"})

# keep only segments within 70 NM of origin (TMA is huge)
def near(seg, r=70):
    return any(math.hypot(*p) <= r for p in seg)
for a in airspace:
    a["segments"] = [s for s in a["segments"] if near(s)]
airspace = [a for a in airspace if a["segments"]]

# ---------- Director sector lines ----------
sectors, cur = {}, None
for line in (SF / "Airports/EGKK/Sector_Boundaries.txt").read_text().splitlines():
    if line.startswith("SECTORLINE:"):
        cur = line.split(":", 1)[1].strip()
        sectors[cur] = []
    elif line.startswith("CIRCLE_SECTORLINE") or not line.strip():
        cur = None if line.startswith("CIRCLE") else cur
    elif cur and line.startswith("COORD:"):
        c = coords(line)
        if c:
            sectors[cur].append(xy(*c[0]))
sector_limits = {}
for line in (SF / "Airports/EGKK/Sector_Ownership.txt").read_text().splitlines():
    if line.startswith("SECTOR:"):
        _, n, lo, hi = line.split(":")[:4]
        sector_limits[n] = [int(lo), int(hi)]

# ---------- RMA polygons (boundaries only; values not in sector file) ----------
rma, cur = {}, None
for line in (SF / "Airports/EGKK/RMA.txt").read_text().splitlines():
    if line and not line.startswith(" "):
        m = re.match(r"(.*?)\s+[NS]\d{3}\.", line)
        cur = (m.group(1) if m else line).strip()
        rma[cur] = []
    c = coords(line)
    real = [p for p in c if abs(p[0]) < 90]
    if cur and len(real) == 2:
        rma[cur].append([xy(*real[0]), xy(*real[1])])

# ---------- assemble ----------
fix_out = {n: {"lat": round(fixes[n][0], 6), "lon": round(fixes[n][1], 6), "xy_nm": xy(*fixes[n])}
           for n in sorted(used | set(old["fixes"]))  if n in fixes}

data = dict(old)
data["_meta"] = dict(old["_meta"])
data["_meta"].update({
    "version": 2,
    "sources": old["_meta"]["sources"] + [
        "VATSIM UK Sector File: London TMA, Worthing CTA, Gatwick sectors, RMA boundaries (GPL-3.0)"],
    "missing_fixes": sorted(missing),
    "notes": [
        "SIDs/STARs: full route strings for 26L and 08R; 'resolved' false if any point lacks coordinates.",
        "No altitude/speed constraints on procedures (sector file has routes only).",
        "RMA polygons are boundaries only: minimum altitudes per area are NOT in the source - set in rules.",
        "Airspace limits parsed from sector-file headers; verify against current UK AIP.",
    ],
})
data["fixes"] = fix_out
data["procedures"] = procs
data.pop("stars_26L", None)
data.pop("sids_26L", None)
data["airspace"] = airspace
data["sectors"] = {"lines": sectors, "limits_ft": {k: v for k, v in sector_limits.items() if k.startswith("KK")}}
data["rma"] = rma
OUT.write_text(json.dumps(data, indent=1))

n_res = sum(v["resolved"] for k in procs for r in procs[k] for v in procs[k][r].values())
n_all = sum(len(procs[k][r]) for k in procs for r in procs[k])
print(f"fixes {len(fix_out)} | procedures resolved {n_res}/{n_all} | missing {len(missing)}: {sorted(missing)[:40]}")
print("airspace:", [(a['name'], a['base_ft'], a['top_ft'], len(a['segments'])) for a in airspace])
print("sectors:", {k: len(v) for k, v in sectors.items()}, "| rma:", {k: len(v) for k, v in rma.items()})
