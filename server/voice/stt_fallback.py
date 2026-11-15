"""Batch speech-to-text fallback on fal Wizper, used on PTT up when Scribe failed (ASSIGNMENT §5 req. 21).

Wizper needs a hosted file (data URIs are rejected), so the transmission is uploaded as WAV first.
Measured 15 Nov: warm ~1 s upload + ~1 s transcription; the first call after idle 4-10.6 s (both the
upload and the model are slow when cold), which can miss the 8 s deadline. So the pipeline keeps it
warm: ``warm()`` uploads and transcribes 0.3 s of silence at server start, and again on push-to-talk
when it has not been used for WARM_EVERY_S, so it is ready by the time Scribe could fail on PTT up.
Past the deadline the controller is asked to repeat or type, exactly as without a fallback.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from server.agents.fal_emergency import wav_bytes
from server.voice.stt_scribe import STTError

logger = logging.getLogger(__name__)

MODEL = "fal-ai/wizper"
DEADLINE_S = 8.0
WARM_EVERY_S = 240.0          # re-warm on push-to-talk after this long unused (fal scales idle apps down)
WARM_TIMEOUT_S = 30.0         # a warm-up runs in the background, so it may take as long as a cold start
WARM_FILE = "warmup.wav"


class WizperSTT:
    def __init__(self, key: str, *, client: Any = None, deadline_s: float = DEADLINE_S):
        if not key:
            raise STTError("FAL_KEY is not set")
        if client is None:
            import fal_client
            client = fal_client.AsyncClient(key=key)
        self.client = client
        self.deadline_s = deadline_s
        self.last_used = float("-inf")        # time.monotonic() of the last successful call or warm-up
        self._warming = False
        self.warmups = 0

    @classmethod
    def from_env(cls) -> Optional["WizperSTT"]:
        try:
            return cls(os.environ.get("FAL_KEY", ""))
        except STTError:
            return None

    async def _run(self, pcm16: bytes, sample_rate: int, file_name: str = "transmission.wav") -> str:
        url = await self.client.upload(wav_bytes(pcm16, sample_rate), "audio/wav", file_name)
        result = await self.client.run(MODEL, arguments={"audio_url": url, "language": "en", "task": "transcribe"})
        return str((result or {}).get("text") or "").strip()

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        if not pcm16:
            return ""
        try:
            text = await asyncio.wait_for(self._run(pcm16, sample_rate), self.deadline_s)
            self.last_used = time.monotonic()
            return text
        except asyncio.TimeoutError as error:
            raise STTError(f"Wizper exceeded {self.deadline_s:.0f} s") from error
        except STTError:
            raise
        except Exception as error:                                   # noqa: BLE001
            raise STTError(f"Wizper failed: {type(error).__name__}: {error}") from error

    def needs_warm(self) -> bool:
        return not self._warming and time.monotonic() - self.last_used > WARM_EVERY_S

    async def warm(self) -> bool:
        """Upload and transcribe 0.3 s of silence so storage auth, connections and the model are warm.

        Runs in the background and never raises; returns whether it succeeded. One at a time.
        """
        if self._warming:
            return False
        self._warming = True
        self.warmups += 1
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._run(b"\x00\x00" * 4800, 16000, WARM_FILE), WARM_TIMEOUT_S)
            self.last_used = time.monotonic()
            logger.info("Wizper warm in %.1f s", self.last_used - started)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as error:                                   # noqa: BLE001
            logger.info("Wizper warm-up failed (%s); the backup will be cold if needed", error)
            return False
        finally:
            self._warming = False
