"""Departures + runway manager (SIM_SPEC §1.5–1.7, ASSIGNMENT_TUDOR SHOULD 10).

DEP lifecycle: QUEUED --release--> TAKEOFF --Vr, 35 ft--> SID --"contact London"--> HANDED_OFF.
The runway manager plays Tower: it releases the next departure only when the runway is free,
no arrival is inside `departure_release_gap_nm`, and the previous departure is far enough
ahead (same-route / diverging / wake minima from traffic.json). All state lives on the World
(or on the aircraft) so the pilot engine's shadow copies of the World stay independent.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

from server.sim.aircraft import AircraftState
from server.sim.geo import dist, hdg_diff, norm_hdg

if TYPE_CHECKING:
    from server.sim.world import World

FIRST_SIDS = ("BOGNA1X", "NOVMA1X", "SFD1X")    # SIDs modelled first (ASSIGNMENT_TUDOR §10)
SID_INITIAL_ALT_FT = 6000.0                      # ⚠ VERIFY: SID initial altitude
TAKEOFF_ACCEL_KT_S = 3.0
LIFTOFF_AGL_FT = 35.0
RWY_TRACK_UNTIL_AGL_FT = 1500.0
ACCEL_ABOVE_FT = 3000.0
CLIMB_IAS_MID = 220.0                            # 1,500 ft .. 3,000 ft
CLIMB_IAS_HIGH = 250.0                           # above 3,000 ft
CHECKIN_PASSING_FT = 2000.0
REQUEST_HIGHER_S = 60.0
NOT_TRANSFERRED_NM = 25.0
REMOVE_NM = 40.0
DIVERGING_DEG = 45.0
DEP_PERSONAS = (("crisp_airline", 55), ("calm_longhaul", 15), ("rushed", 20), ("student", 10))


def sids(w: "World") -> list[str]:
    available = w.egkk["procedures"]["SID"].get(w.runway, {})
    first = [s for s in FIRST_SIDS if s in available]
    if first:
        return first
    return [s for s in w.traffic.get("departure_sids", {}) if s in available]


def sid_route(w: "World", sid: str) -> list[str]:
    route = w.egkk["procedures"]["SID"].get(w.runway, {}).get(sid, {}).get("route", [])
    return [f for f in route if f in w.egkk["fixes"]]


def vr(w: "World", ac: AircraftState) -> float:
    return float(w.perf[ac.actype]["vapp_kt"]) + 10.0       # SIM_SPEC §1.6: Vr = Vapp + 10


def make_departure(w: "World", callsign: str, actype: str, sid: str, persona: str,
                   ) -> AircraftState:
    x, y = w.thr_xy
    hdg = w.loc_course
    return AircraftState(
        callsign=callsign, spoken=w.spoken(callsign), spoken_plain=w.spoken_plain(callsign),
        actype=actype, wake=w.perf[actype]["wake"], persona=persona, flow="DEP",
        phase="QUEUED", x=x, y=y, alt=w.rwy_elev, hdg=hdg, track=hdg, ias=0.0, tas=0.0,
        gs=0.0, tgt_hdg=hdg, lat_mode="RWY_TRACK", route=sid_route(w, sid), route_name=sid,
        cleared_alt=w.rwy_elev, qnh=w.qnh, spawn_t=w.t, on_frequency=False,
        in_jurisdiction=False, freq_unit="tower",
    )


class DepartureGenerator:
    """Seeded departure demand: adds aircraft to the holding point queue (phase QUEUED)."""

    def __init__(self, world: "World", seed: int, first_spawn_s: float) -> None:
        self.w = world
        self.rng = random.Random(seed + 2)
        self.next_t = first_spawn_s

    def rate_per_hr(self) -> float:
        w = self.w
        presets = w.traffic["difficulty_presets"]
        name = w.scenario
        if name == "ramp":
            ramp = presets["ramp"]
            lo, hi = presets[ramp["start"]]["dep_per_hr"], presets[ramp["end"]]["dep_per_hr"]
            return lo + (hi - lo) * min(1.0, w.t / (ramp["over_min"] * 60.0))
        if name in presets and "dep_per_hr" in presets[name]:
            return float(presets[name]["dep_per_hr"])
        return float(w.settings.dep_per_hr)

    def tick(self) -> None:
        rate = self.rate_per_hr()
        if rate <= 0:
            return
        while self.w.t >= self.next_t:
            self.spawn()
            self.next_t += self.rng.uniform(0.6, 1.4) * 3600.0 / rate

    def spawn(self, sid: Optional[str] = None) -> Optional[AircraftState]:
        w, rng = self.w, self.rng
        options = sids(w)
        if not options:
            return None
        if sid not in options:
            sid = rng.choice(options)
        fleet = w.traffic["fleet_mix"]
        airline = rng.choices(fleet, weights=[f["weight"] for f in fleet])[0]
        types = airline["types"]
        actype = rng.choices(list(types), weights=list(types.values()))[0]
        persona = rng.choices([p for p, _ in DEP_PERSONAS],
                              weights=[n for _, n in DEP_PERSONAS])[0]
        callsign = w.generator._callsign(airline["airline"])
        ac = make_departure(w, callsign, actype, sid, persona)
        w.add_aircraft(ac, checkin=False)
        return ac


# --------------------------------------------------------------- runway manager
def queue(w: "World") -> list[AircraftState]:
    return sorted((a for a in w.aircraft.values() if a.phase == "QUEUED"),
                  key=lambda a: (a.release_not_before, a.spawn_t, a.callsign))


def blocked_reason(w: "World", nxt: AircraftState) -> Optional[str]:
    """Why the next departure can't go yet (None = release now)."""
    from server.sim.modes import loc_geometry
    sep = w.traffic["separation"]
    occ = sep["runway_occupancy_s"]
    if w.t < nxt.release_not_before:
        return "scheduled"
    for a in w.aircraft.values():
        if a.phase == "TAKEOFF" and a.alt < w.rwy_elev + LIFTOFF_AGL_FT:
            return "runway occupied"
        if a.phase == "LANDED" and a.landed_t is not None and w.t - a.landed_t < occ["arrival"]:
            return "runway occupied"
    if w.last_landing_t is not None and w.t - w.last_landing_t < occ["arrival"]:
        return "runway occupied"
    if w.last_takeoff_t is not None and w.t - w.last_takeoff_t < occ["departure"]:
        return "runway occupied"
    gap = float(sep["departure_release_gap_nm"])
    for a in w.aircraft.values():
        if a.flow != "ARR" or a.phase not in ("APPROACH", "FINAL"):
            continue
        along, xte = loc_geometry(w, a)
        if 0 < along < gap and abs(xte) < 2.0:
            return f"arrival inside {gap:g} NM"
    lead = w.aircraft.get(w.last_departure or "")
    if lead is not None and lead.flow == "DEP" and lead.phase != "QUEUED":
        need = required_gap_nm(w, lead, nxt)
        if dist(lead.x, lead.y, *w.thr_xy) < need:
            return f"{need:g} NM behind {lead.callsign}"
    return None


def required_gap_nm(w: "World", lead: AircraftState, follow: AircraftState) -> float:
    sep = w.traffic["separation"]
    need = float(sep["departure_diverging_45deg_min"])
    if lead.route_name == follow.route_name or not _diverging(w, lead, follow):
        need = max(need, float(sep["departure_same_route_min"]))
    wake = sep.get("departure_wake_min", {}).get(f"{lead.wake}>{follow.wake}")
    if wake is not None:
        need = max(need, float(wake))
    return need


def _diverging(w: "World", a: AircraftState, b: AircraftState) -> bool:
    if not a.route or not b.route:
        return False
    from server.sim.geo import bearing
    ta = bearing(*w.thr_xy, *w.fix_xy(a.route[0]))
    tb = bearing(*w.thr_xy, *w.fix_xy(b.route[0]))
    return abs(hdg_diff(ta, tb)) >= DIVERGING_DEG


def manage(w: "World") -> None:
    """Release the next departure if allowed (once per tick)."""
    q = queue(w)
    if not q:
        return
    nxt = q[0]
    if blocked_reason(w, nxt) is None:
        nxt.phase, nxt.released_t = "TAKEOFF", w.t
        nxt.tgt_hdg, nxt.lat_mode = w.loc_course, "RWY_TRACK"
        w.last_departure, w.last_takeoff_t = nxt.callsign, w.t


def ground_roll(w: "World", ac: AircraftState, dt: float) -> None:
    """Takeoff roll: accelerate 3 kt/s on the runway heading, rotate at Vr."""
    import math
    ac.ias = min(vr(w, ac), ac.ias + TAKEOFF_ACCEL_KT_S * dt)
    ac.tas = ac.gs = ac.ias
    ac.hdg = ac.track = w.loc_course
    d = ac.gs * dt / 3600.0
    ac.x += d * math.sin(math.radians(ac.track))
    ac.y += d * math.cos(math.radians(ac.track))
    if ac.ias >= vr(w, ac):                         # rotate
        ac.cleared_alt, ac.vert_mode = SID_INITIAL_ALT_FT, "LVL_CHG"
        ac.tgt_ias = None
        ac.alt = w.rwy_elev + 1.0
        w.emit("takeoff", ac.callsign, {"runway": w.runway, "sid": ac.route_name})


def on_ground(w: "World", ac: AircraftState) -> bool:
    return ac.phase == "QUEUED" or (ac.phase == "TAKEOFF" and ac.alt <= w.rwy_elev)


def guide(w: "World", ac: AircraftState) -> None:
    """Departure-specific guidance, run after modes.guide and before kinematics."""
    agl = ac.alt - w.rwy_elev
    if ac.phase == "TAKEOFF" and agl >= LIFTOFF_AGL_FT:
        ac.phase = "SID"
    if ac.lat_mode == "RWY_TRACK" and agl >= RWY_TRACK_UNTIL_AGL_FT and not ac.sid_started:
        ac.sid_started = True
        if ac.route:
            ac.lat_mode, ac.route_idx = "LNAV", 0
    if ac.tgt_ias is None and ac.auto_ias is None:              # departure speed schedule
        if agl < RWY_TRACK_UNTIL_AGL_FT:
            ac.auto_ias = vr(w, ac) + 15.0                       # ~ V2 + 10
        elif ac.alt < ACCEL_ABOVE_FT:
            ac.auto_ias = CLIMB_IAS_MID
        else:
            ac.auto_ias = CLIMB_IAS_HIGH


def after_step(w: "World", ac: AircraftState) -> None:
    """Pilot calls and transfer bookkeeping once the departure has moved this tick."""
    if ac.phase in ("QUEUED", "TAKEOFF") and ac.alt <= w.rwy_elev:
        return
    if not ac.dep_checked_in and ac.alt >= CHECKIN_PASSING_FT and ac.phase != "HANDED_OFF":
        ac.dep_checked_in, ac.on_frequency = True, not ac.radio_failed
        ac.in_jurisdiction, ac.freq_unit = True, "director"
        if ac.on_frequency:
            w.pilot_call(ac, "checkin", alt=round(CHECKIN_PASSING_FT), passing=True,
                         cleared_alt=round(ac.cleared_alt), route=ac.route_name)
    level = abs(ac.alt - ac.cleared_alt) < 1.0 and ac.vert_mode == "ALT"
    if level and ac.cleared_alt == SID_INITIAL_ALT_FT and ac.on_frequency:
        if ac.levelled_t is None:
            ac.levelled_t = w.t
        elif not ac.higher_requested and w.t - ac.levelled_t > REQUEST_HIGHER_S:
            ac.higher_requested = True
            w.pilot_call(ac, "request", what="higher")
    elif not level:
        ac.levelled_t = None


def dist_from_airport(w: "World", ac: AircraftState) -> float:
    return dist(ac.x, ac.y, *w.thr_xy)


def not_transferred(w: "World", ac: AircraftState) -> bool:
    """SID finished or 25 NM out and still on the Director frequency (SIM_SPEC §1.5)."""
    if ac.flow != "DEP" or ac.phase == "HANDED_OFF" or not ac.on_frequency:
        return False
    sid_done = ac.sid_started and ac.route and ac.route_idx >= len(ac.route)
    return bool(sid_done) or dist_from_airport(w, ac) >= NOT_TRANSFERRED_NM


def release_info(w: "World") -> list[dict]:
    """Departure list for the side panel: queue order and why each is waiting."""
    out = []
    for i, a in enumerate(queue(w)):
        out.append({"callsign": a.callsign, "sid": a.route_name, "position": i + 1,
                    "waiting": blocked_reason(w, a) if i == 0 else "queued"})
    return out


__all__ = ["DepartureGenerator", "manage", "ground_roll", "guide", "after_step",
           "on_ground", "queue", "release_info", "not_transferred", "norm_hdg"]
