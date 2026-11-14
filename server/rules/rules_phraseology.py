"""C-group: phraseology, judged at transmission time (SIM_SPEC §3.2 C). All LOW."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from server.contracts import Transmission

from server.rules.common import LEVEL_TYPES

if TYPE_CHECKING:
    from server.rules.engine import RulesEngine

MAX_ELEMENTS = 3                                     # C5
LEVEL_WORDS = re.compile(r"\b(altitude|flight level|fl|level|feet)\b|\bfl\s?\d")
INSTRUCTION_INTENTS = ("instruction", "readback_correction")


class PhraseologyRules:
    def __init__(self, eng: "RulesEngine") -> None:
        self.eng = eng

    def on_tx(self, tx: Transmission) -> None:
        if tx.intent not in INSTRUCTION_INTENTS or not tx.instructions:
            return
        cs = [tx.receiver] if tx.receiver else []
        ev = {"tx_id": tx.tx_id, "transcript": tx.raw_text or tx.norm_text}
        add = lambda rid, title, detail, practice: self.eng.add(  # noqa: E731
            rid, "LOW", "CONTROLLER", cs, t=tx.t, tx_id=tx.tx_id, title=title,
            detail=detail, correct_practice=practice, evidence=ev)
        if not tx.receiver:
            add("C1", "Missing callsign", "The transmission did not identify an aircraft.",
                "Start every instruction with the full callsign.")
        text = (tx.norm_text or tx.raw_text or "").lower()
        for ins in tx.instructions:
            if ins.type not in LEVEL_TYPES:
                continue
            seg = text[ins.span[0]:ins.span[1]] if ins.span else text
            if not LEVEL_WORDS.search(seg):
                add("C2", f"Missing level type{': ' + tx.receiver if tx.receiver else ''}",
                    f"Level {ins.value} given without 'altitude' or 'flight level'.",
                    "Always say 'altitude' or 'flight level' with a level.")
                break
        if tx.parse_path in ("llm", "mixed"):
            add("C4", "Non-standard phrase",
                "Part of the transmission only made sense to the fallback parser.",
                "Use standard CAP 413 phraseology.")
        if len(tx.instructions) > MAX_ELEMENTS:
            add("C5", f"Overloaded transmission{': ' + tx.receiver if tx.receiver else ''}",
                f"{len(tx.instructions)} instructions in one transmission.",
                f"Keep to {MAX_ELEMENTS} instructions or fewer per transmission.")
