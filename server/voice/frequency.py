"""Frequency manager + reply scheduler (VOICE_PIPELINE §5).

Pure logic, no I/O. Owns who is transmitting:
  * CTRL keyed -> pilots do not start; queued replies wait.
  * CTRL keys while a pilot is mid-transmission -> both are blocked (the pipeline turns that into
    ``overlap`` / ``ctrl_blocked`` events and a pilot re-call).
  * Pilot transmissions are serialised with a 0.3-1.0 s gap; higher priority (mayday) goes first.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import random
import time
from typing import Callable, Optional

GAP_RANGE_S = (0.3, 1.0)


class FrequencyManager:
    def __init__(self, seed: int = 42, clock: Callable[[], float] = time.monotonic):
        self._rng = random.Random(seed + 7)
        self._clock = clock
        self.ctrl_keyed = False
        self.active: dict[str, str] = {}          # tx_id -> callsign (pilots on the air)
        self.blocked: set[str] = set()            # pilot tx_ids stepped on by the controller
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._seq = itertools.count()
        self._reserved = False                    # granted but not yet begun
        self._free_at = 0.0                       # earliest start of the next pilot (gap)
        self._timer: Optional[asyncio.TimerHandle] = None

    # ------------------------------------------------------------------ state
    def is_busy(self) -> bool:
        return self.ctrl_keyed or bool(self.active)

    # ------------------------------------------------------------- controller
    def ctrl_down(self) -> list[str]:
        """Controller keys. Returns the pilot tx_ids that were on the air (now blocked)."""
        self.ctrl_keyed = True
        stepped_on = list(self.active)
        self.blocked.update(stepped_on)
        return stepped_on

    def ctrl_up(self) -> None:
        self.ctrl_keyed = False
        self._free_at = max(self._free_at, self._clock() + self._rng.uniform(*GAP_RANGE_S))
        self._pump()

    # ----------------------------------------------------------------- pilots
    def begin(self, tx_id: str, callsign: str) -> None:
        self._reserved = False
        self.active[tx_id] = callsign

    def end(self, tx_id: str) -> bool:
        """A pilot transmission finished. Returns True if the controller had blocked it."""
        self.active.pop(tx_id, None)
        was_blocked = tx_id in self.blocked
        self.blocked.discard(tx_id)
        self._free_at = max(self._free_at, self._clock() + self._rng.uniform(*GAP_RANGE_S))
        self._pump()
        return was_blocked

    def is_blocked(self, tx_id: str) -> bool:
        return tx_id in self.blocked

    def cancel_reservation(self) -> None:
        """A granted turn was abandoned before ``begin`` (task cancelled / TTS failed)."""
        if self._reserved:
            self._reserved = False
            self._pump()

    async def wait_turn(self, priority: int = 0, not_before: float = 0.0) -> None:
        """Wait until this pilot may transmit. ``not_before`` is a monotonic time (reaction delay)."""
        delay = not_before - self._clock()
        if delay > 0:
            await asyncio.sleep(delay)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (-priority, next(self._seq), future))
        self._pump()
        await future

    def force_turn(self) -> None:
        """Deliberate overlap (inject force_overlap): the next pilot goes without waiting."""
        self._free_at = 0.0

    # ----------------------------------------------------------------- pumping
    def _pump(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        while self._waiters and self._waiters[0][2].done():
            heapq.heappop(self._waiters)             # cancelled waiter
        if not self._waiters or self.ctrl_keyed or self.active or self._reserved:
            return
        wait = self._free_at - self._clock()
        if wait > 0:
            self._timer = asyncio.get_running_loop().call_later(wait, self._pump)
            return
        _, _, future = heapq.heappop(self._waiters)
        self._reserved = True
        future.set_result(None)
