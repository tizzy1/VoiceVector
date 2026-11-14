"""Seeded arrival generator (traffic.json): spawns arrivals on STARs with a check-in call."""
from __future__ import annotations

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
PROBE_S = 180                   # feasibility probe horizon
LEVEL_CHECK_NM = 20.0
PERSONAS = (("crisp_airline", 50), ("calm_longhaul", 20), ("rushed", 20), ("student", 10))
SUFFIX_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"


class ArrivalGenerator:
    def __init__(self, world: "World", seed: int, first_spawn_s: float) -> None:
        self.w = world
        self.rng = random.Random(seed + 1)
        self.cfg = world.traffic
        self.backlog = 0
        self.next_t = first_spawn_s
        self.retry_t = 0.0

    # ----------------------------------------------------------- schedule
    def rate_per_hr(self) -> float:
        w = self.w
        presets = self.cfg["difficulty_presets"]
        name = w.scenario
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
            self.backlog += 1
            self.next_t += self.rng.uniform(0.6, 1.4) * 3600.0 / rate
        if self.backlog and self.w.t >= self.retry_t:
            if self.spawn() is not None:
                self.backlog -= 1
            else:
                self.retry_t = self.w.t + RETRY_S

    # -------------------------------------------------------------- spawn
    def spawn(self, entry: Optional[str] = None, force: bool = False) -> Optional[AircraftState]:
        entries = self.cfg["arrival_entries"]
        names = [entry] if entry in entries else self._weighted_order(entries)
        for name in names[:4]:
            ac = self._build(name, entries[name])
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

    def _callsign(self, airline: str) -> str:
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
