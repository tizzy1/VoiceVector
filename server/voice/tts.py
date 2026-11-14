"""ElevenLabs Flash v2.5 streaming TTS over a shared HTTP/2 keep-alive client (VOICE_PIPELINE §12).

``stream()`` yields raw PCM16 chunks as soon as the first bytes arrive. Completed audio is kept in
a small LRU keyed by (voice, text, settings): repeated phrases ("say again, speedbird one one
seven") and "say again" repeats cost no characters and no latency.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import OrderedDict
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_flash_v2_5"
DEFAULT_FORMAT = "pcm_22050"
CONCURRENCY = 4               # Creator plan allows 5 concurrent TTS requests; keep one spare


class TTSError(RuntimeError):
    """TTS unavailable (network, auth, quota). The pipeline degrades to captions only."""


def format_info(output_format: str) -> tuple[str, int]:
    """"pcm_22050" -> ("pcm_s16le", 22050); "mp3_44100_128" -> ("mp3", 44100)."""
    kind, _, rate = output_format.partition("_")
    sample_rate = int(rate.split("_")[0]) if rate else 22050
    return ("pcm_s16le" if kind == "pcm" else "mp3"), sample_rate


class ElevenLabsTTS:
    def __init__(self, api_key: str, *, client: Optional[httpx.AsyncClient] = None,
                 model: str = DEFAULT_MODEL, output_format: str = DEFAULT_FORMAT,
                 timeout: float = 4.0, cache_size: int = 200):
        if not api_key:
            raise TTSError("ELEVENLABS_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.output_format = output_format
        self.audio_format, self.sample_rate = format_info(output_format)
        self.timeout = timeout
        self._own_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=BASE_URL, http2=True,
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120),
        )
        self._cache: OrderedDict[tuple, list[bytes]] = OrderedDict()
        self._cache_size = cache_size
        self._slots = asyncio.Semaphore(CONCURRENCY)

    async def aclose(self) -> None:
        if self._own_client:
            await self.client.aclose()

    @staticmethod
    def _key(voice_id: str, text: str, settings: Optional[dict]) -> tuple:
        return voice_id, text, tuple(sorted((settings or {}).items()))

    def cached(self, voice_id: str, text: str, settings: Optional[dict] = None) -> bool:
        return self._key(voice_id, text, settings) in self._cache

    async def warm(self) -> None:
        """Open the HTTP/2 connection ahead of the first transmission (no characters billed)."""
        try:
            await self.client.get("/v1/models", headers={"xi-api-key": self.api_key})
        except httpx.HTTPError:
            logger.warning("TTS warm-up failed; the first transmission will pay the handshake")

    async def stream(self, text: str, voice_id: str,
                     settings: Optional[dict] = None) -> AsyncIterator[bytes]:
        key = self._key(voice_id, text, settings)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            for chunk in hit:
                yield chunk
            return
        collected: list[bytes] = []
        carry = b""
        body = {"text": text, "model_id": self.model}
        if settings:
            body["voice_settings"] = settings
        try:
            async with self._slots:
                async with self.client.stream(
                    "POST", f"/v1/text-to-speech/{voice_id}/stream",
                    params={"output_format": self.output_format},
                    headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                    content=json.dumps(body),
                ) as response:
                    if response.status_code != 200:
                        detail = (await response.aread())[:200].decode("utf-8", "replace")
                        raise TTSError(f"ElevenLabs TTS {response.status_code}: {detail}")
                    async for raw in response.aiter_bytes(4096):
                        data = carry + raw
                        if self.audio_format == "pcm_s16le" and len(data) % 2:
                            data, carry = data[:-1], data[-1:]       # never split a 16-bit sample
                        else:
                            carry = b""
                        if data:
                            collected.append(data)
                            yield data
        except httpx.HTTPError as error:
            raise TTSError(f"ElevenLabs TTS network error: {error!r}") from error
        self._cache[key] = collected
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


    async def stream_timed(self, text: str, voice_id: str,
                           settings: Optional[dict] = None) -> AsyncIterator[tuple[bytes, Optional[dict]]]:
        """Like ``stream`` but via /stream/with-timestamps: yields (pcm, alignment-or-None).

        Alignment dicts carry ``characters`` / ``character_start_times_seconds`` /
        ``character_end_times_seconds`` for the next slice of ``text``; times are absolute from the
        start of the audio (verified against the live API).
        """
        key = ("timed",) + self._key(voice_id, text, settings)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            for item in hit:
                yield item
            return
        collected: list = []
        carry = b""
        body = {"text": text, "model_id": self.model}
        if settings:
            body["voice_settings"] = settings
        try:
            async with self._slots:
                async with self.client.stream(
                    "POST", f"/v1/text-to-speech/{voice_id}/stream/with-timestamps",
                    params={"output_format": self.output_format},
                    headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                    content=json.dumps(body),
                ) as response:
                    if response.status_code != 200:
                        detail = (await response.aread())[:200].decode("utf-8", "replace")
                        raise TTSError(f"ElevenLabs TTS {response.status_code}: {detail}")
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        data = carry + base64.b64decode(obj.get("audio_base64") or "")
                        if self.audio_format == "pcm_s16le" and len(data) % 2:
                            data, carry = data[:-1], data[-1:]
                        else:
                            carry = b""
                        alignment = obj.get("alignment") or None
                        if alignment and not alignment.get("characters"):
                            alignment = None
                        if data or alignment:
                            collected.append((data, alignment))
                            yield data, alignment
        except httpx.HTTPError as error:
            raise TTSError(f"ElevenLabs TTS network error: {error!r}") from error
        self._cache[key] = collected
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
