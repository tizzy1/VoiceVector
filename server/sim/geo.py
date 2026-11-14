"""Local flat projection and heading helpers.

x = NM east, y = NM north of the RWY 26L landing threshold (data/egkk.json _meta.origin).
Headings are degrees TRUE, 0-359.99.
"""
from __future__ import annotations

import math


class Projection:
    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0 = lat0
        self.lon0 = lon0
        self._cos = math.cos(math.radians(lat0))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * 60.0 * self._cos, (lat - self.lat0) * 60.0

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return self.lat0 + y / 60.0, self.lon0 + x / (60.0 * self._cos)


def norm_hdg(h: float) -> float:
    """Wrap to [0, 360)."""
    return h % 360.0


def hdg_diff(frm: float, to: float) -> float:
    """Signed shortest turn from `frm` to `to`, in (-180, 180]. Positive = right."""
    d = (to - frm) % 360.0
    return d - 360.0 if d > 180.0 else d


def turn_delta(frm: float, to: float, turn_dir: str | None) -> float:
    """Signed turn from `frm` to `to` honouring an explicit direction (may exceed 180)."""
    d = hdg_diff(frm, to)
    if turn_dir == "R" and d < 0:
        d += 360.0
    elif turn_dir == "L" and d > 0:
        d -= 360.0
    return d


def bearing(x0: float, y0: float, x1: float, y1: float) -> float:
    """True bearing from (x0, y0) to (x1, y1)."""
    return norm_hdg(math.degrees(math.atan2(x1 - x0, y1 - y0)))


def dist(x0: float, y0: float, x1: float, y1: float) -> float:
    return math.hypot(x1 - x0, y1 - y0)
