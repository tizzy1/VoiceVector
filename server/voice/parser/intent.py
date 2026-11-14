"""Intent detection (VOICE_PIPELINE §7 step 1)."""
from __future__ import annotations

from server.contracts import Intent


def detect_intent(tokens: list[str], families: list[str], has_instructions: bool) -> Intent:
    say_again = "say" in tokens and "again" in tokens
    if "all" in tokens and "stations" in tokens:
        return "broadcast"
    if "confirm" in families:
        return "confirm_request"
    if "readback" in tokens and not has_instructions:
        return "readback_request"
    if say_again and not has_instructions:
        return "say_again_request"
    if has_instructions and ("negative" in tokens or say_again):
        return "readback_correction"
    return "instruction" if has_instructions else "unknown"
