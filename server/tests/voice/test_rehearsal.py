"""20-minute demo rehearsal on the real stack (ASSIGNMENT §10 "zero unplanned readback errors over a
20-minute session").

Bhavin's real Session wiring with Tudor's real World, PilotEngine and RulesEngine and my parser,
composer and pipeline. Only the audio is fake (1 ms clips) and the simulator is ticked by the test,
so 20 simulated minutes run in seconds. A scripted "good controller" issues varied clearances,
corrects every audible readback error, uses confirm / say again / read back, and the instructor fires
injects. Every link of the chain is then checked.
"""
import asyncio
import logging
import random
import time
from collections import Counter

import pytest

from server.agents.narrative import Narrative
from server.contracts import ControlMsg
from server.pilot.engine import PilotEngine
from server.pilot.readback import Composer
from server.rules.engine import RulesEngine
from server.session import Session
from server.sim.world import World
from server.tests.voice.fakes import FakeTTS
from server.voice import frequency
from server.voice.pipeline import VoicePipeline

SIM_SECONDS = 20 * 60
TX_EVERY_S = 12
ERROR_RATE = "high"

CLEARANCES = [
    "turn left heading two four zero", "turn right heading one eight zero", "fly heading three six zero",
    "descend altitude four thousand feet", "descend altitude five thousand feet", "descend flight level seven zero",
    "descend flight level eight zero", "climb altitude six thousand feet", "reduce speed two one zero knots",
    "speed one eight zero knots or greater", "turn left heading two six zero descend altitude four thousand feet",
    "turn right heading two three zero descend altitude three thousand feet speed one eight zero knots",
    "cleared i l s approach runway two six left", "turn left heading two niner zero cleared i l s approach runway two six left",
    "squawk four five two one", "direct willo", "hold at timba", "q n h one zero one three",
    "expedite descent", "maintain flight level niner zero", "resume own navigation", "no a t c speed restriction",
    "contact tower one two four decimal two two five", "report established", "turn left ten degrees",
]
QUERIES = ["confirm cleared level", "confirm heading", "say again", "read back"]
INJECTS = {300: {"kind": "force_overlap"}, 420: {"kind": "pan", "pick": True, "params": {"reason": "medical"}},
           600: {"kind": "go_around", "pick": True}, 780: {"kind": "spawn_conflict"}, 900: {"kind": "force_overlap"}}


class Recorder:
    def __init__(self):
        self.readbacks, self.exchanges, self.task_errors = [], [], []


REC = Recorder()


class RecordingComposer(Composer):
    def compose(self, plan):
        readback = super().compose(plan)
        REC.readbacks.append((plan, readback))
        return readback


class RecordingPilot(PilotEngine):
    """Tudor's engine, unchanged, except that think-time is shortened (wall-clock) for the test."""

    def handle_transmission(self, tx, evals):
        out = super().handle_transmission(tx, evals)
        REC.exchanges.append((tx, out))
        for resp in out:
            resp.reaction_delay_s = 0.001
        return out

    def on_sim_event(self, e):
        resp = super().on_sim_event(e)
        if resp is not None:
            resp.reaction_delay_s = 0.001
        return resp

    def on_blocked(self, callsign, tx_id):
        resp = super().on_blocked(callsign, tx_id)
        if resp is not None:
            resp.reaction_delay_s = 0.001
        return resp


class RecordingHub:
    def __init__(self):
        self.messages, self.tasks = [], set()

    def spawn(self, coroutine):
        task = asyncio.get_running_loop().create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            REC.task_errors.append(repr(task.exception()))

    async def send(self, msg, roles=None):
        self.messages.append(msg)

    def on_event(self, event):
        pass

    def counts(self):
        return {"radar": 1, "viz": 1}

    async def settle(self, limit=5.0):
        deadline = time.monotonic() + limit
        while self.tasks and time.monotonic() < deadline:
            await asyncio.wait(list(self.tasks), timeout=0.05)


def voice_factory(world, pilot, composer, bus, hub):
    return VoicePipeline(world, pilot, composer, bus, hub, tts=FakeTTS(chunks=1, chunk_bytes=44), seed=7)


FACTORIES = dict(world=World, pilot=RecordingPilot, rules=RulesEngine, composer=RecordingComposer,
                 voice=voice_factory, narrative=Narrative)


def element_matches(planned, said) -> bool:
    keys = ("value", "turn_dir", "runway", "fix", "approach_type", "unit", "frequency")
    return all(getattr(planned, k) == getattr(said, k) for k in keys if getattr(said, k) is not None)


@pytest.mark.asyncio
async def test_twenty_minute_demo_rehearsal(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(frequency, "GAP_RANGE_S", (0.0005, 0.001))
    caplog.set_level(logging.WARNING, logger="server")
    started = time.monotonic()
    rng = random.Random(2026)
    hub = RecordingHub()
    session = Session(hub, FACTORIES, tmp_path)
    session.settings = session.settings.model_copy(update={"error_rate": ERROR_RATE})
    await session.start()
    session.loop_task.cancel()                                     # the test drives the clock
    world, pilot, voice, bus = session.world, session.pilot, session.voice, session.bus

    spoken_tx: dict[str, str] = {}
    pending_corrections: list[str] = []
    seen_errors: set[str] = set()

    def on_event(e):
        if e.type == "pilot_transmission" and e.data.get("errors") and not e.data.get("blocked"):
            reply_to = e.data.get("in_reply_to")
            for eid in e.data["errors"]:
                rec = pilot.errors.get(eid)
                if rec and rec.mode != "execution_only" and eid not in seen_errors and reply_to in spoken_tx:
                    seen_errors.add(eid)
                    pending_corrections.append(spoken_tx[reply_to])      # "callsign | clearance"

    bus.subscribe(on_event)
    transmissions = 0
    for t in range(1, SIM_SECONDS + 1):
        world.tick(1.0)
        pilot.tick(world.t)
        session.rules.on_tick(world.snapshot())
        if t in INJECTS:
            spec = dict(INJECTS[t])
            params = {"kind": spec["kind"], "params": spec.get("params", {})}
            if spec.get("pick") and world.callsigns():
                params["callsign"] = rng.choice(world.callsigns()).icao
            await session.handle_control(ControlMsg(action="inject", params=params))
        if t % TX_EVERY_S == 0 and world.callsigns():
            info = rng.choice(world.callsigns())
            if pending_corrections:
                original = pending_corrections.pop(0)
                cs_spoken, _, rest = original.partition(" | ")
                text = f"{cs_spoken} negative {rest}"
            elif rng.random() < 0.15:
                text = f"{info.spoken} {rng.choice(QUERIES)}"
            else:
                text = f"{info.spoken} | {rng.choice(CLEARANCES)}"
            tx = await voice._process_text(text.replace(" | ", " "), "typed")
            if " | " in text:
                spoken_tx[tx.tx_id] = text
            transmissions += 1
        if hub.tasks:
            await hub.settle()
    await hub.settle()
    await session.handle_control(ControlMsg(action="end_session"))
    await hub.settle()
    elapsed = time.monotonic() - started

    # ------------------------------------------------------------------ checks
    pilot_tx = [e for e in _events(session, tmp_path) if e["type"] == "pilot_transmission"]
    kinds = Counter(e["data"]["kind"] for e in pilot_tx)
    print(f"\nrehearsal: {SIM_SECONDS // 60} sim min in {elapsed:.1f}s, {transmissions} controller transmissions,"
          f" {len(pilot_tx)} pilot transmissions {dict(kinds)}, {len(REC.readbacks)} readbacks composed,"
          f" errors planned {len(pilot.errors)} caught {sum(r.outcome == 'caught' for r in pilot.errors.values())}")

    assert not REC.task_errors, REC.task_errors[:3]
    problems = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert not problems, problems[:3]

    bad = [(p.kind, r.text) for p, r in REC.readbacks if not r.validated or r.fallback_used]
    assert not bad, bad[:5]
    assert voice.composer.rejections == 0

    # zero UNPLANNED readback errors: anything read back differently must be a planned error
    unplanned = []
    for tx, responses in REC.exchanges:
        for resp in responses:
            if resp.kind != "full" or resp.plan is None or resp.callsign != tx.receiver:
                continue
            planned_types = {e.element_type for e in resp.errors}
            for said in tx.instructions:
                match = [el for el in resp.plan.elements if el.type == said.type]
                if match and not any(element_matches(el, said) for el in match) and said.type not in planned_types:
                    unplanned.append((tx.raw_text, said.type, [m.model_dump(exclude_defaults=True) for m in match]))
    assert not unplanned, unplanned[:3]

    assert transmissions >= SIM_SECONDS // TX_EVERY_S - 5
    assert kinds["full"] > 40 and kinds["mayday"] >= 1, kinds
    assert kinds["partial_correction"] >= 1, "readback corrections were exercised"
    assert any(r.outcome == "caught" for r in pilot.errors.values()), "at least one planned error was caught"
    overlaps = Counter(m.overlap_group for m in hub.messages if getattr(m, "type", "") == "audio_start" and m.overlap_group)
    assert overlaps and max(overlaps.values()) == 2, "force_overlap produced a simultaneous pair"
    report = next(m for m in hub.messages if getattr(m, "type", "") == "report").report
    assert report.stats["transmissions"] == transmissions and report.narrative and len(report.coaching) == 3
    assert elapsed < 120, f"rehearsal too slow: {elapsed:.0f}s"


def _events(session, tmp_path):
    import json
    files = list(tmp_path.glob("*/events.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text().splitlines() if line.strip()]
