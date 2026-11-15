"""Claude Haiku fallback for transmissions the regex grammar could not fully parse (VOICE §7 step 6).

Triggered only when the fast path is unsure: an unparsed clause, a confidence below 0.8, or
instructions with no resolved callsign. The model gets the raw + normalised transcript, the
callsigns on frequency, the fixes and runways, and the instruction schema, and is told to fill
``ambiguities`` rather than guess. Its answer is validated (types, ranges, fixes, runways, scope
callsigns) and merged conservatively: everything the regex parsed with confidence is kept; the LLM
only fills what failed. Any error or timeout keeps the regex result unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional, get_args

from pydantic import ValidationError

from server.agents import llm
from server.contracts import CallsignInfo, Instruction, InstrType, Intent, Transmission
from server.voice.parser.grammar import _TYPE_FAMILY, out_of_range
from server.voice.parser.intent import acknowledgement

logger = logging.getLogger(__name__)

DEADLINE_S = 3.0          # measured p50 ~1.8 s, max ~2.7 s with a warm connection
LOW_CONFIDENCE = 0.8
TRIGGERS = ("unparsed_clause", "callsign_unresolved")
FIELDS = ("type", "value", "turn_dir", "verb", "constraint", "approach_type", "runway", "fix", "unit",
          "frequency", "condition", "expect_what", "report_what")

SYSTEM = """You convert one air traffic controller radio transmission (UK, Gatwick Director) into JSON.
The transcript comes from speech recognition and may contain mis-heard words.

Rules:
- Use ONLY the callsigns listed as on frequency; receiver is the ICAO code or null. Never invent one.
- Use ONLY the listed fixes and runways. If a fix or runway is unclear, leave it out and add an ambiguity.
- Do not guess. If a value or element is unclear, omit that instruction and add a short ambiguity string.
- Readbacks and politeness are not instructions. "correction" replaces the earlier same-type value.
- Units: heading degrees 1-360 (north = 360); altitude in feet; flight_level as the FL number (70 = FL70);
  speed knots; vertical_rate ft/min; qnh hPa number; squawk a 4-character string or "ident";
  frequency a string with 3 decimals ("124.225"); runway like "26L".
- turn_dir "L"/"R" only if left/right was said. verb is climb/descend/maintain for level changes.
- constraint: speed "or_greater"/"or_less"; vertical rate "max" (or less) / "min" (or greater).
- expect_what like "ILS 26L"; report_what one of established, heading, speed, level, passing.
- intent: instruction | readback_correction (controller said "negative"/"I say again" correcting the pilot)
  | say_again_request | readback_request | confirm_request | broadcast | unknown.

Instruction types: {types}

Reply with ONLY a JSON object:
{{"receiver": string|null, "intent": string, "instructions": [{{"type": ..., "value": ..., ...}}],
  "ambiguities": [string]}}"""


def needs_fallback(tx: Transmission) -> bool:
    if tx.intent == "broadcast":
        return False
    if not tx.instructions and acknowledgement(tx.norm_text.split()):
        return False                # "readback correct" is complete as it is
    if any(a in tx.ambiguities for a in TRIGGERS):
        return True
    if any(i.confidence < LOW_CONFIDENCE for i in tx.instructions):
        return True
    return not tx.instructions and tx.intent == "unknown" and len(tx.norm_text.split()) >= 3


def _context(tx: Transmission, callsigns: list[CallsignInfo], fixes: set[str], runways: list[str]) -> str:
    return json.dumps({
        "transcript_raw": tx.raw_text,
        "transcript_normalised": tx.norm_text,
        "callsigns_on_frequency": [{"icao": c.icao, "spoken": c.spoken, "telephony": c.telephony}
                                   for c in callsigns],
        "fixes": sorted(fixes),
        "runways": runways,
    })


def validate_instructions(raw: Any, fixes: set[str], runways: list[str]) -> tuple[list[Instruction], list[str]]:
    """Keep only well-formed, in-range instructions with known fixes/runways."""
    kept: list[Instruction] = []
    rejected: list[str] = []
    if not isinstance(raw, list):
        return kept, ["llm_bad_output"]
    for item in raw:
        if not isinstance(item, dict):
            rejected.append("llm_bad_instruction")
            continue
        data = {k: v for k, v in item.items() if k in FIELDS and v is not None}
        if data.get("type") == "squawk" and isinstance(data.get("value"), (int, float)):
            data["value"] = f"{int(data['value']):04d}"
        if isinstance(data.get("fix"), str):
            data["fix"] = data["fix"].upper()
        if isinstance(data.get("runway"), str):
            data["runway"] = data["runway"].upper()
        try:
            ins = Instruction(**data, confidence=0.85)
        except (ValidationError, TypeError):
            rejected.append(f"llm_invalid_{data.get('type', 'instruction')}")
            continue
        if ins.fix and ins.fix not in fixes:
            rejected.append(f"unknown_fix_{ins.fix}")
            continue
        if ins.runway and ins.runway not in runways:
            rejected.append(f"unknown_runway_{ins.runway}")
            continue
        if not complete(ins):
            rejected.append(f"incomplete_{ins.type}")
            continue
        if out_of_range(ins):
            rejected.append(f"llm_out_of_range_{ins.type}")
            continue
        kept.append(ins)
    return kept, rejected


REQUIRED = {
    "heading": "value", "turn_by": "value", "altitude": "value", "flight_level": "value",
    "maintain_level": "value", "vertical_rate": "value", "speed": "value", "squawk": "value", "qnh": "value",
    "direct": "fix", "hold": "fix", "approach_clearance": "approach_type", "expect": "expect_what",
}


def complete(ins: Instruction) -> bool:
    """An instruction without its essential content ("turn left" with no heading) is not an instruction."""
    if ins.type == "contact":
        return bool(ins.unit or ins.frequency)
    field = REQUIRED.get(ins.type)
    return field is None or getattr(ins, field) not in (None, "")


def _same(a: Instruction, b: Instruction) -> bool:
    return all(getattr(a, f) == getattr(b, f) for f in ("type", "value", "turn_dir", "fix", "runway"))


def merge(tx: Transmission, output: dict, callsigns: list[CallsignInfo], fixes: set[str],
          runways: list[str]) -> Transmission:
    """Regex results win; the LLM fills the failed parts only."""
    llm_instrs, rejected = validate_instructions(output.get("instructions"), fixes, runways)
    scope = {c.icao for c in callsigns}
    receiver, confidence = tx.receiver, tx.receiver_confidence
    llm_receiver = output.get("receiver")
    if receiver is None and isinstance(llm_receiver, str) and llm_receiver.upper() in scope:
        receiver, confidence = llm_receiver.upper(), 0.85
    instructions = list(tx.instructions)
    regex_types = {i.type for i in instructions}
    # One instruction per family: a regex "turn_by" and an LLM "heading" are the same turn, read back twice.
    regex_families = {_TYPE_FAMILY.get(i.type, i.type) for i in instructions}
    added = [i for i in llm_instrs if i.type not in regex_types and _TYPE_FAMILY.get(i.type, i.type) not in regex_families
             and not any(_same(i, r) for r in instructions)]
    instructions += added
    intent = tx.intent
    llm_intent = output.get("intent")
    if intent == "unknown" and llm_intent in get_args(Intent):
        intent = llm_intent
    elif intent == "instruction" and llm_intent == "readback_correction" and instructions:
        intent = "readback_correction"
    if intent == "unknown" and instructions:
        intent = "instruction"
    ambiguities = [a for a in tx.ambiguities if a not in TRIGGERS or (a == "callsign_unresolved" and receiver is None)]
    if "unparsed_clause" in tx.ambiguities and not added:
        ambiguities.append("unparsed_clause")
    extra = [str(a)[:60] for a in (output.get("ambiguities") or []) if isinstance(a, (str, int, float))]
    ambiguities += [a for a in extra + rejected if a not in ambiguities]
    if not added and receiver == tx.receiver and intent == tx.intent:
        path = tx.parse_path
    else:
        path = "llm" if not tx.instructions else "mixed"
    return tx.model_copy(update={
        "receiver": receiver, "receiver_confidence": confidence, "intent": intent,
        "instructions": instructions, "ambiguities": ambiguities, "parse_path": path,
    })


async def warm(client: Any = None) -> None:
    """Import the SDK and open the HTTPS connection before the first fallback is needed."""
    client = client or llm.get_client()
    if client is None:
        return
    try:
        await asyncio.wait_for(client.models.list(limit=1), 3.0)
    except Exception:                                              # noqa: BLE001 - optimisation only
        logger.debug("LLM warm-up failed", exc_info=True)


async def refine(tx: Transmission, callsigns: list[CallsignInfo], fixes: set[str], runways: list[str],
                 client: Any = None, model: str = llm.FAST_MODEL) -> Transmission:
    """Return a better Transmission if the LLM can help; otherwise the regex one, unchanged."""
    client = client or llm.get_client()
    if client is None or not needs_fallback(tx):
        return tx
    started = time.perf_counter()
    system = SYSTEM.format(types=", ".join(get_args(InstrType)))
    try:
        response = await asyncio.wait_for(client.messages.create(
            model=model, max_tokens=500,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _context(tx, callsigns, fixes, runways)}],
            **llm.model_kwargs(model)), DEADLINE_S)
        output = llm.json_output(response)
    except Exception as error:                                       # noqa: BLE001 - never lose the regex parse
        logger.warning("LLM parse fallback failed (%s); keeping the regex parse", type(error).__name__)
        return tx.model_copy(update={"timings": {**tx.timings, "llm_s": time.perf_counter() - started}})
    if not output:
        return tx
    merged = merge(tx, output, callsigns, fixes, runways)
    return merged.model_copy(update={"timings": {**merged.timings, "llm_s": time.perf_counter() - started}})
