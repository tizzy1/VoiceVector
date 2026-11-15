"""Seeded arrival generator (traffic.json): spawns arrivals on STARs with a check-in call."""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Optional

from server.sim.aircraft import AircraftState
from server.sim.geo import bearing, dist
from server.sim.kinematics import tas_of
from server.sim.stca import predicted_conflict

if TYPE_CHECKING:
    from server.sim.world import World

SPAWN_TRACK_NM = 40.0           # spawn this many track miles before the STAR end
RETRY_S = 10.0
SIMILAR_CALLSIGN_P = 0.10         # chance a spawn copies a callsign with digits swapped
PROBE_S = 180                   # feasibility probe horizon
LEVEL_CHECK_NM = 20.0
PERSONAS = (("crisp_airline", 50), ("calm_longhaul", 20), ("rushed", 20), ("student", 10))
SUFFIX_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"
# 15 Nov (Sanju for Tudor): random-traffic sessions start quiet and build up to ~8 arrivals, and
# arrivals appear at different stages, including established on final. The scripted demo and the
# judges' scenario are unchanged by this.
RANDOM_SCENARIOS = ("easy", "medium", "busy", "ramp")
MAX_ACTIVE_ARRIVALS = 8
START_ACTIVE_ARRIVALS = 2
BUILD_UP_S = 120.0                       # one more arrival allowed every 2 minutes
MAX_BACKLOG = 2
STAGE_WEIGHTS = (("star", 55), ("hold", 15), ("downwind", 15), ("final", 15))
FINAL_SPAWN_NM = (10.0, 12.5)
DOWNWIND_OFFSET_NM = 7.0
DOWNWIND_ALT_FT = 5000.0


class ArrivalGenerator:
    def __init__(self, world: "World", seed: int, first_spawn_s: float) -> None:
        self.w = world
        self.rng = random.Random(seed + 1)
        self.similar_rng = random.Random(seed + 7)     # similar-callsign pairs (own stream)
        self.cfg = world.traffic
        self.backlog = 0
        self.next_t = first_spawn_s
        self.retry_t = 0.0

    # ----------------------------------------------------------- schedule
    def rate_per_hr(self) -> float:
        w = self.w
        presets = self.cfg["difficulty_presets"]
        name = w.scenario
        if name == "judge":                  # one aircraft only (15 Nov, Sanju for Tudor)
            return 0.0
        if name == "ramp":
            ramp = presets["ramp"]
            lo, hi = presets[ramp["start"]]["arr_per_hr"], presets[ramp["end"]]["arr_per_hr"]
            return lo + (hi - lo) * min(1.0, w.t / (ramp["over_min"] * 60.0))
        if name in presets and "arr_per_hr" in presets[name]:
            return float(presets[name]["arr_per_hr"])
        return float(w.settings.arr_per_hr)

    def tick(self) -> None:
        rate = self.rate_per_hr()
        if rate <= 0:
            return
        while self.w.t >= self.next_t:
            self.backlog = min(self.backlog + 1, MAX_BACKLOG) if self.capped() else self.backlog + 1
            self.next_t += self.rng.uniform(0.6, 1.4) * 3600.0 / rate
        if self.capped() and self.active() >= self.cap():
            return
        if self.backlog and self.w.t >= self.retry_t:
            if self.spawn(stage=self._stage()) is not None:
                self.backlog -= 1
            else:
                self.retry_t = self.w.t + RETRY_S

    def capped(self) -> bool:
        """Random-traffic presets only; "custom" (instructor rates), demo and judge keep their own."""
        return self.w.scenario in RANDOM_SCENARIOS

    def cap(self) -> int:
        return min(MAX_ACTIVE_ARRIVALS, START_ACTIVE_ARRIVALS + int(self.w.t // BUILD_UP_S))

    def active(self) -> int:
        return sum(1 for a in self.w.aircraft.values()
                   if a.flow == "ARR" and a.phase not in ("LANDED", "HANDED_OFF"))

    def _stage(self) -> str:
        if not self.capped():
            return "star"
        names, weights = zip(*STAGE_WEIGHTS)
        return self.rng.choices(names, weights=weights)[0]

    # -------------------------------------------------------------- spawn
    def spawn(self, entry: Optional[str] = None, force: bool = False,
              stage: Optional[str] = None) -> Optional[AircraftState]:
        entries = self.cfg["arrival_entries"]
        names = [entry] if entry in entries else self._weighted_order(entries)
        # an explicit spawn (instructor inject, tests) is a STAR arrival unless a stage is given;
        # only the generator's own scheduled spawns (tick -> _spawn_scheduled) pick a random stage
        stage = stage if stage in dict(STAGE_WEIGHTS) else "star"
        for name in names[:4]:
            ac = self._build(name, entries[name])
            if ac is not None and stage != "star":
                ac = self._restage(ac, stage)
            if ac is not None and (force or self._feasible(ac)):
                self.w.add_aircraft(ac, checkin=True)
                return ac
        return None

    def _weighted_order(self, entries: dict) -> list[str]:
        pool = sorted(entries)
        order: list[str] = []
        while pool:
            pick = self.rng.choices(pool, weights=[entries[n]["weight"] for n in pool])[0]
            order.append(pick)
            pool.remove(pick)
        return order

    def _build(self, entry: str, spec: dict) -> Optional[AircraftState]:
        w, rng = self.w, self.rng
        star = rng.choice(spec["stars"])
        route = w.egkk["procedures"]["STAR"].get(w.runway, {}).get(star, {}).get("route")
        if not route:
            return None
        route = [f for f in route if f in w.egkk["fixes"] and not f.startswith("FI")]
        x, y, hdg, remaining = self._spawn_point(route)
        fl_lo, fl_hi = spec["entry_fl_range"]
        levels = list(range(fl_lo, fl_hi + 1, 10))
        rng.shuffle(levels)
        end = remaining[-1] if remaining else route[-1]
        near = [o for o in w.aircraft.values()          # nearby, or bound for the same stack
                if dist(x, y, o.x, o.y) < LEVEL_CHECK_NM or self._stack(o) == end]
        level = next((fl for fl in levels
                      if all(abs(fl * 100 - o.alt) >= 1000 and abs(fl * 100 - o.cleared_alt) >= 1000
                             for o in near)), None)
        if level is None:
            return None
        fleet = self.cfg["fleet_mix"]
        airline = rng.choices(fleet, weights=[f["weight"] for f in fleet])[0]
        types = airline["types"]
        actype = rng.choices(list(types), weights=list(types.values()))[0]
        wake = w.perf[actype]["wake"]
        callsign = self._callsign(airline["airline"])
        personas = PERSONAS if wake != "H" else (("calm_longhaul", 70), ("crisp_airline", 30))
        persona = rng.choices([p for p, _ in personas], weights=[n for _, n in personas])[0]
        alt = level * 100.0
        ias = 250.0 if alt < 10000 else 270.0
        tas = tas_of(ias, alt)
        return AircraftState(
            callsign=callsign, spoken=w.spoken(callsign), spoken_plain=w.spoken_plain(callsign),
            actype=actype, wake=wake, persona=persona, flow="ARR", phase="STAR",
            x=x, y=y, alt=alt, hdg=hdg, track=hdg, ias=ias, tas=tas, gs=tas, tgt_hdg=hdg,
            lat_mode="LNAV", route=remaining, route_name=star, cleared_alt=alt,
            qnh=w.qnh, spawn_t=w.t,
        )

    def _restage(self, ac: AircraftState, stage: str) -> Optional[AircraftState]:
        """Move a STAR spawn to a later stage: in the hold, on downwind, or established on final."""
        w, rng = self.w, self.rng
        c = math.radians(w.loc_course)
        ux, uy = math.sin(c), math.cos(c)               # direction flown on final
        tx, ty = w.thr_xy
        if stage == "hold":
            fix = ac.route[-1] if ac.route and ac.route[-1] in w.hold_fixes else sorted(w.hold_fixes)[0]
            used = {round(o.cleared_alt) for o in w.aircraft.values() if self._stack(o) == fix}
            level = next((a for a in (7000, 8000, 9000, 10000) if a not in used), None)
            if level is None:
                return None
            fx, fy = w.fix_xy(fix)
            ac.x, ac.y, ac.alt, ac.cleared_alt = fx, fy, float(level), float(level)
            ac.ias = 220.0
            ac.lat_mode, ac.phase, ac.route = "HOLD", "HOLD", []
            ac.hold = {"fix": fix, "inbound": ac.track, "leg": "turn_out", "t": w.t, "prev_d": 0.0}
        elif stage == "downwind":
            side = rng.choice((-1.0, 1.0))
            nx, ny = uy * side, -ux * side              # perpendicular to the final course
            ac.x, ac.y = tx + ux * 3.0 + nx * DOWNWIND_OFFSET_NM, ty + uy * 3.0 + ny * DOWNWIND_OFFSET_NM
            hdg = (w.loc_course + 180.0) % 360.0
            ac.alt = ac.cleared_alt = DOWNWIND_ALT_FT
            ac.ias, ac.hdg, ac.track, ac.tgt_hdg = 220.0, hdg, hdg, hdg
            ac.lat_mode, ac.phase, ac.route = "HDG", "VECTORS", []
        elif stage == "final":
            from server.sim.modes import glidepath_alt
            d = rng.uniform(*FINAL_SPAWN_NM)
            ac.x, ac.y = tx - ux * d, ty - uy * d
            ac.alt = round(glidepath_alt(w, d), -1)
            ac.cleared_alt = 2000.0
            ac.ias, ac.hdg, ac.track, ac.tgt_hdg = 180.0, w.loc_course, w.loc_course, w.loc_course
            ac.lat_mode, ac.phase, ac.route = "HDG", "APPROACH", []
            ac.approach_cleared = {"type": "ILS", "runway": w.runway}
            ac.loc_armed = ac.gs_armed = True
        ac.tas = ac.gs = tas_of(ac.ias, ac.alt)
        return ac

    @staticmethod
    def _stack(o: AircraftState) -> Optional[str]:
        if o.lat_mode == "HOLD" and o.hold:
            return o.hold["fix"]
        if o.lat_mode == "LNAV" and o.route:
            return o.route[-1]
        return None

    def _spawn_point(self, route: list[str]) -> tuple[float, float, float, list[str]]:
        """Walk back SPAWN_TRACK_NM from the STAR end; returns x, y, heading, fixes still ahead."""
        pts = [self.w.fix_xy(f) for f in route]
        left = SPAWN_TRACK_NM
        for i in range(len(pts) - 1, 0, -1):
            (x0, y0), (x1, y1) = pts[i - 1], pts[i]
            seg = dist(x0, y0, x1, y1)
            if seg >= left and seg > 0:
                f = left / seg
                return (x1 + (x0 - x1) * f, y1 + (y0 - y1) * f,
                        bearing(x0, y0, x1, y1), route[i:])
            left -= seg
        hdg = bearing(*pts[0], *pts[1]) if len(pts) > 1 else 0.0
        return pts[0][0], pts[0][1], hdg, route[1:]

    def _similar_callsign(self, airline: str) -> Optional[str]:
        """Sometimes give a new arrival a callsign easily confused with one on frequency
        (same airline, last two digits swapped: BAW117 / BAW171) — a real-world hazard."""
        if self.w.scenario == "demo":                  # the scripted demo stays predictable
            return None
        if self.similar_rng.random() >= SIMILAR_CALLSIGN_P:
            return None
        for cs in sorted(self.w.aircraft):
            body = cs[3:]
            if cs[:3] != airline or len(body) < 3 or not body.isdigit():
                continue
            twin = airline + body[:-2] + body[-1] + body[-2]
            if twin != cs and twin not in self.w.aircraft and twin not in self.w.used_callsigns:
                return twin
        return None

    def _callsign(self, airline: str) -> str:
        twin = self._similar_callsign(airline)
        if twin is not None:
            return twin
        rng = self.rng
        while True:
            if rng.random() < 0.3:
                letters = rng.choice(SUFFIX_LETTERS) + rng.choice(SUFFIX_LETTERS)
                body = f"{rng.randint(1, 99)}{letters}"
            else:
                body = str(rng.randint(1, 9999) if rng.random() < 0.5 else rng.randint(1, 999))
            cs = airline + body
            if cs not in self.w.aircraft and cs not in self.w.used_callsigns:
                return cs

    def _feasible(self, ac: AircraftState) -> bool:
        sep = self.cfg["separation"]
        min_nm, min_ft = float(sep["radar_min_nm"]), float(sep["vertical_min_ft"])
        spacing = float(sep["arrival_spawn_min_spacing_nm"])
        for o in self.w.aircraft.values():
            if o.phase == "LANDED":
                continue
            if dist(ac.x, ac.y, o.x, o.y) < spacing:
                return False
            if predicted_conflict(ac, o, min_nm, min_ft, PROBE_S) is not None:
                return False
        return True
