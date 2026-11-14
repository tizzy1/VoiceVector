"""Read-only live-traffic snapshot import (SIM_SPEC §3.7).

Two steps, kept apart on purpose:
  1. `normalize_vatsim(raw, fetched_at)` turns one VATSIM v3 network-data document into
     neutral `targets` (plain dicts). It keeps only what the sim needs and DROPS personal
     data (cid, name, server, logon time). No network access happens here: the session's
     fetcher (Bhavin) downloads the JSON and calls this.
  2. `World.import_live(targets, source, mode)` -> `import_targets()` filters, classifies
     and converts each target into an ordinary SIMULATED AircraftState. From then on the
     simulator owns it; nothing is ever sent back to VATSIM or any real aircraft.

⚠ VERIFY before enabling a provider (data/VERIFY.md): VATSIM's User Agreement, Commercial
Use and Data Protection policies are PDFs that have not been read yet. Feed facts used
here (vatsim.dev, status.json, read 14 Nov 2026): v3 JSON at
https://data.vatsim.net/v3/vatsim-data.json, regenerated every 15 s; pilot heading is
MAGNETIC; altitude ft MSL; groundspeed kt.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from server.sim.aircraft import AircraftState
from server.sim.geo import dist, norm_hdg
from server.sim.kinematics import tas_of

if TYPE_CHECKING:
    from server.sim.world import World

VATSIM_ATTRIBUTION = "Traffic snapshot: VATSIM network data (data.vatsim.net), read-only"
SOURCES = {"vatsim": VATSIM_ATTRIBUTION}

# ---- filters (⚠ VERIFY: SIM_SPEC §3.7 defaults) ----
RADIUS_NM = 40.0
MIN_ALT_FT, MAX_ALT_FT = 1500.0, 16000.0
MIN_GS_KT = 60.0                      # below this it is on the ground
MAX_TARGET_AGE_S = 120.0              # a pilot not updated for this long is stale
MAX_SNAPSHOT_AGE_S = 600.0            # whole document older than this: refuse
STALE_SNAPSHOT_S = 60.0               # older than this: import but flag as stale
# ---- initial-state heuristics ----
FINAL_CAPTURE_NM = 15.0               # aligned with the localiser inside this: established
FINAL_XTE_NM, FINAL_TRACK_DEG = 1.5, 30.0
VECTOR_RADIUS_NM = 12.0               # arrivals this close start on present heading
SID_INITIAL_ALT_FT = 6000.0
MAX_IAS_BELOW_FL100 = 250.0

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")

# ICAO type -> nearest type in data/perf.json (exact matches are used first)
TYPE_FAMILIES: list[tuple[str, str]] = [
    (r"^A318|^A319|^A19N", "A319"), (r"^A320|^A20N", "A320"), (r"^A321", "A321"),
    (r"^A21N", "A21N"), (r"^B73|^B37M|^B38M|^B39M|^B3XM", "B738"),
    (r"^B77|^B74|^A38|^A34", "B77W"), (r"^B78|^A35|^B76", "B789"), (r"^A33|^A30|^A31", "A333"),
    (r"^E1[79]|^E2|^CRJ|^AT[47]|^DH8|^BCS|^A22|^SB|^RJ", "E190"),
    (r"^C1|^C2|^PA|^SR2|^DA[24]|^P28|^BE|^M20|^TB", "C172"),
]


# =========================================================================================
def _parse_time(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def normalize_vatsim(raw: dict[str, Any], fetched_at: Optional[datetime] = None,
                     ) -> dict[str, Any]:
    """VATSIM v3 document -> {"source", "source_time", "fetched_at", "targets", "rejected"}.

    Each target: callsign, lat, lon, alt_ft, gs_kt, hdg_mag, actype, dep, arr, route,
    squawk, flight_rules, last_updated (ISO). Personal fields are not copied."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    general = raw.get("general") or {}
    targets, rejected = [], []
    for p in raw.get("pilots") or []:
        cs = str(p.get("callsign", "")).strip().upper()
        try:
            lat, lon = float(p["latitude"]), float(p["longitude"])
            alt, gs = float(p["altitude"]), float(p["groundspeed"])
            hdg = float(p.get("heading", 0))
        except (KeyError, TypeError, ValueError):
            rejected.append({"callsign": cs or "?", "reason": "invalid_record"})
            continue
        fp = p.get("flight_plan") or {}
        targets.append({
            "callsign": cs, "lat": lat, "lon": lon, "alt_ft": alt, "gs_kt": gs, "hdg_mag": hdg,
            "actype": str(fp.get("aircraft_short") or "").upper(),
            "dep": str(fp.get("departure") or "").upper(),
            "arr": str(fp.get("arrival") or "").upper(),
            "route": str(fp.get("route") or ""),
            "flight_rules": str(fp.get("flight_rules") or ""),
            "squawk": str(p.get("transponder") or ""),
            "last_updated": p.get("last_updated"),
        })
    return {"source": "vatsim", "source_time": general.get("update_timestamp"),
            "fetched_at": fetched_at.isoformat(), "targets": targets, "rejected": rejected}


# =========================================================================================
def map_type(actype: str, perf: dict[str, Any]) -> tuple[str, bool]:
    """(perf type, exact?) — unknown types fly as an A320."""
    t = actype.upper()
    if t in perf and not t.startswith("_"):
        return t, True
    for pattern, model in TYPE_FAMILIES:
        if re.match(pattern, t) and model in perf:
            return model, False
    return "A320", False


def _mag_variation(w: "World") -> float:
    """True = magnetic + variation, from the active runway's published magnetic heading."""
    rwy = w.egkk["runways"][w.runway]
    return float(rwy["landing_course_true"]) - float(rwy["magnetic_heading_published"])


def _sid_from_route(w: "World", route: str) -> Optional[str]:
    sids = w.egkk["procedures"]["SID"].get(w.runway, {})
    for tok in re.split(r"[\s/.]+", route.upper()):
        if tok in sids and sids[tok].get("resolved", True) and tok != "BIG":
            return tok
    return None


def import_targets(w: "World", snapshot: dict[str, Any], mode: str = "snapshot",
                   now: Optional[datetime] = None) -> dict[str, Any]:
    """Convert normalised targets into simulated aircraft. Returns the import status."""
    source = str(snapshot.get("source", "unknown"))
    status: dict[str, Any] = {
        "source": source, "mode": mode, "ok": False, "stale": False,
        "fetched_at": snapshot.get("fetched_at"), "source_time": snapshot.get("source_time"),
        "imported": [], "skipped": list(snapshot.get("rejected") or []),
        "attribution": SOURCES.get(source, f"Traffic snapshot: {source}, read-only"),
        "error": None, "sim_t": w.t,
    }
    if mode != "snapshot":
        status["error"] = "mode_not_supported"          # context (display-only) mode: not built
        return status
    now = now or _parse_time(snapshot.get("fetched_at")) or datetime.now(timezone.utc)
    src_time = _parse_time(snapshot.get("source_time"))
    if src_time is not None:
        age = (now - src_time).total_seconds()
        if age > MAX_SNAPSHOT_AGE_S:
            status["error"] = "snapshot_too_old"
            return status
        status["stale"] = age > STALE_SNAPSHOT_S
    targets = snapshot.get("targets") or []
    if not targets:
        status["error"] = "empty"
        return status

    var = _mag_variation(w)
    for tg in sorted(targets, key=lambda t: str(t.get("callsign", ""))):
        reason, ac = _convert(w, tg, var, now)
        if ac is None:
            status["skipped"].append({"callsign": tg.get("callsign", "?"), "reason": reason})
            continue
        w.add_aircraft(ac, checkin=ac.flow == "ARR")
        if ac.flow == "DEP":
            w.pilot_call(ac, "checkin", alt=round(ac.alt / 100) * 100, passing=True,
                         cleared_alt=round(ac.cleared_alt), route=ac.route_name)
        status["imported"].append({"callsign": ac.callsign, "flow": ac.flow, "phase": ac.phase,
                                   "actype": ac.actype, "type_exact": reason == "exact_type"})
    status["ok"] = True
    counts: dict[str, int] = {}
    for s in status["skipped"]:
        counts[s["reason"]] = counts.get(s["reason"], 0) + 1
    status["counts"] = {"imported": len(status["imported"]), "skipped": len(status["skipped"]),
                        "skipped_by_reason": counts}
    return status


def _convert(w: "World", tg: dict[str, Any], var: float, now: datetime,
             ) -> tuple[str, Optional[AircraftState]]:
    cs = str(tg.get("callsign", "")).upper()
    if not CALLSIGN_RE.match(cs):
        return "invalid_callsign", None
    if cs in w.aircraft:
        return "callsign_in_use", None
    seen = _parse_time(tg.get("last_updated"))
    if seen is not None and (now - seen).total_seconds() > MAX_TARGET_AGE_S:
        return "stale", None
    try:
        lat, lon = float(tg["lat"]), float(tg["lon"])
        alt, gs, hdg_mag = float(tg["alt_ft"]), float(tg["gs_kt"]), float(tg["hdg_mag"])
    except (KeyError, TypeError, ValueError):
        return "invalid_record", None
    if gs < MIN_GS_KT:
        return "on_ground", None
    x, y = w.proj.to_xy(lat, lon)
    if dist(x, y, *w.thr_xy) > RADIUS_NM:
        return "out_of_area", None
    if not MIN_ALT_FT <= alt <= MAX_ALT_FT:
        return "out_of_level_band", None
    dep, arr = str(tg.get("dep", "")), str(tg.get("arr", ""))
    if arr == "EGKK":
        flow = "ARR"
    elif dep == "EGKK":
        flow = "DEP"
    else:
        return "not_gatwick_traffic", None

    actype, exact = map_type(str(tg.get("actype", "")), w.perf)
    hdg = norm_hdg(hdg_mag + var)
    ias = min(gs / (1.0 + 0.02 * alt / 1000.0), MAX_IAS_BELOW_FL100 if alt < 10000 else 320.0)
    ias = max(ias, float(w.perf[actype]["vapp_kt"]))
    level = round(alt / 100.0) * 100.0
    tas = tas_of(ias, alt)
    squawk = str(tg.get("squawk") or "2000")
    persona = ("calm_longhaul" if w.perf[actype]["wake"] == "H" else
               w.generator.rng.choice(("crisp_airline", "crisp_airline", "rushed", "student")))
    base: dict[str, Any] = dict(
        callsign=cs, spoken=w.spoken(cs), spoken_plain=w.spoken_plain(cs), actype=actype,
        wake=w.perf[actype]["wake"], persona=persona, flow=flow, x=x, y=y, alt=alt, hdg=hdg,
        track=hdg, ias=ias, tas=tas, gs=tas, tgt_hdg=hdg, cleared_alt=level, qnh=w.qnh,
        squawk=squawk if re.fullmatch(r"[0-7]{4}", squawk) else "2000", spawn_t=w.t,
    )
    if flow == "ARR":
        ac = _arrival(w, base)
    else:
        ac = _departure(w, base, str(tg.get("route", "")))
    return ("exact_type" if exact else "mapped_type"), ac


def _arrival(w: "World", base: dict[str, Any]) -> AircraftState:
    ac = AircraftState(phase="VECTORS", lat_mode="HDG", **base)
    c = math.radians(w.loc_course)
    px, py = ac.x - w.thr_xy[0], ac.y - w.thr_xy[1]
    along = -(px * math.sin(c) + py * math.cos(c))
    xte = px * math.cos(c) - py * math.sin(c)
    trk_err = abs((ac.hdg - w.loc_course + 180.0) % 360.0 - 180.0)
    if 0 < along <= FINAL_CAPTURE_NM and abs(xte) < FINAL_XTE_NM and trk_err < FINAL_TRACK_DEG:
        ac.lat_mode, ac.phase, ac.established = "LOC", "APPROACH", True
        ac.loc_armed = ac.gs_armed = True
        ac.approach_cleared = {"type": "ILS", "runway": w.runway}
        ac.cleared_alt = max(ac.cleared_alt, 2000.0)
    elif dist(ac.x, ac.y, *w.thr_xy) > VECTOR_RADIUS_NM:
        hold = min(sorted(w.hold_fixes), key=lambda f: dist(ac.x, ac.y, *w.fix_xy(f)))
        ac.lat_mode, ac.phase, ac.route, ac.route_idx = "LNAV", "STAR", [hold], 0
    return ac


def _departure(w: "World", base: dict[str, Any], route: str) -> AircraftState:
    sid = _sid_from_route(w, route)
    ac = AircraftState(phase="SID", lat_mode="HDG", **base)
    ac.route_name = sid
    ac.released_t, ac.sid_started, ac.dep_checked_in = w.t, True, True
    ac.on_frequency, ac.in_jurisdiction, ac.freq_unit = True, True, "director"
    if ac.alt < SID_INITIAL_ALT_FT:
        ac.cleared_alt = SID_INITIAL_ALT_FT
    if sid:
        fixes = [f for f in w.egkk["procedures"]["SID"][w.runway][sid]["route"]
                 if f in w.egkk["fixes"]]
        if fixes:
            nearest = min(range(len(fixes)), key=lambda i: dist(ac.x, ac.y, *w.fix_xy(fixes[i])))
            ac.lat_mode, ac.route, ac.route_idx = "LNAV", fixes, nearest
    return ac
