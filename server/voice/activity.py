"""Live activity feed for the /pipeline screen (15 Nov, Sanju).

Every screen connected to the session (radar, /viz, /pipeline, any device on the LAN) receives an
``ActivityMsg`` when:
  * the controller keys or releases the mic (node ``mic``);
  * an external API call starts and ends: ElevenLabs Scribe (``stt``), fal Wizper (``stt``),
    Claude (``claude``: parser fallback, coach tip, debrief), ElevenLabs Flash or fal Chatterbox
    (``voice``: readbacks, ATIS, warm-ups);
  * a local stage finishes (``parse``, ``sim``, ``pilot``, ``readback``), with its measured time;
  * a pilot declares MAYDAY or PAN PAN (``pilot``, status ``emergency``);
  * pilot audio goes on and off the air (``radio``).

No audio, no transcript text and no keys are ever put in an activity message: only names,
timings and the callsign. Like tracing, this fails open: nothing here can break the radio.

Usage::

    activity.attach(hub)                           # once, by the voice pipeline
    activity.emit("parse", "Python grammar", "parse", ms=0.4, tx_id=tx_id)
    async with activity.call("claude", "Claude Haiku 5.5", "coach tip", tx_id=tx_id):
        ...
"""
from __future__ import annotations

import itertools
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from server.contracts import ActivityMsg

logger = logging.getLogger(__name__)

_hub: Any = None
_ids = itertools.count(1)

# Display names (the page shows these; keep them short)
SCRIBE = "ElevenLabs Scribe v2 Realtime"
WIZPER = "fal Wizper"
CLAUDE = "Claude Haiku 5.5"
FLASH = "ElevenLabs Flash v2.5"
CHATTERBOX = "fal Chatterbox"


def attach(hub: Any) -> None:
    """Send activity to this hub (the last pipeline created wins: one session per server)."""
    global _hub
    _hub = hub


def detach(hub: Any = None) -> None:
    global _hub
    if hub is None or _hub is hub:
        _hub = None


def new_id() -> str:
    return f"a{next(_ids)}"


def emit(node: str, service: str, op: str, *, phase: str = "instant", status: str = "ok",
         id: Optional[str] = None, ms: Optional[float] = None, tx_id: Optional[str] = None,
         callsign: Optional[str] = None, **detail: Any) -> Optional[str]:
    """Queue one ActivityMsg to every screen. Never raises; returns the activity id."""
    hub = _hub
    if hub is None:
        return None
    try:
        aid = id or new_id()
        msg = ActivityMsg(id=aid, node=node, service=service, op=op, phase=phase, status=status,
                          ms=None if ms is None else round(float(ms), 1), tx_id=tx_id, callsign=callsign,
                          at_ms=round(time.time() * 1000), detail={k: v for k, v in detail.items() if v is not None})
        hub.spawn(hub.send(msg))
        return aid
    except Exception:                                    # noqa: BLE001  (fail open)
        logger.debug("activity %s/%s dropped", node, op, exc_info=True)
        return None


class Span:
    """A started call; ``end`` sends its duration. Use when start and end are in different places."""

    def __init__(self, node: str, service: str, op: str, **kw: Any):
        self.node, self.service, self.op, self.kw = node, service, op, kw
        self.t0 = time.perf_counter()
        self.done = False
        self.id = emit(node, service, op, phase="start", **kw) or new_id()

    def end(self, status: str = "ok", ms: Optional[float] = None, **detail: Any) -> None:
        """``ms`` overrides the shown time (e.g. live STT: release -> final, not the whole stream)."""
        if self.done:
            return
        self.done = True
        total = (time.perf_counter() - self.t0) * 1000
        emit(self.node, self.service, self.op, phase="end", status=status, id=self.id,
             ms=total if ms is None else ms, **{**self.kw, **detail})


@asynccontextmanager
async def call(node: str, service: str, op: str, **kw: Any) -> AsyncIterator[Span]:
    """Time one API call: start now, end (ok / error) when the block exits."""
    span = Span(node, service, op, **kw)
    try:
        yield span
    except BaseException:
        span.end("error")
        raise
    span.end("ok")
