"""Shared helpers for the rule modules (owner: Tudor)."""
from __future__ import annotations

from typing import Any, Optional

from server.contracts import Transmission

CATEGORY = {"A": "A_SAFETY", "B": "B_INSTRUCTION", "C": "C_PHRASEOLOGY",
            "D": "D_READBACK", "E": "E_COMPLIANCE", "F": "F_EFFICIENCY"}

# SIM_SPEC §3.4
PENALTY = {"HIGH": -15, "MEDIUM": -5, "LOW": -1, "INFO": 0}
CATCH_BONUS = 5
CATCH_BONUS_CAP = 25
EFFICIENCY_BONUS_MAX = 20


def mmss(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60:02d}:{t % 60:02d}"


def as_tx(raw: Any) -> Optional[Transmission]:
    if raw is None:
        return None
    if isinstance(raw, Transmission):
        return raw
    try:
        return Transmission.model_validate(raw)
    except Exception:
        return None


def cleared_level_ft(ins) -> Optional[float]:
    if ins.value is None:
        return None
    try:
        v = float(ins.value)
    except (TypeError, ValueError):
        return None
    if ins.type == "flight_level":
        return v * 100.0
    if ins.type in ("altitude", "maintain_level"):
        return v if v >= 1000 else v * 100.0
    return None


LEVEL_TYPES = ("altitude", "flight_level", "maintain_level")
