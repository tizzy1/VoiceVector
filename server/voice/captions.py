"""Caption helpers: parsed-instruction chips and plain-English summaries (ASSIGNMENT §6)."""
from __future__ import annotations

from typing import Iterable, Optional

from server.contracts import CaptionWord, Instruction, ParsedChip, Transmission


def _num(value) -> str:
    return str(int(float(value))) if isinstance(value, (int, float)) else str(value)


def chip_label(i: Instruction) -> Optional[str]:
    t = i.type
    if t == "heading":
        return f"HDG {int(float(i.value)):03d}" + (f" {i.turn_dir}" if i.turn_dir else "")
    if t == "turn_by":
        return f"TURN {i.turn_dir or ''} {_num(i.value)}°".replace("  ", " ")
    if t == "present_heading":
        return "PRESENT HDG"
    if t == "altitude":
        return f"ALT {_num(i.value)}"
    if t == "flight_level":
        return f"FL{_num(i.value)}"
    if t == "maintain_level":
        return f"MAINTAIN {'FL' + _num(i.value) if float(i.value) < 1000 else _num(i.value)}"
    if t == "vertical_rate":
        return f"V/S {_num(i.value)}" + {"max": " max", "min": " min"}.get(i.constraint or "", "")
    if t == "speed":
        return f"SPD {_num(i.value)}" + {"or_greater": "+", "or_less": "-"}.get(i.constraint or "", "")
    if t == "speed_cancel":
        return "SPD FREE"
    if t == "speed_min_clean":
        return "MIN CLEAN"
    if t == "expedite":
        return "EXPEDITE"
    if t == "direct":
        return f"DCT {i.fix}"
    if t == "hold":
        return f"HOLD {i.fix}"
    if t == "resume_nav":
        return "OWN NAV"
    if t == "approach_clearance":
        return f"{i.approach_type or 'APP'} {i.runway or ''}".strip()
    if t == "intercept_localiser":
        return f"LOC {i.runway or ''}".strip()
    if t == "descend_glidepath":
        return "GLIDEPATH"
    if t == "cancel_approach":
        return "CANCEL APP"
    if t == "go_around":
        return "GO AROUND"
    if t == "contact":
        return f"CONTACT {(i.unit or '').upper()} {i.frequency or ''}".strip()
    if t == "squawk":
        return f"SQK {i.value}"
    if t == "qnh":
        return f"QNH {_num(i.value)}"
    if t == "expect":
        return f"EXPECT {i.expect_what or ''}".strip()
    if t == "report":
        return f"REPORT {(i.report_what or '').upper()}".strip()
    if t == "confirm":
        return f"CONFIRM {(i.report_what or '').upper()}".strip()
    if t == "traffic_info":
        return "TRAFFIC"
    if t == "cleared_to_land":
        return f"LAND {i.runway or ''}".strip()
    if t == "cleared_takeoff":
        return f"T/O {i.runway or ''}".strip()
    return t.upper()


def chips_for(tx: Transmission) -> list[ParsedChip]:
    chips = [ParsedChip(label=tx.receiver)] if tx.receiver else []
    for ins in tx.instructions:
        label = chip_label(ins)
        if label:
            chips.append(ParsedChip(label=label, span=ins.span))
    if tx.intent not in ("instruction", "unknown", "broadcast"):
        chips.append(ParsedChip(label=tx.intent.replace("_", " ").upper()))
    return chips


def _phrase(i: Instruction) -> Optional[str]:
    t = i.type
    d = {"L": "left", "R": "right"}.get(i.turn_dir or "", "")
    if t == "heading":
        return f"turn {d} {int(float(i.value)):03d}°".replace("  ", " ") if d else f"fly heading {int(float(i.value)):03d}°"
    if t == "turn_by":
        return f"turn {d} {_num(i.value)}°"
    if t in ("altitude", "flight_level", "maintain_level"):
        verb = {"climb": "climb", "descend": "descend", "maintain": "maintain"}.get(i.verb or "", "level")
        level = f"FL{_num(i.value)}" if (t == "flight_level" or float(i.value) < 1000) else f"{int(float(i.value)):,} ft"
        return f"{verb} {level}"
    if t == "speed":
        return f"speed {_num(i.value)} kt" + {"or_greater": " or more", "or_less": " or less"}.get(i.constraint or "", "")
    if t == "direct":
        return f"direct {i.fix}"
    if t == "approach_clearance":
        return f"cleared {i.approach_type or 'approach'} {i.runway or ''}".strip()
    if t == "contact":
        return f"contact {i.unit or ''} {i.frequency or ''}".strip()
    if t == "squawk":
        return f"squawk {i.value}"
    if t == "qnh":
        return f"QNH {_num(i.value)}"
    label = chip_label(i)
    return label.lower() if label else None


def plain_english(tx: Transmission, names: Optional[dict[str, str]] = None) -> str:
    """"British Airways 117 → turn left 240°, descend 4,000 ft"."""
    who = (names or {}).get(tx.receiver or "", tx.receiver or "Unknown station")
    parts: Iterable[str] = filter(None, (_phrase(i) for i in tx.instructions))
    body = ", ".join(parts)
    if tx.intent in ("say_again_request", "readback_request", "confirm_request", "broadcast") and not body:
        body = tx.intent.replace("_", " ")
    elif tx.intent == "readback_correction":
        body = f"correction: {body}"
    return f"{who} → {body}" if body else who


def estimated_words(text: str, duration_s: float) -> list[CaptionWord]:
    """Spread words over the audio duration, weighted by word length (no timestamps available)."""
    words = text.split()
    if not words or duration_s <= 0:
        return []
    weights = [len(w) + 1 for w in words]
    total = sum(weights)
    out, t = [], 0.0
    for w, weight in zip(words, weights):
        span = duration_s * weight / total
        out.append(CaptionWord(w=w, t0=round(t, 3), t1=round(t + span, 3)))
        t += span
    return out
