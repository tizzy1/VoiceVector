"""PilotEngine — pilot decisions, errors and corrections (owner: Tudor).

STUB STAGE (CONTRACTS §6): one full readback per transmission, `plan.elements` mirror the
controller's instructions, `apply` flies exactly what was said. No errors, no corrections yet.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from server.contracts import (
    ClearanceElement, ErrorKind, ErrorMode, ErrorRate, Evaluation, Event, EventBus,
    PilotResponse, ReadbackPlan, Transmission, WorldAPI,
)

REACTION_DELAY_S: dict[str, tuple[float, float]] = {
    "crisp_airline": (0.8, 1.2),
    "calm_longhaul": (1.0, 1.5),
    "student": (1.5, 2.5),
    "rushed": (0.5, 0.9),
    "stressed": (0.4, 0.8),
}
DEFAULT_PERSONA = "crisp_airline"


class PilotEngine:
    def __init__(self, world: WorldAPI, bus: EventBus, data_dir: Path) -> None:
        self.world = world
        self.bus = bus
        self.data_dir = Path(data_dir)
        self.error_rate: ErrorRate = "low"
        self._forced: Optional[tuple[ErrorMode, Optional[ErrorKind], Optional[str]]] = None
        self._rng = random.Random(getattr(world, "seed", 42) + 2)
        self._n = 0

    # ----------------------------------------------------------- helpers
    def _persona(self, callsign: str) -> str:
        ac = self.world.snapshot().aircraft.get(callsign, {})
        persona = ac.get("persona", DEFAULT_PERSONA)
        return persona if persona in REACTION_DELAY_S else DEFAULT_PERSONA

    def _reaction(self, persona: str) -> float:
        return round(self._rng.uniform(*REACTION_DELAY_S[persona]), 2)

    def _plan(self, callsign: str, kind: str, **kw) -> ReadbackPlan:
        self._n += 1
        return ReadbackPlan(callsign=callsign, persona=self._persona(callsign), kind=kind,
                            seed=getattr(self.world, "seed", 42) * 1000 + self._n, **kw)

    # ------------------------------------------------------------- API
    def handle_transmission(self, tx: Transmission,
                            evaluations: list[Evaluation]) -> list[PilotResponse]:
        cs = tx.receiver
        on_freq = {c.icao for c in self.world.callsigns()}
        if cs is None or cs not in on_freq or not tx.instructions:
            return []                                  # silence
        persona = self._persona(cs)

        refused = next((e for e in evaluations if e.status != "OK"), None)
        if refused is not None:
            kind = "unable" if refused.status == "UNABLE" else "query"
            plan = self._plan(cs, kind, reason_code=refused.reason_code,
                              reason_values=refused.reason_values)
            return [PilotResponse(callsign=cs, kind=kind, plan=plan, in_reply_to=tx.tx_id,
                                  reaction_delay_s=self._reaction(persona))]

        plan = self._plan(cs, "full", elements=[i.model_copy() for i in tx.instructions])
        apply = [ClearanceElement(type=i.type, intended=i.model_copy(), readback=i.model_copy(),
                                  flown=i.model_copy()) for i in tx.instructions]
        return [PilotResponse(
            callsign=cs, kind="full", plan=plan, apply=apply, in_reply_to=tx.tx_id,
            reaction_delay_s=self._reaction(persona),
            apply_delay_s=round(self._rng.uniform(0.5, 2.0), 2),
        )]

    def on_sim_event(self, e: Event) -> Optional[PilotResponse]:
        if e.type != "pilot_call" or not e.callsign:
            return None
        kind = e.data.get("kind", "checkin")
        values = {k: v for k, v in e.data.items() if k != "kind"}
        plan = self._plan(e.callsign, kind, report_values=values)
        return PilotResponse(callsign=e.callsign, kind=kind, plan=plan,
                             reaction_delay_s=self._reaction(self._persona(e.callsign)),
                             priority=10 if kind in ("mayday", "pan") else 0)

    def on_readback_spoken(self, resp: PilotResponse, t_end: float) -> None:
        return None                                    # no errors to track yet

    def tick(self, t: float) -> None:
        return None                                    # no timers yet

    def on_blocked(self, callsign: str, tx_id: str) -> Optional[PilotResponse]:
        on_freq = {c.icao for c in self.world.callsigns()}
        if callsign not in on_freq:
            return None
        plan = self._plan(callsign, "recall")
        return PilotResponse(callsign=callsign, kind="recall", plan=plan, in_reply_to=tx_id,
                             reaction_delay_s=round(self._rng.uniform(3.0, 6.0), 2))

    def set_error_rate(self, rate: ErrorRate) -> None:
        self.error_rate = rate

    def force_error(self, mode: ErrorMode, kind: Optional[ErrorKind] = None,
                    callsign: Optional[str] = None) -> None:
        self._forced = (mode, kind, callsign)          # stored; error policy not built yet
