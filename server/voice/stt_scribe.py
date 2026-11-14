"""ElevenLabs Scribe v2 Realtime: one stream per push-to-talk (VOICE_PIPELINE §3, §6).

The WebSocket opens on PTT *down* and microphone frames are buffered until it is connected, so the
handshake overlaps with the controller speaking. Partials are forwarded live (captions); on PTT up
we send a manual commit and wait for the committed transcript. Keyterms bias the model towards
callsigns, fixes and phraseology.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

MODEL = "scribe_v2_realtime"
MAX_KEYTERMS = 50
MAX_KEYTERM_LEN = 20
STATIC_KEYTERMS = ["niner", "QNH", "localiser", "Gatwick", "Director", "Tower", "flight level",
                   "squawk", "heading", "descend", "expedite", "speedbird", "readback",
                   "say again", "negative", "correction", "decimal", "hectopascals"]


class STTError(RuntimeError):
    """Speech-to-text unavailable; the pipeline tells the controller and falls back to typing."""


def build_keyterms(*groups: list[str]) -> list[str]:
    """Dedupe, drop anything over 20 chars, cap at 50 (Scribe realtime limits)."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for term in group:
            term = " ".join(str(term).split())
            if term and len(term) <= MAX_KEYTERM_LEN and term.lower() not in seen:
                seen.add(term.lower())
                out.append(term)
    return out[:MAX_KEYTERMS]


async def _default_connect(api_key: str, keyterms: list[str]):
    from elevenlabs.realtime import AudioFormat, CommitStrategy, ScribeRealtime
    client = ScribeRealtime(api_key=api_key)
    options = {"model_id": MODEL, "audio_format": AudioFormat.PCM_16000, "sample_rate": 16000,
               "commit_strategy": CommitStrategy.MANUAL, "language_code": "en"}
    try:
        return await client.connect({**options, "keyterms": keyterms} if keyterms else options)
    except Exception:
        if not keyterms:
            raise
        logger.warning("Scribe rejected keyterms; reconnecting without them", exc_info=True)
        return await client.connect(options)


class ScribeStream:
    """``start()`` on PTT down, ``feed()`` per mic frame, ``finish()`` on PTT up -> final text."""

    def __init__(self, api_key: str, keyterms: Optional[list[str]] = None,
                 on_partial: Optional[Callable[[str], None]] = None, *,
                 connect: Optional[Callable[[], Awaitable]] = None,
                 connect_timeout: float = 3.0, commit_timeout: float = 3.0):
        self._queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._on_partial = on_partial
        self._connect = connect or (lambda: _default_connect(api_key, keyterms or []))
        self._connect_timeout = connect_timeout
        self._commit_timeout = commit_timeout
        self._committed = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self.text = ""
        self.partial = ""
        self.error: Optional[str] = None
        self.bytes_fed = 0
        self.connected_at: Optional[float] = None

    @property
    def duration_s(self) -> float:
        return self.bytes_fed / 32000          # 16 kHz mono PCM16

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def feed(self, pcm16: bytes) -> None:
        if pcm16:
            self.bytes_fed += len(pcm16)
            self._queue.put_nowait(pcm16)

    async def _run(self) -> None:
        conn = None
        try:
            conn = await asyncio.wait_for(self._connect(), self._connect_timeout)
            self.connected_at = asyncio.get_running_loop().time()
            conn.on("partial_transcript", self._partial)
            conn.on("committed_transcript", self._final)
            conn.on("error", self._failed)
            while True:
                chunk = await self._queue.get()
                if chunk is None:
                    await conn.commit()
                    await asyncio.wait_for(self._committed.wait(), self._commit_timeout)
                    return
                await conn.send({"audio_base_64": base64.b64encode(chunk).decode("ascii")})
        except asyncio.TimeoutError:
            self.error = self.error or ("connect timeout" if conn is None else "commit timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                   # noqa: BLE001 - never kill the session
            self.error = self.error or f"{type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                try:
                    await asyncio.wait_for(conn.close(), 1.0)
                except Exception:                                  # noqa: BLE001
                    pass
            self._committed.set()

    # ------------------------------------------------------------ SDK handlers
    def _partial(self, data: dict) -> None:
        self.partial = str(data.get("text", "")).strip()
        if self._on_partial and self.partial:
            self._on_partial(self.partial)

    def _final(self, data: dict) -> None:
        self.text = str(data.get("text", "")).strip()
        self._committed.set()

    def _failed(self, data: dict) -> None:
        self.error = str(data.get("error") or data.get("message") or data)
        logger.warning("Scribe error: %s", self.error)

    # ------------------------------------------------------------------ finish
    async def finish(self) -> str:
        """Commit and return the final transcript ("" if nothing was heard)."""
        self._queue.put_nowait(None)
        if self._task is None:
            raise STTError("stream was never started")
        try:
            await asyncio.wait_for(asyncio.shield(self._task),
                                   self._connect_timeout + self._commit_timeout + 1.0)
        except asyncio.TimeoutError:
            self.error = self.error or "timeout"
        if not self.text and self.error:
            raise STTError(self.error)
        return self.text

    async def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
