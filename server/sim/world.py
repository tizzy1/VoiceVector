"""World — the deterministic Gatwick simulator (owner: Tudor).

One 1 Hz tick: due clearances -> guidance (modes) -> physics (kinematics) -> traffic
generator -> STCA. `evaluate` (unable / query) lives in sim/evaluate.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

from server.contracts import (
    AircraftPublic, Alert, CallsignInfo, ClearanceElement, Evaluation, Event, EventBus,
    EventType, InjectKind, Instruction, SessionSettings, StateMsg, Transmission, WorldSnapshot,
)
from server.sim import kinematics, modes, registry, runway as rwy_mgr, stca, weather
from server.sim.aircraft import AircraftState
from server.sim.geo import Projection, heading_for_track, norm_hdg, wind_vector
from server.sim.evaluate import evaluate as evaluate_tx
from server.sim.traffic import ArrivalGenerator

TRANSITION_ALT_FT = 6000
LANDED_REMOVE_S = 30.0
DEMO_FIRST_SPAWN_S = 300.0      # keep the scripted first minutes of the demo quiet
ATIS_LETTER = "K"
EXPECT_APPROACH_ASK_S = 120.0    # arrival asks for the expected approach 2 min after check-in
CONFLICT_LOS_S = 90.0            # spawn_conflict: loss of separation after ~90 s if untouched
CONFLICT_POINT_NM = (18.0, 12.0)  # crossing point NE of the airfield, clear of the demo traffic
CONFLICT_ALT_FT = 7000.0
CONFLICT_IAS = 250.0


class World:
    def __init__(self, bus: EventBus, data_dir: Path) -> None:
        self.bus = bus
        self.data_dir = Path(data_dir)
        self.egkk = self._load("egkk")
        self.perf = self._load("perf")
        self.telephony = self._load("telephony")
        self.traffic = self._load("traffic")
        origin = self.egkk["_meta"]["origin"]
        self.proj = Projection(origin["lat"], origin["lon"])
        self.rules = self.perf["_meta"]["rules"]
        self.max_bank = float(self.rules["max_bank_deg"])
        self.hold_fixes = set(self.egkk.get("holds", {}))
        self.gs_ft_per_nm = float(self.egkk["glideslope_ft_per_nm"])
        sep = self.traffic["separation"]
        self.min_sep_nm = float(sep["radar_min_nm"])
        self.min_sep_ft = float(sep["vertical_min_ft"])
        self.wake_final_nm = {k: float(v) for k, v in sep.get("final_approach_wake_nm", {}).items()
                              if k != "default"}

        self.settings = SessionSettings()
        self.seed = self.settings.seed
        self.scenario = self.settings.scenario
        self.t: float = 0.0
        self.aircraft: dict[str, AircraftState] = {}
        self.used_callsigns: set[str] = set()
        self.alerts: dict[tuple[str, ...], Alert] = {}
        self.wind: tuple[float, float] = (0.0, 0.0)
        self.qnh = 1013
        self._pending: list[tuple[float, int, str, ClearanceElement]] = []
        self._seq = 0
        self._set_runway(self.settings.runway)
        self.generator = ArrivalGenerator(self, self.seed, 0.0)
        self.dep_generator = rwy_mgr.DepartureGenerator(self, self.seed, 0.0)
        self.last_departure: Optional[str] = None
        self.last_takeoff_t: Optional[float] = None
        self.last_landing_t: Optional[float] = None
        self.load_scenario(self.scenario, self.seed)

    # ------------------------------------------------------------------ setup
    def _load(self, name: str) -> dict[str, Any]:
        return json.loads((self.data_dir / f"{name}.json").read_text())

    def _set_runway(self, runway: str) -> None:
        rwy = self.egkk["runways"][runway]
        self.runway = runway
        self.loc_course = float(rwy["localiser_course_true"])
        self.thr_xy = (float(rwy["threshold"]["xy_nm"][0]), float(rwy["threshold"]["xy_nm"][1]))
        self.rwy_elev = float(rwy["elevation_ft"])

    def configure(self, settings: SessionSettings) -> None:
        """Apply runway and traffic rates. The seed only changes via load_scenario."""
        self.settings = settings.model_copy(update={"seed": self.seed})
        if settings.runway != self.runway:
            self._set_runway(settings.runway)

    def load_scenario(self, name: str, seed: int) -> None:
        self.seed, self.scenario = seed, name
        self.settings = self.settings.model_copy(update={"seed": seed})
        self.t = 0.0
        self.aircraft, self.used_callsigns, self.alerts = {}, set(), {}
        self._pending, self._seq = [], 0
        self.wind, self.qnh = (0.0, 0.0), 1013
        self.wind_from, self.wind_kt, self.wx_extra = 0.0, 0.0, None
        first = DEMO_FIRST_SPAWN_S if name == "demo" else 5.0
        self.generator = ArrivalGenerator(self, seed, first)
        self.dep_generator = rwy_mgr.DepartureGenerator(self, seed, first + 60.0)
        self.last_departure, self.last_takeoff_t, self.last_landing_t = None, None, None
        self.script: list[dict[str, Any]] = []   # timed scenario injects, fired once each
        if name != "demo":
            return                      # other scenarios start empty; the generator fills them
        scenario = self._load("scenario_demo")
        meta = scenario["_meta"]
        self.qnh = int(meta.get("qnh", 1013))
        if meta.get("wind"):
            self.wind = wind_vector(float(meta["wind"][0]), float(meta["wind"][1]))
            self.wind_from, self.wind_kt = float(meta["wind"][0]), float(meta["wind"][1])
        self.wx_extra = meta.get("weather")
        stars = self.egkk["procedures"]["STAR"][self.runway]
        for spec in scenario["aircraft"]:
            cs = spec["callsign"]
            route = list(stars.get(spec.get("star", ""), {}).get("route", []))
            if spec.get("at_fix") in route:
                route = route[route.index(spec["at_fix"]) + 1:]
            ias, alt = float(spec["ias_kt"]), float(spec["alt_ft"])
            tas = kinematics.tas_of(ias, alt)
            hdg = norm_hdg(float(spec["hdg"]))
            on_star = bool(route)       # nothing left of the STAR = already on vectors
            self.add_aircraft(AircraftState(
                callsign=cs, spoken=self.spoken(cs), spoken_plain=self.spoken_plain(cs),
                actype=spec["type"], wake=self.perf[spec["type"]]["wake"],
                persona=spec.get("persona", "crisp_airline"), flow="ARR",
                phase="STAR" if on_star else "VECTORS",
                lat_mode="LNAV" if on_star else "HDG",
                x=float(spec["xy_nm"][0]), y=float(spec["xy_nm"][1]), alt=alt,
                hdg=hdg, track=hdg, ias=ias, tas=tas, gs=tas, tgt_hdg=hdg,
                route=route, route_name=spec.get("star"), cleared_alt=alt, qnh=self.qnh,
            ), checkin=False)
        for spec in scenario.get("departures", []):
            dep = rwy_mgr.make_departure(self, spec["callsign"], spec["type"], spec["sid"],
                                         spec.get("persona", "crisp_airline"))
            dep.release_not_before = float(spec.get("release_not_before_s", 0.0))
            self.add_aircraft(dep, checkin=False)
        self.script = sorted((dict(i, fired=False) for i in scenario.get("injects", [])),
                             key=lambda i: float(i["t_s"]))

    def add_aircraft(self, ac: AircraftState, checkin: bool) -> None:
        self.aircraft[ac.callsign] = ac
        self.used_callsigns.add(ac.callsign)
        self.emit("spawn", ac.callsign, {"flow": ac.flow, "actype": ac.actype,
                                         "route": ac.route_name})
        if checkin:
            ac.checkin_t = self.t
            level = ({"level_fl": round(ac.alt / 100)} if ac.alt > TRANSITION_ALT_FT
                     else {"alt": round(ac.alt)})
            self.pilot_call(ac, "checkin", route=ac.route_name, atis=ATIS_LETTER, **level)

    # ------------------------------------------------------------------- tick
    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        self.t += dt
        self._apply_due()
        self._run_script()
        rwy_mgr.manage(self)
        for ac in list(self.aircraft.values()):
            if ac.flow == "DEP" and rwy_mgr.on_ground(self, ac):
                if ac.phase == "TAKEOFF":
                    rwy_mgr.ground_roll(self, ac, dt)
                continue
            modes.guide(self, ac)
            if ac.flow == "DEP":
                rwy_mgr.guide(self, ac)
            if kinematics.step(ac, self.perf[ac.actype], self.rules, self.wind, dt):
                self.emit("level_reached", ac.callsign, {"alt": ac.cleared_alt})
            self._check_level_conditionals(ac)
            if ac.flow == "DEP":
                rwy_mgr.after_step(self, ac)
            else:
                self._arrival_calls(ac)
        for cs in [cs for cs, ac in self.aircraft.items()
                   if (ac.landed_t is not None and self.t - ac.landed_t >= LANDED_REMOVE_S)
                   or (ac.flow == "DEP" and not ac.on_frequency and ac.phase != "QUEUED"
                       and ac.dep_checked_in
                       and rwy_mgr.dist_from_airport(self, ac) > rwy_mgr.REMOVE_NM)]:
            del self.aircraft[cs]
        self.generator.tick()
        self.dep_generator.tick()
        self._update_alerts()

    def _arrival_calls(self, ac: AircraftState) -> None:
        """SIM_SPEC §1.7: ask for the expected approach 2 min after check-in with no expect."""
        if (ac.checkin_t is None or ac.asked_expect or ac.expect or ac.approach_cleared
                or not ac.on_frequency or ac.phase not in ("STAR", "HOLD", "VECTORS")):
            return
        jitter = sum(map(ord, ac.callsign)) % 20         # deterministic, spreads the calls
        if self.t - ac.checkin_t >= EXPECT_APPROACH_ASK_S + jitter:
            ac.asked_expect = True
            self.pilot_call(ac, "request", what="expected_approach")

    def _apply_due(self) -> None:
        due = sorted((p for p in self._pending if p[0] <= self.t), key=lambda p: p[:2])
        self._pending = [p for p in self._pending if p[0] > self.t]
        for _, _, cs, element in due:
            ac = self.aircraft.get(cs)
            if ac is not None and ac.phase != "LANDED":
                self._apply(ac, element.flown)

    # ---------------------------------------------------------- applying
    def schedule_apply(self, callsign: str, elements: list[ClearanceElement], at: float) -> None:
        for element in elements:
            self._seq += 1
            self._pending.append((at + element.delay_s, self._seq, callsign, element))

    def _apply(self, ac: AircraftState, ins: Instruction) -> None:
        cond = ins.condition
        if cond and (cond.after_fix or cond.passing_level is not None):
            ac.conditionals.append({"after_fix": cond.after_fix,
                                    "passing_level": cond.passing_level,
                                    "from_alt": ac.alt,
                                    "instruction": ins.model_dump(exclude={"condition"})})
            return
        if registry.apply(self, ac, ins):
            self.emit("instruction_applied", ac.callsign,
                      {"element_type": ins.type, "value": ins.value, "turn_dir": ins.turn_dir},
                      actor="PILOT")

    def fire_conditionals(self, ac: AircraftState, after_fix: Optional[str] = None,
                          passing: bool = False) -> None:
        keep, fire = [], []
        for c in ac.conditionals:
            lvl = c.get("passing_level")
            hit = (after_fix is not None and c.get("after_fix") == after_fix) or (
                passing and lvl is not None
                and min(c["from_alt"], ac.alt) <= lvl <= max(c["from_alt"], ac.alt))
            (fire if hit else keep).append(c)
        ac.conditionals = keep
        for c in fire:
            self._apply(ac, Instruction.model_validate(c["instruction"]))

    def _check_level_conditionals(self, ac: AircraftState) -> None:
        if any(c.get("passing_level") is not None for c in ac.conditionals):
            self.fire_conditionals(ac, passing=True)

    # ------------------------------------------------- sim-driven behaviour
    def go_around(self, ac: AircraftState, reason: str, call: bool = True) -> None:
        ac.lat_mode, ac.tgt_hdg, ac.turn_dir, ac.hold = "RWY_TRACK", self.loc_course, None, None
        ac.vert_mode, ac.gp_alt, ac.cleared_alt = "LVL_CHG", None, modes.GO_AROUND_ALT_FT
        ac.tgt_ias, ac.auto_ias = modes.GO_AROUND_IAS, None
        ac.loc_armed = ac.gs_armed = ac.established = False
        ac.approach_cleared, ac.phase = None, "GO_AROUND"
        self.emit("go_around", ac.callsign, {"reason": reason})
        if call and ac.on_frequency:
            self.pilot_call(ac, "going_around")

    def land(self, ac: AircraftState) -> None:
        ac.phase, ac.landed_t, ac.on_frequency = "LANDED", self.t, False
        self.last_landing_t = self.t
        ac.alt, ac.vs, ac.vert_mode, ac.gp_alt = self.rwy_elev, 0.0, "ALT", None
        ac.track = ac.hdg = self.loc_course
        ac.tgt_hdg, ac.auto_ias = None, None
        self.emit("landing", ac.callsign, {"runway": self.runway})

    def pilot_call(self, ac: AircraftState, kind: str, **values: Any) -> None:
        # spoken-text keys the readback composer reads, alongside the contract keys
        if kind == "request" and "request" not in values:
            values["request"] = {"higher": "higher level",
                                 "expected_approach": "expected approach"}.get(
                                     str(values.get("what")), str(values.get("what", "vectors")))
        if kind in ("mayday", "pan") and values.get("reason") and "nature" not in values:
            values["nature"] = values["reason"]
        self.emit("pilot_call", ac.callsign, {"kind": kind, **values}, actor="PILOT")

    def _update_alerts(self) -> None:
        now: dict[tuple[str, ...], Alert] = {}
        for c in stca.detect(self.aircraft.values(), self.min_sep_nm, self.min_sep_ft):
            a, b = c["callsigns"]
            if c["kind"] == "STCA_LOS":
                msg = f"{a} and {b} {c['dist_nm']:.1f} NM, {round(c['dalt_ft'], -2):.0f} ft"
                sev = "HIGH"
            else:
                msg = f"{a} and {b} predicted conflict in {c['in_s']} s"
                sev = "MEDIUM"
            key = (c["kind"], a, b)
            old = self.alerts.get(key)
            now[key] = Alert(kind=c["kind"], callsigns=[a, b], severity=sev, message=msg,
                             since=old.since if old else self.t)
        for w in stca.wake_on_final(self.aircraft.values(),
                                    lambda ac: modes.loc_geometry(self, ac)[0], self.wake_final_nm):
            lead, foll = w["callsigns"]
            key = ("WAKE", lead, foll)
            old = self.alerts.get(key)
            now[key] = Alert(kind="WAKE", callsigns=[lead, foll], severity="MEDIUM",
                             message=(f"{foll} {w['gap_nm']:.1f} NM behind {lead}, "
                                      f"wake {w['cat']} needs {w['need_nm']:.0f} NM"),
                             since=old.since if old else self.t)
        for cs, ac in self.aircraft.items():
            if ac.emergency:
                key = ("EMERGENCY", cs)
                old = self.alerts.get(key)
                now[key] = Alert(kind="EMERGENCY", callsigns=[cs], severity="HIGH",
                                 message=f"{cs} emergency, squawk {ac.squawk}",
                                 since=old.since if old else self.t)
        for key in now.keys() - self.alerts.keys():
            self.emit("alert_start", None, {"kind": key[0], "callsigns": list(key[1:]),
                                            "message": now[key].message})
        for key in self.alerts.keys() - now.keys():
            self.emit("alert_end", None, {"kind": key[0], "callsigns": list(key[1:]),
                                          "since": self.alerts[key].since})
        self.alerts = now

    def evaluate(self, tx: Transmission) -> list[Evaluation]:
        """OK / UNABLE / QUERY per instruction (see sim/evaluate.py). Pure — no state change."""
        return evaluate_tx(self, tx)

    def _run_script(self) -> None:
        """Fire the scenario's world injects at their t_s. force_error entries are
        left for PilotEngine.tick, which reads them from world.script."""
        for item in self.script:
            if item["fired"] or item.get("call") != "inject" or self.t < float(item["t_s"]):
                continue
            item["fired"] = True
            self.inject(item["kind"], item.get("callsign"), dict(item.get("params") or {}))

    def set_transmitting(self, callsign: str, on: bool) -> None:
        ac = self.aircraft.get(callsign)
        if ac is not None:
            ac.transmitting = on

    def inject(self, kind: InjectKind, callsign: Optional[str] = None,
               params: Optional[dict[str, Any]] = None) -> None:
        params = params or {}
        ac = self.aircraft.get(callsign) if callsign else None
        if kind in ("mayday", "pan") and ac is not None:
            ac.emergency = True
            if kind == "mayday":
                ac.squawk = "7700"
            self.pilot_call(ac, kind, **params)
        elif kind == "radio_failure" and ac is not None:
            ac.on_frequency, ac.radio_failed = False, True
        elif kind == "go_around" and ac is not None:
            self.go_around(ac, reason="injected")
        elif kind == "spawn_arrival":
            self.generator.spawn(entry=params.get("entry"), force=True)
        elif kind == "spawn_departure":
            dep = self.dep_generator.spawn(sid=params.get("sid"))
            params = {**params, "spawned": [dep.callsign] if dep else []}
        elif kind == "spawn_conflict":
            params = {**params, "spawned": self._spawn_conflict()}
        elif kind == "force_overlap":
            params = {**params, "callers": self._force_overlap()}
        self.emit("inject", callsign, {"kind": kind, **params}, actor="INSTRUCTOR")

    def _spawn_conflict(self) -> list[str]:
        """Two arrivals converging 90° on the same level; LoS in ~90 s if nobody acts."""
        px, py = CONFLICT_POINT_NM
        tas = kinematics.tas_of(CONFLICT_IAS, CONFLICT_ALT_FT)
        r_los = self.min_sep_nm / math.sqrt(2.0)          # each at r from P -> sep = r * sqrt 2
        d0 = r_los + tas * CONFLICT_LOS_S / 3600.0
        out: list[str] = []
        rng = self.generator.rng
        for come_from, airline, actype in ((45.0, "EZY", "A320"), (135.0, "RYR", "B738")):
            track = norm_hdg(come_from + 180.0)
            x = px + d0 * math.sin(math.radians(come_from))
            y = py + d0 * math.cos(math.radians(come_from))
            hdg = heading_for_track(track, tas, self.wind)
            cs = self.generator._callsign(airline)
            persona = rng.choice(("crisp_airline", "rushed"))
            self.add_aircraft(AircraftState(
                callsign=cs, spoken=self.spoken(cs), spoken_plain=self.spoken_plain(cs),
                actype=actype, wake=self.perf[actype]["wake"], persona=persona, flow="ARR",
                phase="VECTORS", lat_mode="HDG", x=x, y=y, alt=CONFLICT_ALT_FT, hdg=hdg,
                track=track, ias=CONFLICT_IAS, tas=tas, gs=tas, tgt_hdg=hdg,
                cleared_alt=CONFLICT_ALT_FT, qnh=self.qnh, spawn_t=self.t,
            ), checkin=False)
            out.append(cs)
        return out

    def _force_overlap(self) -> list[str]:
        """Two pilot calls in the same tick: two new arrivals check in together."""
        callers = []
        for _ in range(2):
            ac = self.generator.spawn(force=True)
            if ac is not None:
                callers.append(ac.callsign)
        if len(callers) < 2:                              # no room: an existing aircraft asks
            for ac in sorted(self.aircraft.values(), key=lambda a: a.callsign):
                if len(callers) >= 2:
                    break
                if ac.on_frequency and ac.flow == "ARR" and ac.callsign not in callers:
                    self.pilot_call(ac, "request", what="expected_approach")
                    callers.append(ac.callsign)
        return callers

    def departure_list(self) -> list[dict[str, Any]]:
        return rwy_mgr.release_info(self)

    # ------------------------------------------------------------ views
    def metar(self) -> dict[str, Any]:
        """SIMULATED EGKK METAR built from the session's own wind and QNH (sim/weather.py)."""
        return weather.build("EGKK", self.wind_from, self.wind_kt, self.qnh,
                             getattr(self, "wx_extra", None))

    def public_state(self) -> StateMsg:
        return StateMsg(
            t=self.t, paused=self.settings.paused, sim_speed=self.settings.sim_speed,
            runway=self.runway, alerts=list(self.alerts.values()),
            backlog={"ARR": self.generator.backlog, "DEP": len(rwy_mgr.queue(self))},
            aircraft=[self._public(ac) for ac in self.aircraft.values()],
            metar=self.metar(),
        )

    def _public(self, ac: AircraftState) -> AircraftPublic:
        lat, lon = self.proj.to_latlon(ac.x, ac.y)
        appr = ac.approach_cleared
        return AircraftPublic(
            callsign=ac.callsign, spoken_plain=ac.spoken_plain, actype=ac.actype, wake=ac.wake,
            flow=ac.flow, phase=ac.phase, x=round(ac.x, 4), y=round(ac.y, 4),
            lat=round(lat, 6), lon=round(lon, 6), alt=round(ac.alt, 1), vs=round(ac.vs),
            hdg=round(ac.hdg, 2), track=round(ac.track, 2), ias=round(ac.ias, 1),
            gs=round(ac.gs, 1), cleared_alt=ac.cleared_alt, tgt_hdg=ac.tgt_hdg,
            turn_dir=ac.turn_dir, tgt_ias=ac.tgt_ias, route_name=ac.route_name,
            next_fix=self._next_fix(ac),
            approach_cleared=f"{appr['type']} {appr['runway']}" if appr else None,
            established=ac.established, squawk=ac.squawk, on_frequency=ac.on_frequency,
            in_jurisdiction=ac.in_jurisdiction, emergency=ac.emergency,
            transmitting=ac.transmitting, plain_status=self._plain_status(ac),
        )

    def snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(
            t=self.t, alerts=list(self.alerts.values()),
            aircraft={cs: ac.model_dump() for cs, ac in self.aircraft.items()},
        )

    def callsigns(self) -> list[CallsignInfo]:
        return [
            CallsignInfo(icao=cs, telephony=self._telephony(cs), spoken=ac.spoken,
                         spoken_digits=self.spoken(cs, digits=True),
                         on_frequency=ac.on_frequency)
            for cs, ac in self.aircraft.items() if ac.on_frequency
        ]

    def fixes(self) -> list[str]:
        return sorted(self.egkk["fixes"])

    # ---------------------------------------------------------- helpers
    def emit(self, type_: EventType, callsign: Optional[str], data: dict[str, Any],
             actor: str = "SYSTEM") -> None:
        self.bus.emit(Event(t=self.t, type=type_, actor=actor, callsign=callsign, data=data))

    def fix_xy(self, name: str) -> tuple[float, float]:
        x, y = self.egkk["fixes"][name]["xy_nm"]
        return float(x), float(y)

    def _next_fix(self, ac: AircraftState) -> Optional[str]:
        if ac.lat_mode == "HOLD" and ac.hold:
            return ac.hold["fix"]
        if ac.lat_mode == "LNAV" and ac.route_idx < len(ac.route):
            return ac.route[ac.route_idx]
        return None

    @staticmethod
    def _split(callsign: str) -> tuple[str, str]:
        """'BAW117' -> ('BAW', '117'); unknown shapes -> ('', callsign)."""
        if len(callsign) > 3 and callsign[:3].isalpha() and callsign[3].isdigit():
            return callsign[:3], callsign[3:]
        return "", callsign

    def _telephony(self, callsign: str) -> str:
        prefix, _ = self._split(callsign)
        name = self.telephony.get(prefix)
        return name if isinstance(name, str) else ""

    def spoken(self, callsign: str, digits: bool = False) -> str:
        """'speedbird one one seven' ('speedbird 117' with digits=True); suffix phonetic."""
        prefix, rest = self._split(callsign)
        words_d: dict[str, str] = self.telephony["_digits"]
        phon: dict[str, str] = self.telephony["_phonetic"]
        spell = {"alfa": "alpha", "juliett": "juliet"}
        parts = [self._telephony(callsign).lower()] if prefix and self._telephony(callsign) else []
        num = "".join(c for c in rest if c.isdigit()) if digits else ""
        if digits:
            if num:
                parts.append(num)
            rest = "".join(c for c in rest if not c.isdigit())
        for c in rest:
            if c.isdigit():
                parts.append(words_d[c])
            else:
                w = phon.get(c.upper(), c).lower()
                parts.append(spell.get(w, w))
        return " ".join(p for p in parts if p)

    def spoken_plain(self, callsign: str) -> str:
        prefix, rest = self._split(callsign)
        name = self.telephony.get("_airline_names", {}).get(prefix)
        return f"{name} {rest}" if name else callsign

    def _plain_status(self, ac: AircraftState) -> str:
        def level(ft: float) -> str:
            if ft > TRANSITION_ALT_FT:
                return f"FL{round(ft / 100):03d}"
            return f"{round(ft):,} ft"
        speed = f"{round(ac.ias)} kt"
        if ac.phase == "LANDED":
            return f"landed runway {self.runway}"
        if ac.phase == "QUEUED":
            return f"waiting to depart {self.runway} · {ac.route_name}"
        if ac.phase == "TAKEOFF" and ac.alt <= self.rwy_elev:
            return f"taking off {self.runway} · {speed}"
        if ac.phase == "HANDED_OFF":
            return f"with London · {ac.route_name}"
        if ac.emergency and ac.squawk == "7700":
            prefix = "MAYDAY · "
        else:
            prefix = ""
        if ac.established and ac.approach_cleared:
            return f"{prefix}established ILS {self.runway} · {speed}"
        if ac.lat_mode == "HOLD" and ac.hold:
            return f"{prefix}holding at {ac.hold['fix']} · {level(ac.alt)}"
        if ac.phase == "GO_AROUND":
            return f"{prefix}going around · climbing to {level(ac.cleared_alt)}"
        if abs(ac.cleared_alt - ac.alt) < 50:
            vertical = f"level {level(ac.cleared_alt)}"
        elif ac.cleared_alt > ac.alt:
            vertical = f"climbing to {level(ac.cleared_alt)}"
        else:
            vertical = f"descending to {level(ac.cleared_alt)}"
        return f"{prefix}{vertical} · {speed}"
