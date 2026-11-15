"""Stage timestamps and rolling latency percentiles (VOICE_PIPELINE §4).

Stamps are time.perf_counter() seconds (the pipeline-trace clock, VOICE_PIPELINE §20.1). ``t_play`` is the moment the pilot's audio is released
to the browsers (``t_release`` is the same instant under its spec name).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional

STAGES = ("t_ptt_down", "t_ptt_up", "t_final", "t_parsed", "t_plan", "t_tts_req",
          "t_tts_first", "t_release", "t_play", "t_audio_end")


def percentile(values: list[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


class Stamps(dict):
    """``stamps.mark("t_final")`` records ``time.perf_counter()``; ``stamps.span(a, b)`` is b - a."""

    def mark(self, stage: str, at: Optional[float] = None) -> float:
        value = time.perf_counter() if at is None else at
        self[stage] = value
        return value

    def span(self, start: str, end: str) -> Optional[float]:
        if start in self and end in self:
            return self[end] - self[start]
        return None


class Metrics:
    """Rolling windows for the on-screen "machine / perceived" latency readout."""

    def __init__(self, window: int = 60):
        self._series: dict[str, deque[float]] = {}
        self._window = window

    def record(self, name: str, value: Optional[float]) -> None:
        if value is None or value < 0:
            return
        self._series.setdefault(name, deque(maxlen=self._window)).append(value)

    def summary(self) -> dict[str, dict[str, Optional[float]]]:
        return {
            name: {"p50": percentile(list(vals), 50), "p90": percentile(list(vals), 90), "n": len(vals)}
            for name, vals in self._series.items()
        }
