"""World — the deterministic Gatwick simulator (owner: Tudor).

STUB STAGE (CONTRACTS §6): loads the demo scenario, flies simple kinematics, applies
heading / level / speed instructions, `evaluate` says OK to everything. No LNAV, ILS,
traffic generator, STCA or pilot-initiated calls yet — those come next.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Optional

from server.contracts import (
    AircraftPublic, CallsignInfo, ClearanceElement, Evaluation, Event, EventBus, EventType,
    InjectKind, Instruction, SessionSettings, StateMsg, Transmission, WorldSnapshot,
)
from server.sim.aircraft import AircraftState
from server.sim.geo import Projection, norm_hdg, turn_delta

MAX_TURN_RATE = 3.0            # deg/s (rate one)
TRANSITION_ALT_FT = 6000


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
        rules = self.perf["_meta"]["rules"]
        self.accel = float(rules["accel_kt_per_s"])
        self.decel = float(rules["decel_kt_per_s_level"])
        self.max_bank = float(rules["max_bank_deg"])

        self.settings = SessionSettings()
        self.seed = self.settings.seed
        self.t: float = 0.0
        self.aircraft: dict[str, AircraftState] = {}
        self._pending: list[tuple[float, int, str, ClearanceElement]] = []
        self._seq = 0
        self._rng = random.Random(self.seed)
        self.load_scenario(self.settings.scenario, self.seed)

    # ------------------------------------------------------------------ setup
    def _load(self, name: str) -> dict[str, Any]:
        return json.loads((self.data_dir / f"{name}.json").read_text())

    def configure(self, settings: SessionSettings) -> None:
        """Apply runway and traffic rates. The seed only changes via load_scenario."""
        self.settings = settings.model_copy(update={"seed": self.seed})

    def load_scenario(self, name: str, seed: int) -> None:
        self.seed = seed
        self.settings = self.settings.model_copy(update={"seed": seed})
        self._rng = random.Random(seed)
        self.t = 0.0
        self.aircraft = {}
        self._pending = []
        self._seq = 0
        if name != "demo":
            return                      # other scenarios start empty (generator comes later)
        scenario = self._load("scenario_demo")
        qnh = int(scenario["_meta"].get("qnh", 1013))
        stars = self.egkk["procedures"]["STAR"][self.settings.runway]
        for spec in scenario["aircraft"]:
            cs = spec["callsign"]
            route = list(stars.get(spec.get("star", ""), {}).get("route", []))
            if spec.get("at_fix") in route:
                route = route[route.index(spec["at_fix"]) + 1:]
            ias = float(spec["ias_kt"])
            alt = float(spec["alt_ft"])
            tas = self._tas(ias, alt)
            hdg = norm_hdg(float(spec["hdg"]))
            self.aircraft[cs] = AircraftState(
                callsign=cs, spoken=self._spoken(cs), spoken_plain=self._spoken_plain(cs),
                actype=spec["type"], wake=self.perf[spec["type"]]["wake"],
                persona=spec.get("persona", "crisp_airline"), flow="ARR", phase="STAR",
                x=float(spec["xy_nm"][0]), y=float(spec["xy_nm"][1]), alt=alt,
                hdg=hdg, track=hdg, ias=ias, tas=tas, gs=tas, tgt_hdg=hdg,
                route=route, route_name=spec.get("star"), cleared_alt=alt, qnh=qnh,
            )
            self._emit("spawn", cs, {"flow": "ARR", "actype": spec["type"],
                                     "route": spec.get("star")})

    # ------------------------------------------------------------------- tick
    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        self.t += dt
        self._apply_due()
        for ac in self.aircraft.values():
            self._fly(ac, dt)

    def _apply_due(self) -> None:
        due = sorted(p for p in self._pending if p[0] <= self.t)
        self._pending = [p for p in self._pending if p[0] > self.t]
        for _, _, cs, element in due:
            ac = self.aircraft.get(cs)
            if ac is not None:
                self._apply(ac, element)

    def _fly(self, ac: AircraftState, dt: float) -> None:
        perf = self.perf[ac.actype]
        # speed
        if ac.tgt_ias is not None and ac.ias != ac.tgt_ias:
            rate = self.accel if ac.tgt_ias > ac.ias else self.decel
            step = min(abs(ac.tgt_ias - ac.ias), rate * dt)
            ac.ias += math.copysign(step, ac.tgt_ias - ac.ias)
        ac.tas = self._tas(ac.ias, ac.alt)
        # heading (bank-limited rate, explicit direction honoured)
        if ac.tgt_hdg is not None:
            delta = turn_delta(ac.hdg, ac.tgt_hdg, ac.turn_dir)
            rate = min(MAX_TURN_RATE,
                       1091.0 * math.tan(math.radians(self.max_bank)) / max(ac.tas, 1.0))
            if abs(delta) <= rate * dt:
                ac.hdg = norm_hdg(ac.tgt_hdg)
                ac.turn_dir = None
            else:
                ac.hdg = norm_hdg(ac.hdg + math.copysign(rate * dt, delta))
        # level
        err = ac.cleared_alt - ac.alt
        if abs(err) < 1.0:
            if ac.vert_mode == "LVL_CHG":
                self._emit("level_reached", ac.callsign, {"alt": ac.cleared_alt})
            ac.alt, ac.vs, ac.vert_mode = ac.cleared_alt, 0.0, "ALT"
        else:
            low = ac.alt < 10000
            if err > 0:
                fpm = perf["climb_fpm_low"] if low else perf["climb_fpm_mid"]
            else:
                fpm = perf["descent_fpm_low"] if low else perf["descent_fpm"]
            step = min(abs(err), fpm * dt / 60.0)
            ac.vs = math.copysign(step * 60.0 / dt, err)
            ac.alt += math.copysign(step, err)
            ac.vert_mode = "LVL_CHG"
        # position (no wind yet: track = heading, GS = TAS)
        ac.track, ac.gs = ac.hdg, ac.tas
        d = ac.gs * dt / 3600.0
        ac.x += d * math.sin(math.radians(ac.track))
        ac.y += d * math.cos(math.radians(ac.track))

    # ---------------------------------------------------------- applying
    def schedule_apply(self, callsign: str, elements: list[ClearanceElement], at: float) -> None:
        for element in elements:
            self._seq += 1
            self._pending.append((at + element.delay_s, self._seq, callsign, element))

    def _apply(self, ac: AircraftState, element: ClearanceElement) -> None:
        ins: Instruction = element.flown
        value: Any = ins.value
        kind = ins.type
        if kind == "heading" and value is not None:
            ac.lat_mode, ac.tgt_hdg, ac.turn_dir = "HDG", norm_hdg(float(value)), ins.turn_dir
        elif kind == "turn_by" and value is not None and ins.turn_dir:
            sign = 1.0 if ins.turn_dir == "R" else -1.0
            ac.lat_mode, ac.turn_dir = "HDG", ins.turn_dir
            ac.tgt_hdg = norm_hdg(ac.hdg + sign * float(value))
        elif kind == "present_heading":
            ac.lat_mode, ac.tgt_hdg, ac.turn_dir = "HDG", ac.hdg, None
        elif kind == "altitude" and value is not None:
            ac.cleared_alt = float(value)
        elif kind == "flight_level" and value is not None:
            ac.cleared_alt = float(value) * 100.0
        elif kind == "maintain_level" and value is not None:
            v = float(value)
            ac.cleared_alt = v if v >= 1000 else v * 100.0
        elif kind == "speed" and value is not None:
            ac.tgt_ias, ac.spd_constraint = float(value), ins.constraint
        elif kind == "speed_cancel":
            ac.tgt_ias, ac.spd_constraint, ac.no_speed_restriction = None, None, True
        elif kind == "squawk" and isinstance(value, (str, int, float)) and str(value) != "ident":
            ac.squawk = str(value).split(".")[0].zfill(4)
        elif kind == "qnh" and value is not None:
            ac.qnh = int(float(value))
        else:
            return                      # not modelled in the stub
        if ac.phase == "STAR" and ac.lat_mode == "HDG" and kind in ("heading", "turn_by"):
            ac.phase = "VECTORS"
        self._emit("instruction_applied", ac.callsign,
                   {"element_type": kind, "value": value, "turn_dir": ins.turn_dir}, actor="PILOT")

    def evaluate(self, tx: Transmission) -> list[Evaluation]:
        """Stub: every instruction is acceptable. Pure — no state change."""
        return [Evaluation(instr_idx=i, status="OK") for i in range(len(tx.instructions))]

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
        elif kind == "radio_failure" and ac is not None:
            ac.on_frequency = False
        self._emit("inject", callsign, {"kind": kind, **params}, actor="INSTRUCTOR")

    # ------------------------------------------------------------ views
    def public_state(self) -> StateMsg:
        return StateMsg(
            t=self.t, paused=self.settings.paused, sim_speed=self.settings.sim_speed,
            runway=self.settings.runway, alerts=[], backlog={"ARR": 0, "DEP": 0},
            aircraft=[self._public(ac) for ac in self.aircraft.values()],
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
            next_fix=ac.route[ac.route_idx] if ac.route_idx < len(ac.route) else None,
            approach_cleared=f"{appr['type']} {appr['runway']}" if appr else None,
            established=ac.established, squawk=ac.squawk, on_frequency=ac.on_frequency,
            in_jurisdiction=ac.in_jurisdiction, emergency=ac.emergency,
            transmitting=ac.transmitting, plain_status=self._plain_status(ac),
        )

    def snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(
            t=self.t, alerts=[],
            aircraft={cs: ac.model_dump() for cs, ac in self.aircraft.items()},
        )

    def callsigns(self) -> list[CallsignInfo]:
        return [
            CallsignInfo(icao=cs, telephony=self._telephony(cs), spoken=ac.spoken,
                         spoken_digits=self._spoken(cs, digits=True),
                         on_frequency=ac.on_frequency)
            for cs, ac in self.aircraft.items() if ac.on_frequency
        ]

    def fixes(self) -> list[str]:
        return sorted(self.egkk["fixes"])

    # ---------------------------------------------------------- helpers
    def _emit(self, type_: EventType, callsign: Optional[str], data: dict[str, Any],
              actor: str = "SYSTEM") -> None:
        self.bus.emit(Event(t=self.t, type=type_, actor=actor, callsign=callsign, data=data))

    @staticmethod
    def _tas(ias: float, alt: float) -> float:
        return ias * (1.0 + 0.02 * alt / 1000.0)

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

    def _spoken(self, callsign: str, digits: bool = False) -> str:
        """'speedbird one one seven' (or 'speedbird 117' with digits=True); suffix letters phonetic."""
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

    def _spoken_plain(self, callsign: str) -> str:
        prefix, rest = self._split(callsign)
        name = self.telephony.get("_airline_names", {}).get(prefix)
        return f"{name} {rest}" if name else callsign

    def _plain_status(self, ac: AircraftState) -> str:
        def level(ft: float) -> str:
            if ft > TRANSITION_ALT_FT:
                return f"FL{round(ft / 100):03d}"
            return f"{round(ft):,} ft"
        if abs(ac.cleared_alt - ac.alt) < 50:
            vertical = f"level {level(ac.cleared_alt)}"
        elif ac.cleared_alt > ac.alt:
            vertical = f"climbing to {level(ac.cleared_alt)}"
        else:
            vertical = f"descending to {level(ac.cleared_alt)}"
        return f"{vertical} · {round(ac.ias)} kt"
