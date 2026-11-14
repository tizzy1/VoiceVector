"""Per-aircraft believed clearance (VOICE_PIPELINE §14): the active ClearanceElement per type."""
from __future__ import annotations

from typing import Optional

from server.contracts import ClearanceElement, Instruction

LEVEL_TYPES = ("altitude", "flight_level", "maintain_level")
LATERAL_TYPES = ("heading", "turn_by", "present_heading", "direct", "resume_nav", "hold")


def family(t: str) -> str:
    """Instructions that replace each other share a family (FL vs altitude, heading vs direct)."""
    if t in LEVEL_TYPES:
        return "level"
    if t in LATERAL_TYPES:
        return "lateral"
    return t


def level_ft(ins: Instruction) -> Optional[float]:
    if ins.value is None or ins.type not in LEVEL_TYPES:
        return None
    v = float(ins.value)
    if ins.type == "flight_level":
        return v * 100.0
    return v if (ins.type == "altitude" or v >= 1000) else v * 100.0


def same_content(a: Instruction, b: Instruction) -> bool:
    """Would a pilot hearing `a` think it is the same clearance as `b`?"""
    if family(a.type) == "level" and family(b.type) == "level":
        return a.type == b.type and level_ft(a) == level_ft(b)
    if a.type != b.type:
        return False
    if a.type == "heading":
        return (float(a.value or -1) == float(b.value or -1)
                and (a.turn_dir is None or a.turn_dir == b.turn_dir))
    return (a.value, a.fix, a.runway, a.turn_dir) == (b.value, b.fix, b.runway, b.turn_dir)


class ClearanceBook:
    def __init__(self) -> None:
        self.active: dict[str, dict[str, ClearanceElement]] = {}   # cs -> family -> element
        self.last_full: dict[str, list[ClearanceElement]] = {}

    def record(self, cs: str, elements: list[ClearanceElement]) -> None:
        book = self.active.setdefault(cs, {})
        for el in elements:
            old = book.get(family(el.type))
            if old is not None:
                old.status = "superseded"
            book[family(el.type)] = el
        if elements:
            self.last_full[cs] = elements

    def match(self, cs: str, ins: Instruction) -> Optional[ClearanceElement]:
        return self.active.get(cs, {}).get(family(ins.type))

    def believed(self, cs: str) -> list[ClearanceElement]:
        return list(self.active.get(cs, {}).values())

    def forget(self, cs: str) -> None:
        self.active.pop(cs, None)
        self.last_full.pop(cs, None)
