"""Deterministic end-of-session report (SIM_SPEC §3.4–3.5). No LLM here: the narrative is
written later from this report only (Sanju)."""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, Optional

from server.contracts import Report

from server.rules.common import CATCH_BONUS, CATCH_BONUS_CAP, PENALTY

if TYPE_CHECKING:
    from server.rules.engine import RulesEngine

TIMELINE_TYPES = {"spawn", "landing", "takeoff", "handoff", "go_around", "inject", "finding",
                  "error_caught", "error_missed", "alert_start", "alert_end",
                  "session_start", "session_end"}
BREAKDOWN_KEYS = {"A_SAFETY": "safety", "B_INSTRUCTION": "instructions",
                  "C_PHRASEOLOGY": "phraseology", "D_READBACK": "readback",
                  "E_COMPLIANCE": "compliance"}


def percentile(xs: list[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def _dist(xs: list[float]) -> dict[str, Any]:
    return {"n": len(xs), "p50": percentile(xs, 0.5), "p90": percentile(xs, 0.9)}


def score(findings, efficiency_bonus: int) -> tuple[int, dict[str, int]]:
    breakdown = {"base": 100, **{v: 0 for v in BREAKDOWN_KEYS.values()}}
    catches = 0
    for f in findings:
        if f.actor != "CONTROLLER":
            continue
        if f.severity == "POSITIVE":
            catches += CATCH_BONUS
        elif f.category in BREAKDOWN_KEYS:
            breakdown[BREAKDOWN_KEYS[f.category]] += PENALTY.get(f.severity, 0)
    breakdown["catches"] = min(catches, CATCH_BONUS_CAP)
    breakdown["efficiency"] = efficiency_bonus
    total = sum(breakdown.values())
    return max(0, min(100, total)), breakdown


def latency(eng: "RulesEngine") -> dict[str, Any]:
    """Pilot response time from sim events, and voice-pipeline stage timings (VOICE §4)."""
    response = []
    for e in eng.events:
        if e.type == "pilot_transmission":
            reply_to = e.data.get("in_reply_to") or e.tx_id
            if reply_to in eng.tx_time:
                response.append(e.t - eng.tx_time[reply_to])
    machine, perceived = [], []
    for timings in eng.tx_timings:
        up, play, final = timings.get("t_ptt_up"), timings.get("t_play"), timings.get("t_final")
        if up is not None and play is not None:
            perceived.append(play - up)
        if final is not None and play is not None:
            machine.append(play - final)
    return {"pilot_response_s": _dist(response), "machine_s": _dist(machine),
            "perceived_s": _dist(perceived)}


def build(eng: "RulesEngine", session_id: str) -> Report:
    findings = sorted(eng.findings, key=lambda f: (f.t, f.id))
    eff = eng.efficiency.stats()
    total, breakdown = score(findings, eff["bonus"])
    count = Counter(e.type for e in eng.events)
    by_cat = Counter(f.category for f in findings if f.actor == "CONTROLLER")
    stats: dict[str, Any] = {
        "transmissions": count["ctrl_transmission"],
        "pilot_transmissions": count["pilot_transmission"],
        "landings": count["landing"], "departures": count["takeoff"],
        "go_arounds": count["go_around"], "aircraft": count["spawn"],
        "findings_by_category": dict(by_cat),
        "catch_table": eng.monitoring.catch_table(),
        "los": list(eng.safety.los_closed),
        "efficiency": eff,
        "latency": latency(eng),
    }
    return Report(
        session_id=session_id, duration_s=eng.world.t, score=total,
        score_breakdown=breakdown, findings=findings,
        errors=sorted(eng.monitoring.errors.values(), key=lambda r: r.t_planned),
        stats=stats,
        timeline=[e for e in eng.events if e.type in TIMELINE_TYPES],
    )
