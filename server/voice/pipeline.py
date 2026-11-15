"""VoicePipeline: PTT -> Scribe -> parse -> pilot -> composer -> ElevenLabs -> browsers.

Implements ``VoiceAPI`` (contracts.py) with the constructor Session wires in:
``VoicePipeline(world, pilot, composer, bus, hub)``; API keys come from the environment.

Design rules (ASSIGNMENT §6): no LLM on the PTT path unless the regex parse fails; every external
call has a timeout and a fallback (STT/TTS failures degrade to captions + typed input, they never
kill the session); the controller's words are never silently dropped.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from server.contracts import (CoachMsg, CoachNote, AudioChunkMsg, AudioEndMsg, AudioStartMsg, CaptionMsg, Event,
                              PilotResponse, Transmission)
from server.voice.captions import (align_display_words, chips_for, estimated_words, plain_english,
                                  spoken_word_times)
from server.voice.frequency import FrequencyManager
from server.voice.metrics import Metrics, Stamps
from server.voice.parser import default_runways, parse
from server.voice.parser import llm_fallback
from server.voice.stt_fallback import WizperSTT
from server.voice.stt_scribe import STATIC_KEYTERMS, ScribeStream, STTError, build_keyterms
from server.voice.tts import ElevenLabsTTS, TTSError
from server.voice.pronounce import respell
from server.voice import coach
from server.voice.replay import ReplayStore
from server.voice import trace as T
from server.voice.prosody import compensated_speed, restore_word_times, shape
from server.voice.voices import Voices, settings_for, target_wps

logger = logging.getLogger(__name__)

MIN_TRANSMISSION_S = 0.3          # shorter key-ups are accidental blips
PARTIAL_INTERVAL_S = 0.2          # <= 5 partial captions per second
FIRST_AUDIO_TIMEOUT_S = 6.0
EMERGENCY_FIRST_AUDIO_S = 14.0   # Chatterbox (~6.5 s incl. reference) + ElevenLabs fallback
MAX_PTT_AUDIO_BYTES = 60 * 32000  # keep at most 60 s of 16 kHz PCM16 for the batch STT fallback
EST_SPEECH_S_PER_WORD = 0.42      # reading time when there is no audio
EventTypeName = str


@dataclass
class _Ptt:
    tx_id: str
    stamps: Stamps
    stepped_on: dict[str, str] = field(default_factory=dict)     # pilot tx_id -> callsign
    stt: Optional[ScribeStream] = None
    last_partial: float = 0.0
    partials: int = 0                                         # all partials received (trace count)
    audio: bytearray = field(default_factory=bytearray)      # kept for the batch STT fallback


@dataclass
class _OverlapGroup:
    """Deliberate overlap (force_overlap inject): these callers transmit at the same instant."""
    id: str
    members: set[str]
    created: float = field(default_factory=time.monotonic)
    arrived: set[str] = field(default_factory=set)
    tx_ids: list[str] = field(default_factory=list)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    dead: bool = False          # a caller gave up waiting: everyone talks normally


OVERLAP_WAIT_S = 6.0      # how long one caller waits for its partner before talking alone
OVERLAP_TTL_S = 20.0

STTFactory = Callable[[list[str], Callable[[str], None]], ScribeStream]


class VoicePipeline:
    def __init__(self, world, pilot, composer, bus, hub, *, tts: Optional[ElevenLabsTTS] = None,
                 stt_factory: Optional[STTFactory] = None, voices: Optional[Voices] = None,
                 seed: Optional[int] = None, emergency: Any = None, stt_fallback: Any = None):
        self.world, self.pilot, self.composer, self.bus, self.hub = world, pilot, composer, bus, hub
        seed = int(getattr(world, "seed", 42) if seed is None else seed)
        key = os.environ.get("ELEVENLABS_API_KEY", "")
        if tts is None and key:
            try:
                tts = ElevenLabsTTS(key)
            except TTSError:
                tts = None
        self.tts = tts
        if stt_factory is None and key:
            stt_factory = lambda terms, cb: ScribeStream(key, terms, cb)   # noqa: E731
        self.stt_factory = stt_factory
        # fal: emotional emergency voice and batch STT fallback (keys from env; None without FAL_KEY)
        if emergency is None and self.tts is not None:
            from server.agents.fal_emergency import ChatterboxVoice
            emergency = ChatterboxVoice.from_env(self.tts)
        self.emergency = emergency
        self.stt_fallback = stt_fallback if stt_fallback is not None else WizperSTT.from_env()
        data_dir = getattr(composer, "data_dir", None)
        self.voices = voices or (Voices.load(data_dir, seed) if data_dir else Voices(seed=seed))
        # Designed voices live in ONE ElevenLabs account. With another account's key they 404, so
        # pilots fall back to the standard premade voices (present in every account) instead of silence.
        self.standard_voices = Voices(seed=seed)
        self.missing_voices: set[str] = set()
        self.freq = FrequencyManager(seed)
        self.metrics = Metrics()
        self.reveal_errors = False
        self.coach_enabled = True                         # phraseology coach (server/voice/coach.py)
        self.replays = ReplayStore()                      # "hear what you missed" (server/voice/replay.py)
        self.on_coach: Optional[Callable[[int], None]] = None   # judge challenge hook (session sets it)
        self._ptt: Optional[_Ptt] = None
        self._tx_stamps: dict[str, Stamps] = {}
        self._ctrl_traces: dict[str, list] = {}       # ctrl tx_id -> [Trace, replies claimed]
        self._trace_dir = T.session_dir(bus)
        self._overlaps: dict[str, _OverlapGroup] = {}
        self._counter = 0
        subscribe = getattr(bus, "subscribe", None)
        if subscribe is not None:
            subscribe(self._on_bus_event)
        if not key and tts is None:
            logger.warning("ELEVENLABS_API_KEY not set: captions only, no speech")
        self.llm_client = None                     # None -> llm.get_client() (key from env)
        try:
            if self.tts is not None:
                self.hub.spawn(self.tts.warm())
            self.hub.spawn(llm_fallback.warm())
            self._warm_backup_stt()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------ helpers
    def _new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter:04d}{secrets.token_hex(1)}"

    def _emit(self, type_: EventTypeName, actor: str, callsign: Optional[str] = None,
              tx_id: Optional[str] = None, **data: Any) -> None:
        self.bus.emit(Event(t=float(self.world.t), type=type_, actor=actor, callsign=callsign,
                            tx_id=tx_id, data=data))

    async def _notice(self, text: str, tx_id: Optional[str] = None) -> None:
        await self.hub.send(CaptionMsg(tx_id=tx_id or self._new_id("n"), speaker="SYSTEM",
                                       stage="final", text=text))

    def _on_bus_event(self, event: Event) -> None:
        """force_overlap inject: the named callers' next transmissions start together."""
        if event.type == "inject" and event.data.get("kind") == "force_overlap":
            callers = [str(c) for c in event.data.get("callers") or []]
            if len(callers) >= 2:
                group = _OverlapGroup(self._new_id("ovl"), set(callers))
                for cs in callers:
                    self._overlaps[cs] = group

    def _take_overlap(self, callsign: str) -> Optional[_OverlapGroup]:
        group = self._overlaps.pop(callsign, None)
        if group is None or time.monotonic() - group.created > OVERLAP_TTL_S:
            return None
        return group

    def _warm_backup_stt(self) -> None:
        """Keep the batch STT backup (fal Wizper) warm: it is slow from cold (4-10.6 s vs ~2 s warm)."""
        backup = self.stt_fallback
        needs = getattr(backup, "needs_warm", None)
        if backup is not None and needs is not None and needs():
            self.hub.spawn(backup.warm())

    def is_frequency_busy(self) -> bool:
        return self.freq.is_busy()

    def set_reveal_errors(self, on: bool) -> None:
        self.reveal_errors = bool(on)

    def _keyterms(self) -> list[str]:
        telephony = [c.telephony for c in self.world.callsigns()]
        fixes: list[str] = []
        for ac in getattr(self.world, "aircraft", {}).values():
            route = list(getattr(ac, "route", []))[getattr(ac, "route_idx", 0):][:4]
            fixes += [str(f) for f in route]
        return build_keyterms(telephony, STATIC_KEYTERMS, fixes)

    # --------------------------------------------------------------------- PTT
    async def on_ptt(self, state: Literal["down", "up"]) -> None:
        if state == "down":
            if self._ptt is not None:
                return
            stamps = Stamps()
            stamps.mark("t_ptt_down")
            stepped_on = {tx: self.freq.active[tx] for tx in self.freq.ctrl_down()}
            ptt = _Ptt(self._new_id("c"), stamps, stepped_on)
            self._ptt = ptt
            self._warm_backup_stt()               # ready by PTT up, in case Scribe fails
            self._emit("ctrl_ptt_down", "CONTROLLER", tx_id=ptt.tx_id)
            if stepped_on:
                self._emit("overlap", "SYSTEM", tx_id=ptt.tx_id, with_ctrl=True,
                           tx_ids=list(stepped_on), callsigns=sorted(set(stepped_on.values())))
            if self.stt_factory is not None:
                try:
                    ptt.stt = self.stt_factory(self._keyterms(), lambda text: self._on_partial(ptt, text))
                    ptt.stt.start()
                except Exception:                                    # noqa: BLE001
                    logger.exception("could not start speech-to-text")
                    ptt.stt = None
            return
        await self._ptt_up()

    def _on_partial(self, ptt: _Ptt, text: str) -> None:
        ptt.partials += 1
        if "t_partial_first" not in ptt.stamps:
            ptt.stamps.mark("t_partial_first")
        ptt.stamps.mark("t_partial_last")
        now = time.monotonic()
        if now - ptt.last_partial < PARTIAL_INTERVAL_S:
            return
        ptt.last_partial = now
        self.hub.spawn(self.hub.send(CaptionMsg(tx_id=ptt.tx_id, speaker="CTRL", stage="partial", text=text)))

    async def on_audio_in(self, pcm16: bytes) -> None:
        ptt = self._ptt
        if ptt is None:
            return
        if len(ptt.audio) < MAX_PTT_AUDIO_BYTES:
            ptt.audio += pcm16
        if ptt.stt is not None:
            ptt.stt.feed(pcm16)

    async def _ptt_up(self) -> None:
        ptt, self._ptt = self._ptt, None
        if ptt is None:
            return
        ptt.stamps.mark("t_ptt_up")
        self.freq.ctrl_up()
        self._emit("ctrl_ptt_up", "CONTROLLER", tx_id=ptt.tx_id)
        if ptt.stepped_on:
            await self._blocked(ptt)
            return
        if ptt.stt is None and self.stt_fallback is None:
            await self._notice("Voice input unavailable. Use the typed input.", ptt.tx_id)
            await self._ctrl_trace_failed(ptt, "no speech-to-text")
            return
        if len(ptt.audio) / 32000 < MIN_TRANSMISSION_S:
            if ptt.stt is not None:
                await ptt.stt.cancel()
            await self._ctrl_trace_failed(ptt, "too short", status="skipped")
            return
        text: Optional[str] = None
        if ptt.stt is not None:
            try:
                text = await ptt.stt.finish()
            except STTError as error:
                logger.warning("speech-to-text failed: %s", error)
        if text is None and self.stt_fallback is not None:
            await self._notice("Speech service unavailable; trying the backup transcriber…", ptt.tx_id)
            try:
                text = await self.stt_fallback.transcribe(bytes(ptt.audio))
                ptt.stamps["stt_fallback"] = 1.0
            except STTError as error:
                logger.warning("backup speech-to-text failed: %s", error)
        if text is None:
            await self._notice("Could not hear that. Try again or use the typed input.", ptt.tx_id)
            await self._ctrl_trace_failed(ptt, "no transcript")
            return
        ptt.stamps.mark("t_final")
        ptt.stamps["partials"] = float(ptt.partials)
        if not text.strip():
            await self._notice("Nothing heard. Try again.", ptt.tx_id)
            await self._ctrl_trace_failed(ptt, "empty transcript", status="skipped")
            return
        await self._process_text(text, "voice", ptt.tx_id, ptt.stamps)

    def _capture_stages(self, trace: "T.Trace", stamps: Stamps, typed: bool) -> None:
        """speech, stt_partials, stt_final: shared by normal, failed and blocked transmissions."""
        down, up = stamps.get("t_ptt_down"), stamps.get("t_ptt_up")
        if typed:
            trace.add("speech", up, up, "skipped", duration_ms=0, provider="typed")
            trace.add("stt_final", up, stamps.get("t_final", up), "skipped", provider="typed")
            return
        if down is not None and up is not None:
            trace.add("speech", down, up, duration_ms=round((up - down) * 1000, 1))
        if "t_partial_first" in stamps:
            trace.add("stt_partials", stamps["t_partial_first"], stamps["t_partial_last"],
                      count=int(stamps.get("partials", 0)))
        if "t_final" in stamps:
            fallback = "stt_fallback" in stamps
            trace.add("stt_final", up, stamps["t_final"], "fallback" if fallback else "ok",
                      provider="fal_wizper" if fallback else "scribe")

    async def _ctrl_trace_failed(self, ptt: _Ptt, reason: str, status: str = "error") -> None:
        try:
            up = ptt.stamps.get("t_ptt_up", time.perf_counter())
            trace = T.Trace(ptt.tx_id, "CTRL", up)
            self._capture_stages(trace, ptt.stamps, typed=False)
            trace.add("stt_final", up, time.perf_counter(), status, provider="scribe", reason=reason)
            await T.send(self.hub, trace, True, self._trace_dir)
        except Exception:                                            # noqa: BLE001  (fail open)
            logger.exception("trace failed")

    async def _blocked(self, ptt: _Ptt) -> None:
        """Controller keyed over a pilot: the instruction is not delivered; the pilot re-calls."""
        if ptt.stt is not None:
            await ptt.stt.cancel()
        self._emit("ctrl_blocked", "CONTROLLER", tx_id=ptt.tx_id,
                   callsigns=sorted(set(ptt.stepped_on.values())))
        await self._notice("Transmission blocked: the frequency was in use.", ptt.tx_id)
        try:
            up = ptt.stamps.get("t_ptt_up", time.perf_counter())
            trace = T.Trace(ptt.tx_id, "CTRL", up, sorted(set(ptt.stepped_on.values()))[0])
            self._capture_stages(trace, ptt.stamps, typed=False)
            trace.add("stt_final", up, up, "skipped", provider="scribe", reason="blocked")
            trace.add("blocked", up, time.perf_counter(), "error",
                      callsigns=sorted(set(ptt.stepped_on.values())))
            await T.send(self.hub, trace, True, self._trace_dir)
        except Exception:                                            # noqa: BLE001
            logger.exception("trace failed")
        for tx_id, callsign in ptt.stepped_on.items():
            try:
                resp = self.pilot.on_blocked(callsign, tx_id)
            except Exception:                                        # noqa: BLE001
                logger.exception("pilot.on_blocked failed")
                continue
            if resp is not None:
                self.hub.spawn(self.speak(resp))

    async def on_typed(self, text: str) -> None:
        await self._process_text(text, "typed")

    # ----------------------------------------------------------------- parsing
    async def _process_text(self, text: str, source: Literal["voice", "typed"],
                            tx_id: Optional[str] = None, stamps: Optional[Stamps] = None) -> Transmission:
        tx_id = tx_id or self._new_id("c")
        stamps = stamps if stamps is not None else Stamps()
        if source == "typed":
            for stage in ("t_ptt_down", "t_ptt_up", "t_final"):
                stamps.mark(stage)
        callsigns = self.world.callsigns()
        stamps.mark("t_parse0")
        tx = parse(text, callsigns=callsigns, fixes=set(self.world.fixes()), source=source,
                   tx_id=tx_id, t=float(self.world.t))
        stamps.mark("t_parsed")
        regex_timings, regex_done = dict(tx.timings), stamps["t_parsed"]
        parse_s = stamps.span("t_final", "t_parsed")
        self._tx_stamps[tx_id] = stamps
        while len(self._tx_stamps) > 64:
            self._tx_stamps.pop(next(iter(self._tx_stamps)))
        names = {a.callsign: a.spoken_plain for a in self.world.public_state().aircraft}

        def caption(t: Transmission, latency: dict) -> CaptionMsg:
            return CaptionMsg(tx_id=tx_id, speaker="CTRL", stage="final", text=text, chips=chips_for(t),
                              plain_english=plain_english(t, names), latency=latency)

        latency = {"parse_s": round(parse_s or 0.0, 4)}
        await self.hub.send(caption(tx, latency))        # the fast regex result is shown immediately
        llm_used = llm_fallback.needs_fallback(tx)
        if llm_used:
            stamps.mark("t_llm0")
            refined = await llm_fallback.refine(tx, callsigns, set(self.world.fixes()), default_runways(),
                                                client=self.llm_client)
            stamps.mark("t_parsed")                       # the decision is final only now
            if refined.parse_path != tx.parse_path:
                latency = {**latency, "llm_s": round(refined.timings.get("llm_s", 0.0), 3)}
                await self.hub.send(caption(refined, latency))   # same tx_id: the UI replaces the caption
            tx = refined
        tx.timings = {**tx.timings, **stamps}
        stamps.mark("t_eval0")
        evals = self.world.evaluate(tx)
        stamps.mark("t_eval1")
        self._emit("ctrl_transmission", "CONTROLLER", tx.receiver, tx_id,
                   tx=tx.model_dump(mode="json"), evals=[e.model_dump(mode="json") for e in evals])
        stamps.mark("t_pd0")
        responses = self.pilot.handle_transmission(tx, evals)
        stamps.mark("t_pd1")
        trace = self._ctrl_trace(tx_id, source, stamps, regex_timings, regex_done, llm_used, tx, evals, responses)
        speaking = [r for r in responses if r.kind != "silence" and r.callsign and r.plan is not None]
        if trace is not None:
            if speaking:
                self._ctrl_traces[tx_id] = [trace, 0]
                while len(self._ctrl_traces) > 64:
                    self._ctrl_traces.pop(next(iter(self._ctrl_traces)))
            else:                                         # nobody answers: the trace ends here
                trace.total_ms = (stamps["t_pd1"] - trace.anchor) * 1000
                await T.send(self.hub, trace, True, self._trace_dir)
        for resp in responses:
            if resp.in_reply_to is None:
                resp.in_reply_to = tx_id
            self.hub.spawn(self.speak(resp))
        if self.coach_enabled:                           # after the reply is scheduled: never delays it
            self.hub.spawn(self._coach(tx, text, stamps))
        return tx

    async def _coach(self, tx: Transmission, raw: str, stamps: Stamps) -> None:
        """Phraseology coach (server/voice/coach.py): rules now, Claude's one-line tip when it lands."""
        try:
            ac = getattr(self.world, "aircraft", {}).get(tx.receiver or "")
            speech = stamps.span("t_ptt_down", "t_ptt_up") if tx.source == "voice" else None
            rev = coach.review(tx, raw, callsigns=self.world.callsigns(),
                               aircraft_alt=getattr(ac, "alt", None), speech_s=speech)
            if not rev.notes:
                return
            msg = CoachMsg(tx_id=tx.tx_id, callsign=tx.receiver, score=rev.score,
                           notes=[CoachNote(**n) for n in rev.notes])
            await self.hub.send(msg)
            if self.on_coach is not None:
                self.on_coach(rev.score)
            tip = await coach.claude_tip(raw, rev, client=self.llm_client)
            if tip:
                await self.hub.send(msg.model_copy(update={"tip": tip}))
        except Exception:                                # noqa: BLE001  (coaching must never break radio)
            logger.exception("coach failed")

    def _ctrl_trace(self, tx_id, source, stamps, rt, regex_done, llm_used, tx, evals, responses):
        """Controller-side stages: capture, STT, language, sim check, pilot decision. Never raises."""
        try:
            trace = T.Trace(tx_id, "CTRL", stamps["t_ptt_up"])
            self._capture_stages(trace, stamps, typed=source == "typed")
            p0 = stamps["t_parse0"]
            n1 = p0 + rt.get("normalise_s", 0.0)
            c1 = n1 + rt.get("callsign_s", 0.0)
            trace.add("normalise", p0, n1, tokens=int(rt.get("tokens", 0)))
            trace.add("parse", c1, regex_done, path=tx.parse_path, clauses=int(rt.get("clauses", 0)),
                      unparsed="unparsed_clause" in tx.ambiguities)
            if llm_used:
                trace.add("llm_fallback", stamps["t_llm0"], stamps["t_parsed"],
                          "ok" if tx.parse_path != "regex" else "fallback",
                          model=llm_fallback_model(), ttft_ms=None, streaming=False,
                          response_ms=round((stamps["t_parsed"] - stamps["t_llm0"]) * 1000, 1))
            trace.add("callsign_match", n1, c1, callsign=tx.receiver, score=rt.get("callsign_score"))
            trace.add("evaluate", stamps["t_eval0"], stamps["t_eval1"], statuses=[e.status for e in evals])
            detail = {"kind": [r.kind for r in responses] if len(responses) != 1 else responses[0].kind,
                      "error_policy": "evaluated"}
            if self.reveal_errors:                         # never reveal a planned error otherwise
                planned = [rec.kind for r in responses for rec in r.errors]
                if planned:
                    detail["planned_error_kind"] = planned[0] if len(planned) == 1 else planned
            trace.add("pilot_decision", stamps["t_pd0"], stamps["t_pd1"], **detail)
            return trace
        except Exception:                                  # noqa: BLE001  (fail open)
            logger.exception("controller trace failed")
            return None

    def _pilot_trace(self, resp: PilotResponse, tx_id: str, stamps: Stamps) -> "Optional[T.Trace]":
        try:
            entry = self._ctrl_traces.get(resp.in_reply_to or "")
            if entry is None:                              # pilot-initiated: anchor = compose start
                return T.Trace(tx_id, resp.callsign or "", stamps["t_plan"], resp.callsign)
            base, claimed = entry
            entry[1] += 1
            ident = base.tx_id if claimed == 0 else f"{base.tx_id}:{resp.callsign}"
            return base.copy_for(ident, resp.callsign)
        except Exception:                                  # noqa: BLE001
            logger.exception("pilot trace failed")
            return None

    # ------------------------------------------------------------------- speak
    @staticmethod
    def _fmt(value: Any) -> str:
        try:
            number = float(value)
            return f"{int(number):,}" if number >= 1000 else str(int(number))
        except (TypeError, ValueError):
            return str(value)

    def error_reveal_text(self, resp: PilotResponse) -> Optional[str]:
        lines = []
        for rec in resp.errors:
            if rec.mode == "execution_only":
                lines.append(f"read back {self._fmt(rec.intended)} correctly, flying {self._fmt(rec.flown_value)}")
            else:
                lines.append(f"said {self._fmt(rec.readback_value)} — cleared {self._fmt(rec.intended)}")
        return "; ".join(lines) or None

    async def _produce(self, text: str, voice_id: str, settings: dict, queue: asyncio.Queue,
                       stamps: Stamps, alignment: list, engines: list, meta: dict,
                       shaped: Optional[tuple[str, dict]] = None, fallback: Optional[tuple] = None) -> None:
        """Stream from the first engine that works; fall back only if nothing was produced yet.

        ``shaped`` = (text with prosody markup, its settings) for engines that set ``prosody``;
        every other engine gets the plain text. ``meta["spoken_text"]`` records what was sent.
        """
        stamps.mark("t_tts_req")
        last_error: Exception = TTSError("no TTS engine")
        plain = (text, settings)
        attempts = [(engine, voice_id) for engine in engines]
        for engine, voice_id in self._with_voice_fallback(attempts, fallback, meta):
            meta["engine"] = engine
            text, settings = shaped if (shaped and getattr(engine, "prosody", False)) else plain
            if fallback is not None and voice_id == fallback[0].id and "fallback_settings" in meta:
                settings = {**settings, **meta["fallback_settings"]}   # standard voice: uncalibrated speed
            meta["spoken_text"] = text
            produced = False
            try:
                timed = getattr(engine, "stream_timed", None)
                if timed is not None:                   # word-synced captions (ElevenLabs timestamps)
                    async for chunk, align in timed(text, voice_id, settings):
                        if align:
                            alignment.append(align)
                        if chunk:
                            produced = True
                            if "t_tts_first" not in stamps:
                                stamps.mark("t_tts_first")
                            await queue.put(chunk)
                else:
                    async for chunk in engine.stream(text, voice_id, settings):
                        produced = True
                        if "t_tts_first" not in stamps:
                            stamps.mark("t_tts_first")
                        await queue.put(chunk)
                await queue.put(None)
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:                               # noqa: BLE001
                last_error = error if isinstance(error, TTSError) else TTSError(repr(error))
                if produced:
                    break                                # mid-stream failure: do not restart the voice
                if "voice_not_found" in str(error) and fallback is not None and voice_id != fallback[0].id:
                    meta["voice_missing"] = voice_id     # the generator retries with a standard voice
                    continue
                logger.warning("%s failed (%s); trying the next voice engine", type(engine).__name__, error)
        await queue.put(last_error)

    def _with_voice_fallback(self, attempts: list, fallback: Optional[tuple], meta: dict):
        """Yield (engine, voice_id); after a voice_not_found, retry that engine with a standard voice."""
        for engine, voice_id in attempts:
            meta.pop("voice_missing", None)
            yield engine, voice_id
            missing = meta.pop("voice_missing", None)
            if missing and fallback is not None:
                self._voice_missing(missing)
                meta["fallback_settings"] = fallback[1]
                meta["voice_name"] = fallback[0].name
                yield engine, fallback[0].id

    def _voice_missing(self, voice_id: str) -> None:
        first = not self.missing_voices
        self.missing_voices.add(voice_id)
        if first:
            logger.warning("ElevenLabs voice %s not found in this account: the designed pilot voices "
                           "belong to the team account. Using standard voices. Use the team ELEVENLABS_API_KEY.",
                           voice_id)
            self.hub.spawn(self._notice(
                "Pilot voices: the designed voices are not in this ElevenLabs account, so standard voices are "
                "used. Put the team ELEVENLABS_API_KEY in .env and restart to hear them."))

    async def speak(self, resp: PilotResponse) -> None:
        if resp.kind == "silence" or not resp.callsign or resp.plan is None:
            return
        stamps = Stamps()
        stamps.mark("t_plan")
        tx_id = self._new_id("p")
        trace = self._pilot_trace(resp, tx_id, stamps)
        try:
            await self._speak(resp, tx_id, stamps, trace)
        finally:                                           # exactly one final trace, whatever happened
            if trace is not None and not trace.sent_final:
                await T.send(self.hub, trace, True, self._trace_dir)

    async def _speak(self, resp: PilotResponse, tx_id: str, stamps: Stamps, trace) -> None:
        cs = resp.callsign
        readback = self.composer.compose(resp.plan)
        stamps.mark("t_composed")
        if trace is not None:
            st = getattr(self.composer, "last_stats", None) or {}
            built = stamps["t_plan"] + float(st.get("build_s", 0.0))
            trace.add("compose", stamps["t_plan"], min(built, stamps["t_composed"]),
                      variant_ids=list(readback.variant_ids), persona=resp.plan.persona)
            trace.add("validate", min(built, stamps["t_composed"]), stamps["t_composed"],
                      "fallback" if readback.fallback_used else ("ok" if readback.validated else "error"),
                      attempts=int(st.get("attempts", 0)), fallback=bool(readback.fallback_used))
            trace.total_ms = (stamps["t_composed"] - trace.anchor) * 1000
            await T.send(self.hub, trace, False, self._trace_dir)          # provisional, after validate
        if not readback.tts_text:
            return
        parent = self._tx_stamps.get(resp.in_reply_to or "", Stamps())
        persona = resp.plan.persona
        stressed = resp.kind in ("mayday", "pan") or persona == "stressed"
        voice = self.voices.voice_for(cs, persona)
        if voice.id in self.missing_voices:
            voice = self.standard_voices.voice_for(cs, persona)
        settings = settings_for(persona, stressed, voice)
        speech = getattr(self.composer, "callsign_speech", None)
        shaped = shape(readback.tts_text, persona, callsign_speech=respell(speech(cs)[1]) if speech else "",
                       seed=resp.plan.seed, stressed=stressed)
        shaped_pair = None
        if shaped.text != readback.tts_text:
            shaped_pair = (shaped.text, compensated_speed(settings, len(readback.tts_text.split()),
                                                          target_wps(persona, stressed), shaped.extra_s))
        not_before = time.monotonic() + max(0.0, resp.reaction_delay_s)

        queue: asyncio.Queue = asyncio.Queue()
        alignment: list[dict] = []
        meta: dict = {}
        producer = None
        first: Any = None
        emergency_call = resp.kind in ("mayday", "pan") and self.emergency is not None
        engines = [e for e in ([self.emergency] if emergency_call else []) + [self.tts] if e is not None]
        if engines:
            standard = self.standard_voices.voice_for(cs, persona)
            producer = asyncio.create_task(self._produce(readback.tts_text, voice.id, settings, queue, stamps,
                                                         alignment, engines, meta, shaped_pair,
                                                         fallback=(standard, settings_for(persona, stressed))))
            try:
                first = await asyncio.wait_for(
                    queue.get(), EMERGENCY_FIRST_AUDIO_S if emergency_call else FIRST_AUDIO_TIMEOUT_S)
            except asyncio.TimeoutError:
                first = TTSError("no audio within timeout")
        tts_ok = producer is not None and not isinstance(first, Exception)
        if isinstance(first, Exception):
            logger.warning("TTS unavailable for %s (%s); speaking by caption only", cs, first)

        if trace is not None:
            engine = meta.get("engine")
            model = getattr(engine, "model", None) or (type(engine).__name__ if engine else None)
            if "t_tts_first" in stamps:
                trace.machine_ms = (stamps["t_tts_first"] - trace.anchor) * 1000
                trace.add("tts_first_audio", stamps["t_tts_req"], stamps["t_tts_first"], model=model,
                          voice=meta.get("voice_name", voice.name), machine_ms=round(trace.machine_ms, 1))
            elif "t_tts_req" in stamps:
                trace.add("tts_first_audio", stamps["t_tts_req"], time.perf_counter(), "error",
                          model=model, voice=voice.name)
        on_air = False
        group = self._take_overlap(cs)
        try:
            if group is not None:
                group = await self._meet(group, cs, not_before)
            if group is None:
                await self.freq.wait_turn(resp.priority, not_before)
            self.freq.begin(tx_id, cs)
            on_air = True
            stamps.mark("t_release")
            started = stamps.mark("t_play")
            if trace is not None:
                hold_from = stamps.get("t_tts_first", stamps["t_composed"])
                trace.add("reaction_hold", hold_from, stamps["t_release"],
                          persona_delay_ms=round(resp.reaction_delay_s * 1000, 1))
                trace.total_ms = (started - trace.anchor) * 1000             # = the "Response" chip
                await T.send(self.hub, trace, False, self._trace_dir)      # updated at release
            self.world.set_transmitting(cs, True)
            if group is not None:
                group.tx_ids.append(tx_id)
                if len(group.tx_ids) == len(group.arrived):     # the last one on the air announces it
                    self._emit("overlap", "SYSTEM", tx_id=tx_id, with_ctrl=False, tx_ids=list(group.tx_ids),
                               callsigns=sorted(group.arrived), overlap_group=group.id)
            if tts_ok:
                await self.hub.send(AudioStartMsg(
                    tx_id=tx_id, callsign=cs, format=meta["engine"].audio_format,
                    sample_rate=meta["engine"].sample_rate,
                    blocked=self.freq.is_blocked(tx_id), overlap_group=group.id if group else None,
                    emotion="stressed" if stressed else "normal"))
            base = parent.get("t_ptt_up", stamps["t_plan"])     # same anchor as the trace's machine_ms
            first_audio = (stamps["t_tts_first"] - base) if "t_tts_first" in stamps else 0.0
            perceived = started - parent.get("t_ptt_up", stamps["t_plan"])
            latency = {"parse_s": round(parent.span("t_final", "t_parsed") or 0.0, 4),
                       "first_audio_s": round(first_audio, 3), "perceived_s": round(perceived, 3)}
            await self.hub.send(CaptionMsg(
                tx_id=tx_id, speaker=cs, stage="pilot_text", text=readback.text, latency=latency,
                error_reveal=self.error_reveal_text(resp) if self.reveal_errors else None))
            audio_bytes = 0
            spoken_pcm = bytearray() if resp.errors else None      # kept only for "hear what you missed"
            if tts_ok:
                seq, item = 0, first
                while item is not None and not isinstance(item, Exception):
                    audio_bytes += len(item)
                    if spoken_pcm is not None:
                        spoken_pcm += item
                    await self.hub.send(AudioChunkMsg(tx_id=tx_id, seq=seq, data_b64=base64.b64encode(item).decode("ascii")))
                    seq += 1
                    item = await queue.get()
                if isinstance(item, Exception):
                    logger.warning("TTS stream broke for %s: %s", cs, item)
                await self.hub.send(AudioEndMsg(tx_id=tx_id))
                if spoken_pcm:
                    try:
                        self.replays.keep(resp, bytes(spoken_pcm), meta["engine"].sample_rate,
                                          meta["engine"].audio_format, readback.text)
                    except Exception:                                    # noqa: BLE001
                        logger.exception("replay not kept")
            words = readback.text.split()
            if tts_ok and audio_bytes:
                engine = meta["engine"]
                bytes_per_s = 2 * engine.sample_rate if engine.audio_format == "pcm_s16le" else 16000
                duration = audio_bytes / bytes_per_s
            else:
                duration = len(words) * EST_SPEECH_S_PER_WORD
            await self.hub.send(CaptionMsg(tx_id=tx_id, speaker=cs, stage="pilot_words", text=readback.text,
                                           words=self._word_times(readback, alignment, duration,
                                                                  meta.get("spoken_text"))))
            # The pilot stays on the air for the length of the speech (audio arrives faster than realtime).
            await asyncio.sleep(max(0.0, started + duration - time.perf_counter()))
            stamps.mark("t_audio_end")
        finally:
            if on_air:
                self.world.set_transmitting(cs, False)
                blocked = self.freq.end(tx_id) or group is not None
            else:
                blocked = False
                self.freq.cancel_reservation()
            if producer is not None:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
        self._finish_pilot(resp, tx_id, readback, stamps, parent, blocked, tts_ok)
        if trace is not None and "t_audio_end" in stamps:
            trace.add("playback", stamps["t_release"], stamps["t_audio_end"],
                      "ok" if tts_ok else "fallback", **({} if tts_ok else {"audio": "caption only"}))
            if resp.apply:
                trace.add("apply_scheduled", stamps["t_audio_end"], stamps["t_apply"],
                          apply_at_sim_s=round(stamps["apply_at_sim_s"], 2))
            else:
                trace.add("apply_scheduled", stamps["t_audio_end"], stamps["t_audio_end"], "skipped")
            if blocked:
                trace.add("blocked", stamps["t_release"], stamps["t_audio_end"], "error",
                          reason="overlap" if group is not None else "stepped on")
            await T.send(self.hub, trace, True, self._trace_dir)
        if group is not None:                                  # nobody heard either call: both re-call
            self._emit("pilot_blocked", "PILOT", cs, tx_id, overlap_group=group.id)
            try:
                recall = self.pilot.on_blocked(cs, tx_id)
            except Exception:                                  # noqa: BLE001
                logger.exception("pilot.on_blocked failed")
                recall = None
            if recall is not None:
                self.hub.spawn(self.speak(recall))

    @staticmethod
    def _word_times(readback, alignment: list[dict], duration: float, spoken_text: Optional[str] = None):
        """Real per-word timings from the TTS alignment; an even spread if unavailable or mismatched."""
        if alignment:
            chars, starts, ends = [], [], []
            for a in alignment:
                chars += list(a.get("characters") or [])
                starts += list(a.get("character_start_times_seconds") or [])
                ends += list(a.get("character_end_times_seconds") or [])
            sent = spoken_text or readback.tts_text
            spoken = spoken_word_times(sent, chars, starts, ends)
            if spoken and sent != readback.tts_text:            # drop breaks/fillers, back to plain words
                spoken = restore_word_times(readback.tts_text, spoken)
            words = align_display_words(readback.text, spoken) if spoken else None
            if words:
                return words
            logger.info("TTS alignment did not line up with the caption; spreading words evenly")
        return estimated_words(readback.text, duration)

    async def _meet(self, group: _OverlapGroup, cs: str, not_before: float) -> Optional[_OverlapGroup]:
        """Wait (after the reaction delay) until every caller in the group is ready, then go together."""
        await asyncio.sleep(max(0.0, not_before - time.monotonic()))
        if group.dead:
            return None
        group.arrived.add(cs)
        if group.arrived >= group.members:
            group.released.set()
        try:
            await asyncio.wait_for(group.released.wait(), OVERLAP_WAIT_S)
        except asyncio.TimeoutError:
            group.dead = True
            return None                                        # partner never came: talk normally
        return None if group.dead else group

    def _finish_pilot(self, resp: PilotResponse, tx_id: str, readback, stamps: Stamps, parent: Stamps,
                      blocked: bool, spoke: bool) -> None:
        cs = resp.callsign
        t_end = float(self.world.t)
        if not blocked:
            self.pilot.on_readback_spoken(resp, t_end)
        if resp.apply:
            self.world.schedule_apply(cs, resp.apply, at=t_end + resp.apply_delay_s)
            stamps.mark("t_apply")
            stamps["apply_at_sim_s"] = t_end + resp.apply_delay_s
        timings = {**{k: v for k, v in parent.items() if k in ("t_ptt_down", "t_ptt_up", "t_final", "t_parsed")},
                   **stamps}
        self.metrics.record("machine_s", stamps.span("t_plan", "t_play"))
        if "t_ptt_up" in parent and "t_tts_first" in stamps:
            self.metrics.record("machine_ptt_s", stamps["t_tts_first"] - parent["t_ptt_up"])
        if "t_final" in parent and "t_play" in stamps:
            self.metrics.record("pipeline_s", stamps["t_play"] - parent["t_final"])
        if "t_ptt_up" in parent and "t_play" in stamps:
            self.metrics.record("perceived_s", stamps["t_play"] - parent["t_ptt_up"])
        if "t_tts_first" in stamps:          # same anchor as the caption chip and the trace's machine_ms
            self.metrics.record("first_audio_s", stamps["t_tts_first"] - parent.get("t_ptt_up", stamps["t_plan"]))
        self._emit("pilot_transmission", "PILOT", cs, tx_id, text=readback.text, kind=resp.kind,
                   plan=resp.plan.model_dump(mode="json"), timings=timings, in_reply_to=resp.in_reply_to,
                   blocked=blocked, spoken_audio=spoke, fallback_used=readback.fallback_used,
                   errors=[r.id for r in resp.errors])

    async def aclose(self) -> None:
        if self._ptt is not None and self._ptt.stt is not None:
            await self._ptt.stt.cancel()
        if self.tts is not None:
            await self.tts.aclose()


def llm_fallback_model() -> str:
    from server.agents import llm
    return llm.FAST_MODEL
