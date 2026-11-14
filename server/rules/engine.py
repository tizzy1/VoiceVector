"""RulesEngine — legality rules and the session report (owner: Tudor). SIM_SPEC §3.

Findings are deterministic code; each is also emitted as a `finding` event for the feed.
`report()` can be called whenever the session ends (any time) and closes open episodes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from server.contracts import (Actor, Alert, Evaluation, Event, EventBus, Finding, Report,
                              Severity, WorldAPI, WorldSnapshot)
from server.sim.aircraft import AircraftState

from server.rules import report as report_mod
from server.rules.common import CATEGORY, as_tx
from server.rules.rules_efficiency import EfficiencyRules
from server.rules.rules_instruction import InstructionRules
from server.rules.rules_monitoring import MonitoringRules
from server.rules.rules_phraseology import PhraseologyRules
from server.rules.rules_safety import SafetyRules

TIMELINE_TYPES = report_mod.TIMELINE_TYPES


class RulesEngine:
    def __init__(self, world: WorldAPI, bus: EventBus, data_dir: Path) -> None:
        self.world: Any = world
        self.bus = bus
        self.data_dir = Path(data_dir)
        self.traffic = json.loads((self.data_dir / "traffic.json").read_text())
        self.events: list[Event] = []
        self.findings: list[Finding] = []
        self.last_tx_to: dict[str, float] = {}
        self.tx_time: dict[str, float] = {}
        self.tx_timings: list[dict[str, float]] = []
        self._last_t: Optional[float] = None
        self._alerts: list[Alert] = []
        self.safety = SafetyRules(self)
        self.instruction = InstructionRules(self)
        self.phraseology = PhraseologyRules(self)
        self.monitoring = MonitoringRules(self)
        self.efficiency = EfficiencyRules(self)

    # ------------------------------------------------------------- findings
    def add(self, rule_id: str, severity: Severity, actor: Actor, callsigns: list[str], *,
            t: float, title: str, detail: str, correct_practice: str = "",
            evidence: Optional[dict[str, Any]] = None, tx_id: Optional[str] = None,
            emit: bool = True) -> Finding:
        f = Finding(id=f"F{len(self.findings) + 1:03d}", rule_id=rule_id,
                    category=CATEGORY[rule_id[0]], severity=severity, t=round(t, 1),
                    actor=actor, callsigns=callsigns, title=title, detail=detail,
                    correct_practice=correct_practice,
                    evidence={**({"tx_id": tx_id} if tx_id else {}), **(evidence or {})})
        self.findings.append(f)
        if emit:
            e = Event(t=t, type="finding", actor=actor if actor != "PILOT" else "SYSTEM",
                      callsign=callsigns[0] if callsigns else None, tx_id=tx_id,
                      data={**f.model_dump(), "source": "rules"})
            self.bus.emit(e)
        return f

    def world_alerts(self) -> list[Alert]:
        return self._alerts

    # ---------------------------------------------------------------- events
    def on_event(self, e: Event) -> None:
        self.events.append(e)
        if e.type == "finding" and e.data.get("source") == "rules":
            return
        if e.type == "ctrl_transmission":
            self._on_tx(e)
        self.monitoring.on_event(e)
        self.efficiency.on_event(e)

    def _on_tx(self, e: Event) -> None:
        tx = as_tx(e.data.get("tx"))
        if tx is None:
            return
        self.tx_time[tx.tx_id] = e.t
        if e.tx_id and e.tx_id != tx.tx_id:
            self.tx_time[e.tx_id] = e.t
        if tx.timings:
            self.tx_timings.append(dict(tx.timings))
        evals = []
        for raw in e.data.get("evals") or []:
            try:
                evals.append(raw if isinstance(raw, Evaluation) else Evaluation.model_validate(raw))
            except Exception:
                pass
        self.phraseology.on_tx(tx)
        if not tx.receiver:
            return
        self.last_tx_to[tx.receiver] = e.t
        ac = getattr(self.world, "aircraft", {}).get(tx.receiver)
        if ac is not None and tx.intent in ("instruction", "readback_correction"):
            self.instruction.on_tx(tx, evals, ac)

    # ------------------------------------------------------------------ tick
    def on_tick(self, snap: WorldSnapshot) -> None:
        dt = 0.0 if self._last_t is None else max(0.0, snap.t - self._last_t)
        self._last_t = snap.t
        self._alerts = list(snap.alerts)
        acs = [AircraftState.model_validate(d) for d in snap.aircraft.values()]
        self.safety.tick(snap.t, acs)
        self.instruction.tick(snap.t, acs)
        self.efficiency.tick(snap.t, acs, dt)

    # ---------------------------------------------------------------- report
    def report(self, session_id: str) -> Report:
        self.safety.finish(self.world.t)
        return report_mod.build(self, session_id)
