"""Offline test doubles for the voice pipeline (never imported by production code)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from server.contracts import (ClearanceElement, Evaluation, Instruction, PilotResponse, ReadbackPlan,
                              Transmission)
from server.voice.parser.callsign import DEMO_CALLSIGNS

DATA = Path(__file__).resolve().parents[3] / "data"


class FakeHub:
    def __init__(self):
        self.messages: list[Any] = []
        self.tasks: set[asyncio.Task] = set()

    async def send(self, msg, roles=None):
        self.messages.append(msg)

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def settle(self, timeout: float = 5.0):
        """Wait for every spawned operation (speak(), partial captions) to finish."""
        deadline = asyncio.get_running_loop().time() + timeout
        while self.tasks and asyncio.get_running_loop().time() < deadline:
            await asyncio.wait(list(self.tasks), timeout=0.05)

    def of(self, kind: str, **match) -> list:
        out = []
        for m in self.messages:
            if getattr(m, "type", None) == kind and all(getattr(m, k, None) == v for k, v in match.items()):
                out.append(m)
        return out

    def captions(self, stage: str) -> list:
        return [m for m in self.messages if getattr(m, "type", None) == "caption" and m.stage == stage]


class FakeBus:
    def __init__(self):
        self.events: list = []
        self.subs: list = []

    def emit(self, e):
        self.events.append(e)
        for cb in self.subs:
            cb(e)

    def subscribe(self, cb):
        self.subs.append(cb)

    def now(self):
        return 0.0

    def of(self, type_: str) -> list:
        return [e for e in self.events if e.type == type_]


@dataclass
class FakeAircraft:
    callsign: str
    spoken_plain: str


@dataclass
class FakeState:
    aircraft: list = field(default_factory=lambda: [FakeAircraft("BAW117", "British Airways 117"),
                                                    FakeAircraft("EZY45AB", "easyJet 45AB")])


class FakeWorld:
    def __init__(self):
        self.t = 100.0
        self.seed = 42
        self.transmitting: list[tuple[str, bool]] = []
        self.applied: list[tuple[str, list, float]] = []

    def callsigns(self):
        return list(DEMO_CALLSIGNS)

    def fixes(self):
        return ["WILLO", "TIMBA", "HOLLY"]

    def evaluate(self, tx: Transmission):
        return [Evaluation(instr_idx=i, status="OK") for i, _ in enumerate(tx.instructions)]

    def public_state(self):
        return FakeState()

    def set_transmitting(self, cs: str, on: bool):
        self.transmitting.append((cs, on))

    def schedule_apply(self, cs, elements, at):
        self.applied.append((cs, elements, at))


def heading_response(cs="BAW117", value=240, kind="full", delay=0.0, priority=0, in_reply_to=None,
                     apply=True, persona="crisp_airline", errors=None) -> PilotResponse:
    el = Instruction(type="heading", value=value, turn_dir="L")
    plan = ReadbackPlan(callsign=cs, persona=persona, kind=kind, elements=[el], seed=1)
    ce = ClearanceElement(type="heading", intended=el, readback=el, flown=el)
    return PilotResponse(callsign=cs, kind=kind, plan=plan, apply=[ce] if apply else [],
                         apply_delay_s=1.5, reaction_delay_s=delay, priority=priority,
                         in_reply_to=in_reply_to, errors=errors or [])


class FakePilot:
    """Scripted pilot: returns ``responses`` for every transmission."""

    def __init__(self, responses=None):
        self.responses = responses if responses is not None else [heading_response()]
        self.received: list[Transmission] = []
        self.spoken: list = []
        self.blocked_calls: list = []
        self.recall = None

    def handle_transmission(self, tx, evals):
        self.received.append(tx)
        return [r.model_copy() for r in self.responses]

    def on_readback_spoken(self, resp, t_end):
        self.spoken.append((resp, t_end))

    def on_blocked(self, callsign, tx_id):
        self.blocked_calls.append((callsign, tx_id))
        return self.recall

    def on_sim_event(self, e):
        return None


class FakeComposer:
    data_dir = DATA

    def __init__(self):
        from server.pilot.readback import Composer
        self.real = Composer(DATA)

    def compose(self, plan):
        return self.real.compose(plan)

    def callsign_speech(self, callsign):
        return self.real.callsign_speech(callsign)


class FakeTTS:
    """Streams a few PCM chunks; ``delay`` simulates network latency to first audio."""
    audio_format = "pcm_s16le"
    sample_rate = 22050

    def __init__(self, chunks: int = 3, chunk_bytes: int = 490, delay: float = 0.0, fail: Optional[Exception] = None,
                 fail_after: Optional[int] = None):
        self.chunks, self.chunk_bytes, self.delay, self.fail = chunks, chunk_bytes, delay, fail
        self.fail_after = fail_after
        self.requests: list[tuple[str, str, dict]] = []

    async def warm(self):
        pass

    async def aclose(self):
        pass

    async def stream(self, text, voice_id, settings=None):
        self.requests.append((text, voice_id, settings or {}))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None and self.fail_after is None:
            raise self.fail
        for i in range(self.chunks):
            if self.fail is not None and self.fail_after == i:
                raise self.fail
            yield bytes([i % 250]) * self.chunk_bytes


class FakeSTT:
    """Stands in for ScribeStream; ``text`` is returned by ``finish()``."""

    def __init__(self, text="speedbird one one seven turn left heading two four zero", error=None, on_partial=None):
        self.text, self.error, self.on_partial = text, error, on_partial
        self.started = False
        self.cancelled = False
        self.bytes_fed = 0

    @property
    def duration_s(self):
        return self.bytes_fed / 32000

    def start(self):
        self.started = True

    def feed(self, pcm):
        self.bytes_fed += len(pcm)

    async def finish(self):
        if self.error:
            raise self.error
        return self.text

    async def cancel(self):
        self.cancelled = True


class FakeTimedTTS(FakeTTS):
    """Also offers stream_timed: perfectly regular 0.06 s per character alignment."""

    def __init__(self, *args, break_alignment=False, prosody=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.break_alignment = break_alignment
        self.prosody = prosody

    async def stream_timed(self, text, voice_id, settings=None):
        self.requests.append((text, voice_id, settings or {}))
        half = len(text) // 2
        pieces = [text[:half], text[half:]]
        offset = 0
        for i in range(self.chunks):
            align = None
            if i < 2:
                chars = list(pieces[i]) if not self.break_alignment else list("garbage")
                align = {"characters": chars,
                         "character_start_times_seconds": [(offset + k) * 0.06 for k in range(len(chars))],
                         "character_end_times_seconds": [(offset + k) * 0.06 + 0.05 for k in range(len(chars))]}
                offset += len(chars)
            yield bytes([i % 250]) * self.chunk_bytes, align
