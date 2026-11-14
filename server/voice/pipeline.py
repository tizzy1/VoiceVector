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

from server.contracts import (AudioChunkMsg, AudioEndMsg, AudioStartMsg, CaptionMsg, Event,
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
from server.voice.voices import Voices, settings_for

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
        self.freq = FrequencyManager(seed)
        self.metrics = Metrics()
        self.reveal_errors = False
        self._ptt: Optional[_Ptt] = None
        self._tx_stamps: dict[str, Stamps] = {}
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
            return
        if len(ptt.audio) / 32000 < MIN_TRANSMISSION_S:
            if ptt.stt is not None:
                await ptt.stt.cancel()
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
            return
        ptt.stamps.mark("t_final")
        if not text.strip():
            await self._notice("Nothing heard. Try again.", ptt.tx_id)
            return
        await self._process_text(text, "voice", ptt.tx_id, ptt.stamps)

    async def _blocked(self, ptt: _Ptt) -> None:
        """Controller keyed over a pilot: the instruction is not delivered; the pilot re-calls."""
        if ptt.stt is not None:
            await ptt.stt.cancel()
        self._emit("ctrl_blocked", "CONTROLLER", tx_id=ptt.tx_id,
                   callsigns=sorted(set(ptt.stepped_on.values())))
        await self._notice("Transmission blocked: the frequency was in use.", ptt.tx_id)
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
        tx = parse(text, callsigns=callsigns, fixes=set(self.world.fixes()), source=source,
                   tx_id=tx_id, t=float(self.world.t))
        stamps.mark("t_parsed")
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
        if llm_fallback.needs_fallback(tx):
            refined = await llm_fallback.refine(tx, callsigns, set(self.world.fixes()), default_runways(),
                                                client=self.llm_client)
            stamps.mark("t_parsed")                       # the decision is final only now
            if refined.parse_path != tx.parse_path:
                latency = {**latency, "llm_s": round(refined.timings.get("llm_s", 0.0), 3)}
                await self.hub.send(caption(refined, latency))   # same tx_id: the UI replaces the caption
            tx = refined
        tx.timings = {**tx.timings, **stamps}
        evals = self.world.evaluate(tx)
        self._emit("ctrl_transmission", "CONTROLLER", tx.receiver, tx_id,
                   tx=tx.model_dump(mode="json"), evals=[e.model_dump(mode="json") for e in evals])
        for resp in self.pilot.handle_transmission(tx, evals):
            if resp.in_reply_to is None:
                resp.in_reply_to = tx_id
            self.hub.spawn(self.speak(resp))
        return tx

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
                       stamps: Stamps, alignment: list, engines: list, meta: dict) -> None:
        """Stream from the first engine that works; fall back only if nothing was produced yet."""
        stamps.mark("t_tts_req")
        last_error: Exception = TTSError("no TTS engine")
        for engine in engines:
            meta["engine"] = engine
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
                logger.warning("%s failed (%s); trying the next voice engine", type(engine).__name__, error)
        await queue.put(last_error)

    async def speak(self, resp: PilotResponse) -> None:
        if resp.kind == "silence" or not resp.callsign or resp.plan is None:
            return
        cs = resp.callsign
        stamps = Stamps()
        stamps.mark("t_plan")
        readback = self.composer.compose(resp.plan)
        if not readback.tts_text:
            return
        parent = self._tx_stamps.get(resp.in_reply_to or "", Stamps())
        persona = resp.plan.persona
        stressed = resp.kind in ("mayday", "pan") or persona == "stressed"
        voice = self.voices.voice_for(cs, persona)
        settings = settings_for(persona, stressed)
        not_before = time.monotonic() + max(0.0, resp.reaction_delay_s)

        queue: asyncio.Queue = asyncio.Queue()
        alignment: list[dict] = []
        meta: dict = {}
        producer = None
        first: Any = None
        emergency_call = resp.kind in ("mayday", "pan") and self.emergency is not None
        engines = [e for e in ([self.emergency] if emergency_call else []) + [self.tts] if e is not None]
        if engines:
            producer = asyncio.create_task(self._produce(readback.tts_text, voice.id, settings, queue, stamps,
                                                         alignment, engines, meta))
            try:
                first = await asyncio.wait_for(
                    queue.get(), EMERGENCY_FIRST_AUDIO_S if emergency_call else FIRST_AUDIO_TIMEOUT_S)
            except asyncio.TimeoutError:
                first = TTSError("no audio within timeout")
        tts_ok = producer is not None and not isinstance(first, Exception)
        if isinstance(first, Exception):
            logger.warning("TTS unavailable for %s (%s); speaking by caption only", cs, first)

        tx_id = self._new_id("p")
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
            base = parent.get("t_final", stamps["t_plan"])      # stage stamps live in two dicts
            first_audio = (stamps["t_tts_first"] - base) if "t_tts_first" in stamps else 0.0
            perceived = started - parent.get("t_ptt_up", stamps["t_plan"])
            latency = {"parse_s": round(parent.span("t_final", "t_parsed") or 0.0, 4),
                       "first_audio_s": round(first_audio, 3), "perceived_s": round(perceived, 3)}
            await self.hub.send(CaptionMsg(
                tx_id=tx_id, speaker=cs, stage="pilot_text", text=readback.text, latency=latency,
                error_reveal=self.error_reveal_text(resp) if self.reveal_errors else None))
            audio_bytes = 0
            if tts_ok:
                seq, item = 0, first
                while item is not None and not isinstance(item, Exception):
                    audio_bytes += len(item)
                    await self.hub.send(AudioChunkMsg(tx_id=tx_id, seq=seq, data_b64=base64.b64encode(item).decode("ascii")))
                    seq += 1
                    item = await queue.get()
                if isinstance(item, Exception):
                    logger.warning("TTS stream broke for %s: %s", cs, item)
                await self.hub.send(AudioEndMsg(tx_id=tx_id))
            words = readback.text.split()
            if tts_ok and audio_bytes:
                engine = meta["engine"]
                bytes_per_s = 2 * engine.sample_rate if engine.audio_format == "pcm_s16le" else 16000
                duration = audio_bytes / bytes_per_s
            else:
                duration = len(words) * EST_SPEECH_S_PER_WORD
            await self.hub.send(CaptionMsg(tx_id=tx_id, speaker=cs, stage="pilot_words", text=readback.text,
                                           words=self._word_times(readback, alignment, duration)))
            # The pilot stays on the air for the length of the speech (audio arrives faster than realtime).
            await asyncio.sleep(max(0.0, started + duration - time.monotonic()))
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
    def _word_times(readback, alignment: list[dict], duration: float):
        """Real per-word timings from the TTS alignment; an even spread if unavailable or mismatched."""
        if alignment:
            chars, starts, ends = [], [], []
            for a in alignment:
                chars += list(a.get("characters") or [])
                starts += list(a.get("character_start_times_seconds") or [])
                ends += list(a.get("character_end_times_seconds") or [])
            spoken = spoken_word_times(readback.tts_text, chars, starts, ends)
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
        timings = {**{k: v for k, v in parent.items() if k in ("t_ptt_down", "t_ptt_up", "t_final", "t_parsed")},
                   **stamps}
        self.metrics.record("machine_s", stamps.span("t_plan", "t_play"))
        if "t_final" in parent and "t_play" in stamps:
            self.metrics.record("pipeline_s", stamps["t_play"] - parent["t_final"])
        if "t_ptt_up" in parent and "t_play" in stamps:
            self.metrics.record("perceived_s", stamps["t_play"] - parent["t_ptt_up"])
        if "t_final" in parent and "t_tts_first" in stamps:
            self.metrics.record("first_audio_s", stamps["t_tts_first"] - parent["t_final"])
        self._emit("pilot_transmission", "PILOT", cs, tx_id, text=readback.text, kind=resp.kind,
                   plan=resp.plan.model_dump(mode="json"), timings=timings, in_reply_to=resp.in_reply_to,
                   blocked=blocked, spoken_audio=spoke, fallback_used=readback.fallback_used,
                   errors=[r.id for r in resp.errors])

    async def aclose(self) -> None:
        if self._ptt is not None and self._ptt.stt is not None:
            await self._ptt.stt.cancel()
        if self.tts is not None:
            await self.tts.aclose()
