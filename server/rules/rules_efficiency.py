"""F-group: efficiency — scored as a bonus, never illegal (SIM_SPEC §3.2 F, §3.4)."""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, Optional

from server.contracts import Event
from server.sim.aircraft import AircraftState
from server.sim.modes import loc_geometry

from server.rules.common import EFFICIENCY_BONUS_MAX

if TYPE_CHECKING:
    from server.rules.engine import RulesEngine

SPACING_BUFFER_NM = 0.5          # target = wake minimum + 0.5 NM
SPACING_FULL_SCORE_NM = 0.5      # within this of target → full marks for that pair
SPACING_ZERO_SCORE_NM = 2.5      # this far off target → no marks


class EfficiencyRules:
    def __init__(self, eng: "RulesEngine") -> None:
        self.eng = eng
        sep = eng.traffic.get("separation", {})
        self.wake = sep.get("final_approach_wake_nm", {})
        self.radar_min = float(sep.get("radar_min_nm", 3.0))
        self.tx_per_ac: Counter[str] = Counter()
        self.hold_s: Counter[str] = Counter()
        self.spacing: list[dict[str, Any]] = []
        self._last_acs: dict[str, AircraftState] = {}

    def on_event(self, e: Event) -> None:
        if e.type == "ctrl_transmission" and e.callsign:
            self.tx_per_ac[e.callsign] += 1
        elif e.type == "landing" and e.callsign:
            self._spacing_at_landing(e.callsign, e.t)

    def tick(self, t: float, acs: list[AircraftState], dt: float) -> None:
        self._last_acs = {a.callsign: a for a in acs}
        for a in acs:
            if a.phase == "HOLD" or a.lat_mode == "HOLD":
                self.hold_s[a.callsign] += dt

    def _spacing_at_landing(self, leader_cs: str, t: float) -> None:
        lead = self._last_acs.get(leader_cs)
        if lead is None:
            return
        w = self.eng.world
        best: Optional[tuple[float, AircraftState]] = None
        for a in self._last_acs.values():
            if a.callsign == leader_cs or a.flow != "ARR" or not a.established:
                continue
            along, _ = loc_geometry(w, a)
            if along > 0 and (best is None or along < best[0]):
                best = (along, a)
        if best is None:
            return
        gap, foll = best
        target = float(self.wake.get(f"{lead.wake}>{foll.wake}", self.radar_min)) \
            + SPACING_BUFFER_NM
        self.spacing.append({"leader": leader_cs, "follower": foll.callsign, "t": t,
                             "gap_nm": round(gap, 2), "target_nm": target})

    def bonus(self) -> int:
        if not self.spacing:
            return 0
        marks = []
        for s in self.spacing:
            off = abs(s["gap_nm"] - s["target_nm"])
            if s["gap_nm"] < s["target_nm"] - SPACING_BUFFER_NM:
                marks.append(0.0)                       # tighter than the minimum: no credit
            elif off <= SPACING_FULL_SCORE_NM:
                marks.append(1.0)
            else:
                marks.append(max(0.0, 1 - (off - SPACING_FULL_SCORE_NM)
                                 / (SPACING_ZERO_SCORE_NM - SPACING_FULL_SCORE_NM)))
        return int(round(EFFICIENCY_BONUS_MAX * sum(marks) / len(marks)))

    def stats(self) -> dict[str, Any]:
        n = len(self.tx_per_ac)
        return {
            "final_spacing": self.spacing,
            "time_in_holds_s": {k: round(v) for k, v in self.hold_s.items()},
            "transmissions_per_aircraft": dict(self.tx_per_ac),
            "mean_transmissions_per_aircraft": (round(sum(self.tx_per_ac.values()) / n, 1)
                                                if n else 0),
            "bonus": self.bonus(),
        }
