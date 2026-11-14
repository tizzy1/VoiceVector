"""Batch speech-to-text fallback on fal Wizper, used on PTT up when Scribe failed (ASSIGNMENT §5 req. 21).

Wizper needs a hosted file (data URIs are rejected), so the transmission is uploaded as WAV first.
Measured on this account: upload ~1.2 s, transcription 15 s to minutes (queue / cold start), so the
hard deadline matters: past it the controller is asked to repeat or type, exactly as without a fallback.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from server.agents.fal_emergency import wav_bytes
from server.voice.stt_scribe import STTError

logger = logging.getLogger(__name__)

MODEL = "fal-ai/wizper"
DEADLINE_S = 8.0


class WizperSTT:
    def __init__(self, key: str, *, client: Any = None, deadline_s: float = DEADLINE_S):
        if not key:
            raise STTError("FAL_KEY is not set")
        if client is None:
            import fal_client
            client = fal_client.AsyncClient(key=key)
        self.client = client
        self.deadline_s = deadline_s

    @classmethod
    def from_env(cls) -> Optional["WizperSTT"]:
        try:
            return cls(os.environ.get("FAL_KEY", ""))
        except STTError:
            return None

    async def _run(self, pcm16: bytes, sample_rate: int) -> str:
        url = await self.client.upload(wav_bytes(pcm16, sample_rate), "audio/wav", "transmission.wav")
        result = await self.client.run(MODEL, arguments={"audio_url": url, "language": "en", "task": "transcribe"})
        return str((result or {}).get("text") or "").strip()

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        if not pcm16:
            return ""
        try:
            return await asyncio.wait_for(self._run(pcm16, sample_rate), self.deadline_s)
        except asyncio.TimeoutError as error:
            raise STTError(f"Wizper exceeded {self.deadline_s:.0f} s") from error
        except STTError:
            raise
        except Exception as error:                                   # noqa: BLE001
            raise STTError(f"Wizper failed: {type(error).__name__}: {error}") from error
