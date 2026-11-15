"""Live pipeline trace (VOICE_PIPELINE §20.1; shapes in CONTRACTS §3.1).

Every number is a server measurement: stage edges are ``Stamps`` marks (``time.perf_counter``,
ns resolution) taken where the work happens. The anchor is PTT up for a controller transmission
and compose start for a pilot-initiated call, so speech before PTT up has a negative ``t0_ms``.

Tracing fails open: every public method swallows its own exceptions, so a tracing bug can never
interrupt the voice path. No audio and no transcript text are put in a trace.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from server.contracts import PipelineTrace, TraceMsg, TraceStage

logger = logging.getLogger(__name__)

# name -> category, in panel order (VOICE_PIPELINE §20.1, exact names)
STAGES: dict[str, str] = {
    "speech": "capture",
    "stt_partials": "stt",
    "stt_final": "stt",
    "normalise": "language",
    "parse": "language",
    "llm_fallback": "language",
    "callsign_match": "language",
    "evaluate": "sim",
    "pilot_decision": "pilot",
    "compose": "voice",
    "validate": "voice",
    "tts_first_audio": "voice",
    "reaction_hold": "playback",
    "playback": "playback",
    "apply_scheduled": "sim",
    "blocked": "sim",
}


class Trace:
    """One transmission's waterfall. ``add`` takes perf_counter seconds; ``msg`` builds the TraceMsg."""

    def __init__(self, tx_id: str, speaker: str, anchor: float, reply_callsign: Optional[str] = None):
        self.tx_id, self.speaker, self.anchor = tx_id, speaker, anchor
        self.reply_callsign = reply_callsign
        self.stages: list[TraceStage] = []
        self.total_ms = 0.0
        self.machine_ms: Optional[float] = None
        self.sent_final = False

    def ms(self, t: float) -> float:
        return round((t - self.anchor) * 1000.0, 2)

    def add(self, name: str, t0: Optional[float], t1: Optional[float], status: str = "ok",
            **detail: Any) -> None:
        try:
            if t0 is None or t1 is None:
                return
            if t1 < t0:                                   # never a negative bar
                t1 = t0
            self.stages = [s for s in self.stages if s.name != name]
            self.stages.append(TraceStage(name=name, category=STAGES[name], t0_ms=self.ms(t0),
                                          t1_ms=self.ms(t1), status=status,
                                          detail={k: v for k, v in detail.items() if v is not None}))
            order = list(STAGES)
            self.stages.sort(key=lambda s: order.index(s.name))
        except Exception:                                 # noqa: BLE001  (fail open)
            logger.exception("trace stage %s dropped", name)

    def copy_for(self, tx_id: str, reply_callsign: Optional[str]) -> "Trace":
        """The controller-side stages, shared by every pilot who answers one transmission."""
        other = Trace(tx_id, self.speaker, self.anchor, reply_callsign)
        other.stages = list(self.stages)
        return other

    def msg(self, final: bool) -> TraceMsg:
        return TraceMsg(trace=PipelineTrace(tx_id=self.tx_id, speaker=self.speaker,
                                            reply_callsign=self.reply_callsign, stages=list(self.stages),
                                            total_ms=max(0.0, round(self.total_ms, 2)), final=final))


async def send(hub: Any, trace: Optional[Trace], final: bool, log_dir: Optional[Path]) -> None:
    """Send a provisional or final trace; append final ones to ``<session>/traces.jsonl``. Never raises."""
    if trace is None or trace.sent_final:
        return
    try:
        msg = trace.msg(final)
        if final:
            trace.sent_final = True
            if log_dir is not None:
                with (log_dir / "traces.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(msg.trace.model_dump(mode="json")) + "\n")
        await hub.send(msg)
    except Exception:                                     # noqa: BLE001  (fail open)
        logger.exception("trace %s not sent", getattr(trace, "tx_id", "?"))


def session_dir(bus: Any) -> Optional[Path]:
    """runs/<session>/ from the session's EventLog (events.jsonl lives there); None in tests."""
    try:
        name = getattr(getattr(bus, "file", None), "name", None)
        return Path(name).parent if name else None
    except Exception:                                     # noqa: BLE001
        return None
