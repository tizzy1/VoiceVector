"""RulesEngine — legality rules and the session report (owner: Tudor).

STUB STAGE: records events and returns a valid, empty report. No rules yet.
"""
from __future__ import annotations

from pathlib import Path

from server.contracts import Event, EventBus, Report, WorldAPI, WorldSnapshot

TIMELINE_TYPES = {"spawn", "landing", "takeoff", "handoff", "inject", "finding",
                  "error_caught", "error_missed", "alert_start", "alert_end"}


class RulesEngine:
    def __init__(self, world: WorldAPI, bus: EventBus, data_dir: Path) -> None:
        self.world = world
        self.bus = bus
        self.data_dir = Path(data_dir)
        self.events: list[Event] = []

    def on_event(self, e: Event) -> None:
        self.events.append(e)

    def on_tick(self, snap: WorldSnapshot) -> None:
        return None

    def report(self, session_id: str) -> Report:
        def count(type_: str) -> int:
            return sum(1 for e in self.events if e.type == type_)
        return Report(
            session_id=session_id, duration_s=self.world.t, score=100,
            score_breakdown={"base": 100}, findings=[], errors=[],
            stats={"transmissions": count("ctrl_transmission"),
                   "pilot_transmissions": count("pilot_transmission"),
                   "landings": count("landing"), "departures": count("takeoff")},
            timeline=[e for e in self.events if e.type in TIMELINE_TYPES],
        )
