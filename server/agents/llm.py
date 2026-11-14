"""Shared Anthropic client for the LLM features (narrative, parser fallback, variant bank)."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Measured on this account: Sonnet 5.5 cannot disable thinking and needs ~5 s for a debrief, which
# exceeds the 5 s the session gives the writer; Haiku 5.5 with thinking off takes ~2.5 s.
FAST_MODEL = "claude-haiku-5-5"           # debrief, parser fallback, variant generation
NARRATIVE_MODEL = os.environ.get("SWEATBOX_NARRATIVE_MODEL", FAST_MODEL)
DEFAULT_TIMEOUT_S = 4.3


def model_kwargs(model: str) -> dict:
    """Haiku burns output tokens on hidden thinking by default; turn it off for latency."""
    return {"thinking": {"type": "disabled"}} if "haiku" in model else {}

_client = None


def get_client():
    """Lazily built AsyncAnthropic (no retries: callers have their own fallbacks and deadlines)."""
    global _client
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    if _client is None:
        import anthropic
        _client = anthropic.AsyncAnthropic(api_key=key, max_retries=0, timeout=DEFAULT_TIMEOUT_S)
    return _client


def response_text(response) -> str:
    return "".join(getattr(b, "text", "") for b in getattr(response, "content", []) or []
                   if getattr(b, "type", None) == "text")


def json_output(response) -> Optional[dict]:
    """The first JSON object in the model's text (tolerates code fences / leading chatter)."""
    text = response_text(response).strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
