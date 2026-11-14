"""Debrief narrative: Claude writes ``narrative`` + 3 ``coaching`` points from the report only.

Guard rails (ASSIGNMENT §3.3): the model sees only the report JSON, must cite finding ids, may not
add findings, and every number it writes must already appear in the report; anything else is
rejected and a deterministic summary is used instead. ``write`` never raises.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from server.agents import llm
from server.contracts import Report

logger = logging.getLogger(__name__)

MAX_NARRATIVE_WORDS = 180          # hard limit (spec); the prompt asks for less so it is quick
MAX_COACHING_WORDS = 40
TARGET_NARRATIVE_WORDS = 120
TARGET_COACHING_WORDS = 30
DEADLINE_S = 4.5                   # session.py allows the writer 5 s
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

FORMAT = (
    ' Reply with ONLY a JSON object, no other text: {"narrative": string, "coaching": [string, string, '
    'string], "cited_finding_ids": [string]}.'
)

SYSTEM = (
    "You write the debrief for an air traffic control trainee after a simulator session. "
    "Use ONLY the report JSON you are given. Do not invent findings, events, aircraft, times or "
    "numbers: every number you write must appear in the report exactly as given (do not convert units, "
    "round or compute new numbers). Cite the finding ids you rely on "
    "in square brackets, e.g. [F3]. Be specific, fair and encouraging; plain English, no markdown. "
    f"The narrative is at most {TARGET_NARRATIVE_WORDS} words. Give exactly three coaching points, "
    f"each at most {TARGET_COACHING_WORDS} words, each an action the trainee can practise."
    + FORMAT
)


def compact(report: Report) -> dict[str, Any]:
    """Only what the writer needs; also the universe of allowed numbers and ids."""
    return {
        "score": report.score, "score_breakdown": report.score_breakdown,
        "duration_s": round(report.duration_s),
        "findings": [{"id": f.id, "rule": f.rule_id, "category": f.category, "severity": f.severity,
                      "t": round(f.t), "callsigns": f.callsigns, "title": f.title, "detail": f.detail,
                      "correct_practice": f.correct_practice, "actor": f.actor} for f in report.findings],
        "errors": [{"callsign": e.callsign, "kind": e.kind, "outcome": e.outcome,
                    "caught_by": e.caught_by, "intended": e.intended, "readback_value": e.readback_value}
                   for e in report.errors],
        "stats": {k: v for k, v in report.stats.items() if isinstance(v, (int, float, str))},
    }


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def validate(output: Optional[dict], source: dict[str, Any]) -> Optional[str]:
    """Return a reason string if the output must be rejected, else None."""
    if not output:
        return "no tool output"
    narrative, coaching = str(output.get("narrative", "")).strip(), output.get("coaching")
    if not narrative or len(narrative.split()) > MAX_NARRATIVE_WORDS:
        return "narrative empty or too long"
    if not isinstance(coaching, list) or len(coaching) != 3 or any(
            not str(c).strip() or len(str(c).split()) > MAX_COACHING_WORDS for c in coaching):
        return "coaching must be three short points"
    allowed_ids = {f["id"] for f in source["findings"]}
    cited = set(re.findall(r"\[([A-Za-z0-9_.-]+)\]", " ".join([narrative, *map(str, coaching)])))
    cited |= {str(i) for i in output.get("cited_finding_ids", [])}
    if cited - allowed_ids:
        return f"cites unknown findings {sorted(cited - allowed_ids)}"
    ids_text = " ".join(allowed_ids)
    bracket_free = re.sub(r"\[[^\]]*\]", " ", " ".join([narrative, *map(str, coaching)]))
    unknown = _numbers(bracket_free) - _numbers(json.dumps(source, default=str)) - _numbers(ids_text)
    if unknown:
        return f"numbers not in the report: {sorted(unknown)}"
    return None


def deterministic(report: Report) -> tuple[str, list[str]]:
    """Plain summary used when there is no key, no network or the model output is rejected."""
    ctrl = [f for f in report.findings if f.actor == "CONTROLLER"]
    bad = [f for f in ctrl if f.severity in ("HIGH", "MEDIUM", "LOW")]
    good = [f for f in ctrl if f.severity == "POSITIVE"]
    parts = [f"Session score {report.score} out of 100."]
    if good:
        parts.append(f"{len(good)} good catches or practices were recorded, e.g. {good[0].title}.")
    if bad:
        parts.append(f"{len(bad)} points to improve, the most serious being {bad[0].title}.")
    else:
        parts.append("No controller findings were recorded.")
    coaching = [f.correct_practice or f.title for f in bad[:3]]
    defaults = ["Check every readback against what you said before moving on.",
                "Keep instructions short and give one clear element at a time.",
                "Scan for converging traffic before issuing each new clearance."]
    coaching += defaults[: 3 - len(coaching)]
    return " ".join(parts), coaching[:3]


class Narrative:
    def __init__(self, client: Any = None, model: str = llm.NARRATIVE_MODEL):
        self._client = client
        self.model = model
        self._warm_task: Optional[asyncio.Task] = None
        if client is None and llm.get_client() is not None:
            try:                      # open the HTTPS connection now so the end-of-session call is not cold
                self._warm_task = asyncio.get_running_loop().create_task(self._warm())
            except RuntimeError:
                pass

    async def _warm(self) -> None:
        try:
            await asyncio.wait_for(llm.get_client().models.list(limit=1), 3.0)
        except Exception:             # noqa: BLE001 - purely an optimisation
            logger.debug("narrative warm-up skipped", exc_info=True)

    async def write(self, report: Report) -> Report:
        source = compact(report)
        text, coaching = deterministic(report)
        client = self._client or llm.get_client()
        if client is not None:
            try:
                response = await asyncio.wait_for(client.messages.create(
                    model=self.model, max_tokens=500, system=SYSTEM,
                    messages=[{"role": "user", "content": json.dumps(source, default=str)}],
                    **llm.model_kwargs(self.model)), DEADLINE_S)
                output = llm.json_output(response)
                problem = validate(output, source)
                if problem is None:
                    text, coaching = output["narrative"].strip(), [str(c).strip() for c in output["coaching"]]
                else:
                    logger.warning("debrief rejected (%s); using deterministic summary", problem)
            except Exception:                                        # noqa: BLE001 - never block the report
                logger.exception("debrief generation failed; using deterministic summary")
        return report.model_copy(update={"narrative": text, "coaching": coaching})
