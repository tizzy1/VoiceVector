"""D/E-groups: did the controller catch the pilot's planned errors? (SIM_SPEC §3.2 D/E, §3.3).

The pilot engine owns the error lifecycle and emits error_caught / error_missed; the pilot's
deviation itself is actor=PILOT, the controller is judged on catching it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.contracts import ErrorRecord, Event

from server.rules.common import mmss

if TYPE_CHECKING:
    from server.rules.engine import RulesEngine

CAUGHT_HOW = {"readback_correction": "corrected the readback",
              "execution_correction": "corrected the aircraft",
              "confirm_request": "asked the pilot to confirm",
              "readback_request": "asked for a read back"}


def _value(v: Any) -> str:
    if isinstance(v, dict):
        if "FL" in v:
            return f"FL{v['FL']}"
        side = {"L": "left ", "R": "right "}.get(v.get("turn_dir") or "", "")
        return f"{side}{v.get('value')}"
    return "?" if v is None else str(v)


class MonitoringRules:
    def __init__(self, eng: "RulesEngine") -> None:
        self.eng = eng
        self.errors: dict[str, ErrorRecord] = {}

    def on_event(self, e: Event) -> None:
        if e.type in ("error_planned", "error_spoken", "error_caught", "error_missed"):
            try:
                rec = ErrorRecord.model_validate({k: v for k, v in e.data.items()
                                                  if k in ErrorRecord.model_fields})
            except Exception:
                return
            self.errors[rec.id] = rec
            if e.type == "error_caught":
                self._caught(rec, e)
            elif e.type == "error_missed":
                self._missed(rec, e)
        elif e.type == "finding" and e.data.get("rule_id") == "D5" and "source" not in e.data:
            self.eng.add(
                "D5", "LOW", "CONTROLLER", [e.callsign] if e.callsign else [], t=e.t,
                tx_id=e.tx_id or e.data.get("tx_id"), emit=False,
                title=e.data.get("title") or "Unnecessary correction",
                detail=e.data.get("detail") or "The readback was already correct.",
                correct_practice="Only correct a readback that was actually wrong.")

    def _caught(self, rec: ErrorRecord, e: Event) -> None:
        execution = rec.mode == "execution_only"
        rid = "E1" if execution else "D1"
        what = "Execution error" if execution else "Readback error"
        if rec.kind == "level_bust_overshoot":
            rid, what = "E3", "Level bust"
        self.eng.add(
            rid, "POSITIVE", "CONTROLLER", [rec.callsign], t=e.t, tx_id=e.tx_id,
            title=f"{what} caught: {rec.callsign}",
            detail=f"Pilot had {_value(rec.readback_value if not execution else rec.flown_value)}"
                   f" instead of {_value(rec.intended)}; controller "
                   f"{CAUGHT_HOW.get(rec.caught_by or '', 'corrected it')} at {mmss(e.t)}.",
            correct_practice="Good catch — listen to every readback and watch the radar "
                             "for compliance.",
            evidence={"error_id": rec.id, "kind": rec.kind, "mode": rec.mode})

    def _missed(self, rec: ErrorRecord, e: Event) -> None:
        if rec.kind == "omitted_element":
            self.eng.add(
                "D3", "MEDIUM", "CONTROLLER", [rec.callsign], t=e.t, tx_id=e.tx_id,
                title=f"Incomplete readback accepted: {rec.callsign}",
                detail=f"The pilot left out the {rec.element_type.replace('_', ' ')} "
                       f"({_value(rec.intended)}) and it was not challenged.",
                correct_practice="Make sure every safety element is read back; if one is "
                                 "missing, ask for it, e.g. 'read back the heading'.",
                evidence={"error_id": rec.id, "kind": rec.kind, "mode": rec.mode,
                          "reason": e.data.get("reason")})
            return
        execution = rec.mode == "execution_only"
        rid = "E2" if execution else "D2"
        what = "Execution error" if execution else "Readback error"
        wrong = rec.flown_value if execution else rec.readback_value
        self.eng.add(
            rid, "MEDIUM", "CONTROLLER", [rec.callsign], t=e.t, tx_id=e.tx_id,
            title=f"{what} missed: {rec.callsign}",
            detail=f"Pilot had {_value(wrong)} instead of {_value(rec.intended)} "
                   f"({rec.kind.replace('_', ' ')}); not corrected.",
            correct_practice=("Watch the radar after every instruction and correct a wrong "
                              "turn or level straight away." if execution else
                              "Check every element of a readback; if anything is wrong, say "
                              "'negative' and repeat the correct value."),
            evidence={"error_id": rec.id, "kind": rec.kind, "mode": rec.mode,
                      "reason": e.data.get("reason")})
        # The PILOT's deviation itself (SIM_SPEC §3.3), unscored.
        self.eng.add(
            rid, "INFO", "PILOT", [rec.callsign], t=rec.t_spoken or rec.t_planned,
            title=f"Pilot {'flew' if execution else 'read back'} the wrong value: {rec.callsign}",
            detail=f"{rec.kind.replace('_', ' ')}: {_value(wrong)} instead of "
                   f"{_value(rec.intended)}.",
            evidence={"error_id": rec.id})

    def catch_table(self) -> list[dict[str, Any]]:
        rows = []
        for r in sorted(self.errors.values(), key=lambda r: r.t_planned):
            rows.append({
                "error_id": r.id, "callsign": r.callsign, "kind": r.kind, "mode": r.mode,
                "element": r.element_type, "intended": _value(r.intended),
                "readback": _value(r.readback_value), "flown": _value(r.flown_value),
                "outcome": r.outcome, "caught_by": r.caught_by,
                "time_to_catch_s": (round(r.t_caught - (r.t_spoken or r.t_planned), 1)
                                    if r.t_caught is not None else None)})
        return rows
