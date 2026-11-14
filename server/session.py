"""Session lifecycle and fixed-contract integration wiring."""
import asyncio
import importlib
import logging
import secrets
from pathlib import Path

from server.contracts import (
    CaptionMsg, ControlMsg, Event, ReportMsg, SessionMsg, SessionSettings, StateMsg,
)
from server.hub import Hub

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "world": ("server.sim.world", "World"),
    "pilot": ("server.pilot.engine", "PilotEngine"),
    "rules": ("server.rules.engine", "RulesEngine"),
    "composer": ("server.pilot.readback", "Composer"),
    "voice": ("server.voice.pipeline", "VoicePipeline"),
    "narrative": ("server.agents.narrative", "Narrative"),
}


class EventLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8")
        self.clock = lambda: 0.0
        self.subscribers = []

    def now(self) -> float:
        return self.clock()

    def subscribe(self, callback) -> None:
        self.subscribers.append(callback)

    def emit(self, event: Event) -> None:
        self.file.write(event.model_dump_json() + "\n")
        self.file.flush()
        for callback in tuple(self.subscribers):
            try:
                callback(event)
            except Exception:
                logger.exception("Event subscriber failed for %s", event.type)

    def close(self) -> None:
        self.file.close()


def load_factories() -> tuple[dict, list[str]]:
    factories = {}
    missing = []
    for key, (module, name) in MODULES.items():
        try:
            factories[key] = getattr(importlib.import_module(module), name)
        except ModuleNotFoundError as error:
            if error.name != module:
                raise
            missing.append(module)
    return factories, missing


class Session:
    def __init__(self, hub: Hub, factories: dict | None = None, runs_dir: Path | None = None):
        self.hub = hub
        self.factories, self.missing = load_factories() if factories is None else (factories, [])
        self.runs_dir = runs_dir or ROOT / "runs"
        self.settings = SessionSettings()
        self.code = secrets.token_hex(2).upper()
        self.bus = None
        self.world = None
        self.loop_task = None
        self.operations: set[asyncio.Task] = set()
        self.controller = None
        self.report = None
        self.lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return not self.missing

    def message(self, base_url: str) -> SessionMsg:
        return SessionMsg(
            code=self.code, settings=self.settings, clients=self.hub.counts(),
            join_url=f"{base_url}/viz?session={self.code}",
        )

    def state(self) -> StateMsg:
        if self.world is None:
            return StateMsg(t=0, paused=True, sim_speed=1, runway=self.settings.runway,
                            aircraft=[], alerts=[])
        return self.world.public_state().model_copy(update={
            "paused": self.settings.paused, "sim_speed": self.settings.sim_speed,
        })

    def emit(self, kind: str) -> None:
        if self.bus:
            self.bus.emit(Event(t=self.bus.now(), type=kind, actor="SYSTEM"))

    def on_sim_event(self, event: Event) -> None:
        response = self.pilot.on_sim_event(event)
        if response:
            self.start_operation(self.voice.speak(response))

    def start_operation(self, coroutine) -> None:
        task = self.hub.spawn(coroutine)
        self.operations.add(task)
        task.add_done_callback(self.operations.discard)

    async def start(self) -> None:
        if not self.ready:
            return
        self.bus = EventLog(self.runs_dir / secrets.token_hex(8) / "events.jsonl")
        f = self.factories
        self.world = f["world"](self.bus, ROOT / "data")
        self.bus.clock = lambda: self.world.t
        self.pilot = f["pilot"](self.world, self.bus, ROOT / "data")
        self.rules = f["rules"](self.world, self.bus, ROOT / "data")
        self.composer = f["composer"](ROOT / "data")
        self.voice = f["voice"](self.world, self.pilot, self.composer, self.bus, self.hub)
        self.narrative = f["narrative"]()
        self.bus.subscribe(self.rules.on_event)
        self.bus.subscribe(self.hub.on_event)
        self.bus.subscribe(self.on_sim_event)
        self.world.load_scenario(self.settings.scenario, self.settings.seed)
        self.world.configure(self.settings)
        self.pilot.set_error_rate(self.settings.error_rate)
        self.emit("session_start")
        self.loop_task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        tasks = list(self.operations)
        if self.loop_task:
            tasks.append(self.loop_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.operations.clear()
        self.controller = None
        if self.bus:
            self.bus.close()
            self.bus = None

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1 / self.settings.sim_speed)
            if not self.settings.paused:
                self.world.tick(1.0)
                self.pilot.tick(self.world.t)
                self.rules.on_tick(self.world.snapshot())
                await self.hub.send(self.state())

    async def notice(self, text: str) -> None:
        await self.hub.send(CaptionMsg(
            tx_id=secrets.token_hex(6), speaker="SYSTEM", stage="final", text=text,
        ))

    async def handle_control(self, message: ControlMsg) -> None:
        async with self.lock:
            if not self.ready:
                raise ValueError("Waiting for simulator and voice modules")
            action, params = message.action, message.params
            if action in ("new_session", "load_scenario"):
                settings = SessionSettings.model_validate({
                    **self.settings.model_dump(), **params,
                })
                self.validate_settings(settings)
                await self.stop()
                self.settings = settings
                self.report = None
                await self.start()
            elif action in ("pause", "resume", "set_speed", "set_traffic",
                            "set_runway", "set_error_rate"):
                changes = dict(params)
                if action in ("pause", "resume"):
                    changes = {"paused": action == "pause"}
                settings = SessionSettings.model_validate({
                    **self.settings.model_dump(), **changes,
                })
                self.validate_settings(settings)
                self.world.configure(settings)
                self.pilot.set_error_rate(settings.error_rate)
                self.settings = settings
                self.emit({"pause": "session_pause", "resume": "session_resume"}.get(
                    action, "settings_changed"))
            elif action == "inject":
                self.world.inject(**params)
            elif action == "force_error":
                self.pilot.force_error(**params)
            elif action == "reveal_errors":
                self.voice.set_reveal_errors(bool(params.get("on", False)))
            elif action == "end_session":
                self.settings.paused = True
                self.emit("session_end")
                report = self.rules.report(self.code)
                try:
                    report = await asyncio.wait_for(self.narrative.write(report), 5)
                except Exception:
                    logger.exception("Narrative unavailable; keeping deterministic report")
                self.report = ReportMsg(report=report)
                await self.hub.send(self.report)
            else:
                raise ValueError("Presenter beats are scheduled for a later task")
            await self.hub.send(self.state())

    @staticmethod
    def validate_settings(settings: SessionSettings) -> None:
        if settings.sim_speed not in (1, 2):
            raise ValueError("Simulation speed must be 1 or 2")
        if not (0 <= settings.arr_per_hr <= 60 and 0 <= settings.dep_per_hr <= 60):
            raise ValueError("Traffic rates must be between 0 and 60")
