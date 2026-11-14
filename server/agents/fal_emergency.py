"""Emergency voice: fal Chatterbox speaks MAYDAY / PAN calls with real emotion (ASSIGNMENT §5 req. 19).

The pilot keeps their own voice: a short neutral reference clip of the aircraft's ElevenLabs voice is
synthesised once, uploaded to fal storage and given to Chatterbox as the zero-shot voice prompt, with
a high ``exaggeration``. Chatterbox is not streamed (measured ~5-5.5 s for a 6 s call, 24 kHz mono
WAV), so the call has a deadline; the pipeline falls back to ElevenLabs with stressed settings.

Exposes the same ``stream(text, voice_id, settings)`` shape as ElevenLabsTTS, yielding PCM16 chunks.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

MODEL = "fal-ai/chatterbox/text-to-speech"
DEADLINE_S = 10.0
REFERENCE_TEXT = ("Gatwick Director, good afternoon, we are descending to four thousand feet "
                  "with information kilo, and we have the weather.")
CHUNK = 4096


class EmergencyVoiceError(RuntimeError):
    pass


def wav_bytes(pcm16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def decode_wav(data: bytes) -> tuple[bytes, int]:
    """WAV -> (mono PCM16 little-endian, sample rate). Mixes stereo down; converts 8/24/32-bit."""
    import numpy as np
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
    except (wave.Error, EOFError) as error:
        raise EmergencyVoiceError(f"not a WAV file: {error}") from error
    if width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        ints = (raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16))
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        samples = ints.astype(np.float32) / 8388608
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648
    else:
        raise EmergencyVoiceError(f"unsupported sample width {width}")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    pcm = np.clip(np.round(samples * 32768), -32768, 32767).astype("<i2").tobytes()
    return pcm, rate


Fetch = Callable[[str], Awaitable[bytes]]


async def _fetch(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


class ChatterboxVoice:
    """Emotional TTS for emergencies, in the aircraft's own (cloned) voice."""

    audio_format = "pcm_s16le"

    def __init__(self, key: str, reference_tts: Any, *, client: Any = None, fetch: Fetch = _fetch,
                 exaggeration: float = 0.85, deadline_s: float = DEADLINE_S):
        if not key:
            raise EmergencyVoiceError("FAL_KEY is not set")
        if reference_tts is None:
            raise EmergencyVoiceError("a reference voice (ElevenLabs) is required to keep the pilot's voice")
        if client is None:
            import fal_client
            client = fal_client.AsyncClient(key=key)
        self.client = client
        self.reference_tts = reference_tts
        self.fetch = fetch
        self.exaggeration = exaggeration
        self.deadline_s = deadline_s
        self.sample_rate = 24000
        self._references: dict[str, asyncio.Task] = {}

    @classmethod
    def from_env(cls, reference_tts: Any) -> Optional["ChatterboxVoice"]:
        try:
            return cls(os.environ.get("FAL_KEY", ""), reference_tts)
        except EmergencyVoiceError:
            return None

    async def _make_reference(self, voice_id: str) -> str:
        pcm = b"".join([c async for c in self.reference_tts.stream(REFERENCE_TEXT, voice_id)])
        if not pcm:
            raise EmergencyVoiceError("empty reference clip")
        return await self.client.upload(wav_bytes(pcm, self.reference_tts.sample_rate), "audio/wav",
                                        f"ref-{voice_id}.wav")

    async def reference(self, voice_id: str) -> str:
        """One reference clip per voice per process (shared by concurrent emergencies)."""
        task = self._references.get(voice_id)
        if task is None or (task.done() and task.exception() is not None):
            task = asyncio.create_task(self._make_reference(voice_id))
            self._references[voice_id] = task
        return await asyncio.shield(task)

    async def _synthesise(self, text: str, voice_id: str, seed: int) -> bytes:
        ref = await self.reference(voice_id)
        result = await self.client.run(MODEL, arguments={
            "text": text, "audio_url": ref, "exaggeration": self.exaggeration,
            "cfg": 0.35, "temperature": 0.7, "seed": seed})
        url = ((result or {}).get("audio") or {}).get("url")
        if not url:
            raise EmergencyVoiceError("Chatterbox returned no audio")
        pcm, rate = decode_wav(await self.fetch(url))
        self.sample_rate = rate
        return pcm

    async def stream(self, text: str, voice_id: str, settings: Optional[dict] = None) -> AsyncIterator[bytes]:
        seed = sum(map(ord, text + voice_id)) % 100000
        try:
            pcm = await asyncio.wait_for(self._synthesise(text, voice_id, seed), self.deadline_s)
        except asyncio.TimeoutError as error:
            raise EmergencyVoiceError(f"Chatterbox exceeded {self.deadline_s:.0f} s") from error
        except EmergencyVoiceError:
            raise
        except Exception as error:                                   # noqa: BLE001
            raise EmergencyVoiceError(f"Chatterbox failed: {type(error).__name__}: {error}") from error
        for i in range(0, len(pcm), CHUNK):
            yield pcm[i:i + CHUNK]
