"""A-group: continuous safety rules, run every tick (SIM_SPEC §3.2 A)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.sim.aircraft import AircraftState
from server.sim.airspace import area_minimum_ft, cas_base_ft, in_cas
from server.sim.modes import loc_geometry
from server.sim.stca import _in_trail_ok, separation

from server.rules.common import mmss

if TYPE_CHECKING:
    from server.rules.engine import RulesEngine

PRED_IGNORED_S = 60          # A2: no relevant instruction within 60 s of the prediction
MIN_ALT_TOL_FT = 50          # A4: ignore tiny dips during level-off
AIRBORNE_SKIP = {"QUEUED", "TAKEOFF", "LANDED", "HANDED_OFF", "GO_AROUND"}


class SafetyRules:
    def __init__(self, eng: "RulesEngine") -> None:
        self.eng = eng
        self.los: dict[tuple[str, str], dict[str, Any]] = {}     # open A1 episodes
        self.los_closed: list[dict[str, Any]] = []
        self.pred: dict[tuple[str, str], dict[str, Any]] = {}    # open A2 predictions
        self.wake: dict[tuple[str, str], dict[str, Any]] = {}    # open A3 episodes
        self.below_min: set[str] = set()                         # open A4 episodes
        self.cas_seen: set[str] = set()                          # been inside CAS (A5)
        self.outside_cas: set[str] = set()                       # open A5 episodes
        sep = eng.traffic.get("separation", {})
        self.min_nm = float(sep.get("radar_min_nm", 3.0))
        self.min_ft = float(sep.get("vertical_min_ft", 1000))
        self.wake_table: dict[str, float] = {
            k: float(v) for k, v in sep.get("final_approach_wake_nm", {}).items()
            if k != "default"}

    # ------------------------------------------------------------------ tick
    def tick(self, t: float, acs: list[AircraftState]) -> None:
        live = [a for a in acs if a.phase not in ("LANDED", "HANDED_OFF", "QUEUED", "TAKEOFF")]
        self._a1(t, live)
        self._a2(t)
        self._a3(t, live)
        self._a4(t, live)
        self._a5(t, live)

    # A5 outside controlled airspace -------------------------------------------
    def _a5(self, t: float, acs: list[AircraftState]) -> None:
        """An aircraft that was inside controlled airspace leaves it (laterally or below the
        base) while under our control. Only aircraft the controller has been working count:
        one flying its own SID/STAR, hold or default track (never instructed) is the sim's
        doing, and an untransferred departure is already B10."""
        w = self.eng.world
        for a in acs:
            cs = a.callsign
            inside = in_cas(w, a.x, a.y, a.alt)
            if inside:
                self.cas_seen.add(cs)
                self.outside_cas.discard(cs)
                continue
            own_procedure = a.lat_mode == "LNAV" and a.route_name and a.phase in ("STAR", "SID")
            if (cs not in self.cas_seen or cs in self.outside_cas or not a.on_frequency
                    or own_procedure or a.lat_mode in ("LOC", "HOLD")
                    or cs not in self.eng.last_tx_to):
                continue
            self.outside_cas.add(cs)
            base = cas_base_ft(w, a.x, a.y)
            where = (f"below the controlled-airspace base of {base} ft" if base is not None
                     and a.alt < base else "outside the lateral limits of controlled airspace")
            self.eng.add(
                "A5", "HIGH", "CONTROLLER", [cs], t=t,
                title=f"Outside controlled airspace: {cs}",
                detail=f"At {int(round(a.alt, -2))} ft, {where}.",
                correct_practice="Keep IFR traffic inside controlled airspace: check the base "
                                 "of the airspace before descending and the boundary before "
                                 "vectoring.",
                evidence={"alt": round(a.alt), "base_ft": base, "x": round(a.x, 1),
                          "y": round(a.y, 1)})

    # A1 loss of radar separation --------------------------------------------
    def _a1(self, t: float, acs: list[AircraftState]) -> None:
        now: set[tuple[str, str]] = set()
        for i, a in enumerate(acs):
            for b in acs[i + 1:]:
                d, dalt = separation(a, b)
                if d < self.min_nm and dalt < self.min_ft and not _in_trail_ok(a, b, d):
                    key = tuple(sorted((a.callsign, b.callsign)))
                    now.add(key)
                    ep = self.los.setdefault(key, {"start": t, "min_nm": d, "min_ft": dalt,
                                                   "t_min": t})
                    if d < ep["min_nm"]:
                        ep.update(min_nm=d, min_ft=dalt, t_min=t)
        for key in [k for k in self.los if k not in now]:
            self._close_los(key, t)

    def _close_los(self, key: tuple[str, str], t_end: float | None) -> None:
        ep = self.los.pop(key)
        rec = {"callsigns": list(key), "start": ep["start"], "end": t_end,
               "min_nm": round(ep["min_nm"], 2), "min_ft": int(round(ep["min_ft"], -1)),
               "t_min": ep["t_min"]}
        self.los_closed.append(rec)
        self.eng.add(
            "A1", "HIGH", "CONTROLLER", list(key), t=ep["start"],
            title=f"Loss of separation: {key[0]} / {key[1]}",
            detail=(f"Minimum {rec['min_nm']:.1f} NM / {rec['min_ft']} ft at {mmss(ep['t_min'])}"
                    f" (from {mmss(ep['start'])} to "
                    f"{mmss(t_end) if t_end is not None else 'session end'})"),
            correct_practice="Keep at least 3 NM or 1,000 ft between IFR aircraft; act on the "
                             "STCA prediction before it becomes a loss.",
            evidence=rec)

    # A2 predicted conflict ignored ------------------------------------------
    def _a2(self, t: float) -> None:
        active = {tuple(sorted(a.callsigns)) for a in self.eng.world_alerts()
                  if a.kind == "STCA_PRED"}
        for key in active:
            ep = self.pred.setdefault(key, {"start": t, "flagged": False})
            if ep["flagged"] or t - ep["start"] < PRED_IGNORED_S:
                continue
            last = max(self.eng.last_tx_to.get(cs, -1e9) for cs in key)
            if last < ep["start"]:
                ep["flagged"] = True
                self.eng.add(
                    "A2", "HIGH", "CONTROLLER", list(key), t=t,
                    title=f"Predicted conflict ignored: {key[0]} / {key[1]}",
                    detail=f"STCA prediction from {mmss(ep['start'])}; no instruction to "
                           f"either aircraft within {PRED_IGNORED_S} s.",
                    correct_practice="Resolve a predicted conflict straight away with a "
                                     "heading or level change to one of the aircraft.")
        for key in [k for k in self.pred if k not in active]:
            del self.pred[key]

    # A3 wake on final ---------------------------------------------------------
    def _a3(self, t: float, acs: list[AircraftState]) -> None:
        w = self.eng.world
        final = []
        for a in acs:
            if a.flow != "ARR" or not (a.established or a.lat_mode == "LOC"):
                continue
            along, _ = loc_geometry(w, a)
            if along > 0:
                final.append((along, a))
        final.sort(key=lambda p: p[0])
        now: set[tuple[str, str]] = set()
        for (la, lead), (fa, foll) in zip(final, final[1:]):
            need = self.wake_table.get(f"{lead.wake}>{foll.wake}")
            if need is None:
                continue
            gap = fa - la
            if gap < need:
                key = (lead.callsign, foll.callsign)
                now.add(key)
                ep = self.wake.setdefault(key, {"start": t, "min": gap, "need": need,
                                                "cat": f"{lead.wake}>{foll.wake}"})
                ep["min"] = min(ep["min"], gap)
        for key in [k for k in self.wake if k not in now]:
            self._close_wake(key)

    def _close_wake(self, key: tuple[str, str]) -> None:
        ep = self.wake.pop(key)
        self.eng.add(
            "A3", "HIGH", "CONTROLLER", list(key), t=ep["start"],
            title=f"Wake separation on final: {key[1]} behind {key[0]}",
            detail=f"Minimum {ep['min']:.1f} NM; {ep['cat']} needs {ep['need']:.0f} NM.",
            correct_practice="Space the follower at the wake minimum for the leader's "
                             "category plus a buffer before turning it onto the localiser.",
            evidence={"min_nm": round(ep["min"], 2), "required_nm": ep["need"],
                      "category": ep["cat"]})

    # A4 below minimum altitude -----------------------------------------------
    def _a4(self, t: float, acs: list[AircraftState]) -> None:
        w = self.eng.world
        now: set[str] = set()
        for a in acs:
            if a.phase in AIRBORNE_SKIP or a.established or a.vert_mode == "GS":
                continue
            if a.flow == "DEP" and a.vs >= 0:
                continue                                  # climbing out after take-off
            mn = area_minimum_ft(w, a)
            if a.alt < mn - MIN_ALT_TOL_FT:
                now.add(a.callsign)
                if a.callsign not in self.below_min:
                    self.eng.add(
                        "A4", "HIGH", "CONTROLLER", [a.callsign], t=t,
                        title=f"Below minimum altitude: {a.callsign}",
                        detail=f"{int(round(a.alt, -1))} ft where the minimum is {mn} ft.",
                        correct_practice="Do not leave an aircraft below the surveillance "
                                         "minimum altitude unless it is established on the "
                                         "approach.",
                        evidence={"alt": round(a.alt), "minimum": mn})
        self.below_min = now

    # ------------------------------------------------------------------ end
    def finish(self, t: float) -> None:
        """Close every open episode when the session ends (end time recorded as None)."""
        for key in list(self.los):
            self._close_los(key, None)
        for key in list(self.wake):
            self._close_wake(key)
