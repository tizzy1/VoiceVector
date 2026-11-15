"""Session lifecycle and fixed-contract integration wiring."""
import asyncio
import importlib
import logging
import secrets
from collections import Counter
from pathlib import Path

from server.contracts import (
    CaptionMsg, ControlMsg, Event, ReportMsg, SessionMsg, SessionSettings, StateMsg, LiveImportStatus,
)
from server.hub import Hub
from tools.traffic_snapshot import SnapshotFetcher, SnapshotError, demo_snapshot

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
        self.factories, _ = load_factories() if factories is None else (factories, [])
        self.missing = [module for key, (module, _) in MODULES.items()
                        if key not in self.factories]
        self.runs_dir = runs_dir or ROOT / "runs"
        self.settings = SessionSettings()
        self.code = secrets.token_hex(2).upper()
        self.bus = None
        self.world = None
        self.pilot = None
        self.rules = None
        self.voice = None
        self.narrative = None
        self.loop_task = None
        self.operations: set[asyncio.Task] = set()
        self.controller = None
        self.report = None
        self.lock = asyncio.Lock()
        self.snapshot_fetcher = SnapshotFetcher()
        self.live_import = None
        self.import_provenance = {}
        self.importing = False

    @property
    def ready(self) -> bool:
        return self.voice is not None

    @property
    def simulation_ready(self) -> bool:
        return self.world is not None

    def message(self, base_url: str) -> SessionMsg:
        return SessionMsg(
            code=self.code, settings=self.settings, clients=self.hub.counts(),
            join_url=f"{base_url}/viz?session={self.code}",
        )

    def state(self) -> StateMsg:
        if self.world is None:
            return StateMsg(t=0, paused=True, sim_speed=1, runway=self.settings.runway,
                            aircraft=[], alerts=[])
        state = self.world.public_state()
        self.import_provenance = {ac.callsign: self.import_provenance[ac.callsign]
                                  for ac in state.aircraft if ac.callsign in self.import_provenance}
        return state.model_copy(update={
            "paused": self.settings.paused, "sim_speed": self.settings.sim_speed,
            "live_import_available": callable(getattr(self.world, "import_live", None)),
            "live_import": self.live_import,
            "aircraft": [ac.model_copy(update=self.import_provenance.get(ac.callsign, {})) for ac in state.aircraft],
        })

    def emit(self, kind: str) -> None:
        if self.bus:
            self.bus.emit(Event(t=self.bus.now(), type=kind, actor="SYSTEM"))

    def on_sim_event(self, event: Event) -> None:
        if not self.ready:
            return
        response = self.pilot.on_sim_event(event)
        if response:
            self.start_operation(self.voice.speak(response))

    def start_operation(self, coroutine) -> None:
        task = self.hub.spawn(coroutine)
        self.operations.add(task)
        task.add_done_callback(self.operations.discard)

    async def start(self) -> None:
        if "world" not in self.factories:
            return
        self.bus = EventLog(self.runs_dir / secrets.token_hex(8) / "events.jsonl")
        f = self.factories
        self.world = f["world"](self.bus, ROOT / "data")
        self.bus.clock = lambda: self.world.t
        if "pilot" in f:
            self.pilot = f["pilot"](self.world, self.bus, ROOT / "data")
        if "rules" in f:
            self.rules = f["rules"](self.world, self.bus, ROOT / "data")
            self.bus.subscribe(self.rules.on_event)
        if self.pilot is not None and "composer" in f and "voice" in f:
            self.composer = f["composer"](ROOT / "data")
            self.voice = f["voice"](self.world, self.pilot, self.composer, self.bus, self.hub)
        if "narrative" in f:
            self.narrative = f["narrative"]()
        self.bus.subscribe(self.hub.on_event)
        self.bus.subscribe(self.on_sim_event)
        self.world.configure(self.settings)
        self.world.load_scenario(self.settings.scenario, self.settings.seed)
        if hasattr(self.world, "strip_autofill"):
            self.world.strip_autofill = self.settings.strip_autofill
        if self.pilot is not None:
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
        self.world = self.pilot = self.rules = self.voice = self.narrative = None
        self.live_import = None
        self.import_provenance = {}
        self.importing = False
        self.loop_task = None

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1 / self.settings.sim_speed)
            if not self.settings.paused:
                self.world.tick(1.0)
                if self.pilot is not None:
                    self.pilot.tick(self.world.t)
                if self.rules is not None:
                    self.rules.on_tick(self.world.snapshot())
                await self.hub.send(self.state())

    async def notice(self, text: str) -> None:
        await self.hub.send(CaptionMsg(
            tx_id=secrets.token_hex(6), speaker="SYSTEM", stage="final", text=text,
        ))

    async def handle_control(self, message: ControlMsg) -> None:
        if message.action == "import_live":
            await self.import_snapshot(message.params)
            return
        if message.action == "beat":
            key = message.params.get("key")
            beats = {
                1: ("load_scenario", {"scenario": "demo", "paused": False}),
                4: ("force_error", {"mode": "consistent", "kind": "wrong_value_adjacent"}),
                8: ("end_session", {}),
            }
            if type(key) is not int or key not in beats:
                raise ValueError("Available presenter beats: 1, 4, 8")
            action, params = beats[key]
            message = ControlMsg(action=action, params=params)
        async with self.lock:
            if not self.simulation_ready:
                raise ValueError("Waiting for simulator module")
            action, params = message.action, message.params
            if action == "edit_strip":
                edit = getattr(self.world, "edit_strip", None)
                if not callable(edit):
                    raise ValueError("Strip editing is unavailable in this simulator")
                if (set(params) != {"callsign", "field", "value"}
                        or not isinstance(params["callsign"], str)
                        or not isinstance(params["field"], str)):
                    raise ValueError("Strip edit requires callsign, field and value")
                if not edit(params["callsign"], params["field"], params["value"]):
                    raise ValueError("Strip edit rejected: check the aircraft, field and value")
            elif action == "set_strip_autofill":
                if set(params) != {"on"} or type(params["on"]) is not bool:
                    raise ValueError("Strip auto-fill requires a boolean on value")
                if not hasattr(self.world, "strip_autofill"):
                    raise ValueError("Strip auto-fill is unavailable in this simulator")
                self.world.strip_autofill = params["on"]
                self.settings.strip_autofill = params["on"]
                self.emit("settings_changed")
            elif action in ("new_session", "load_scenario"):
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
                if self.pilot is not None:
                    self.pilot.set_error_rate(settings.error_rate)
                self.settings = settings
                if action == "resume":
                    self.report = None
                self.emit({"pause": "session_pause", "resume": "session_resume"}.get(
                    action, "settings_changed"))
            elif action == "inject":
                self.world.inject(**params)
            elif action == "force_error":
                if self.pilot is None:
                    raise ValueError("Waiting for pilot module")
                self.pilot.force_error(**params)
                await self.notice("Pilot error armed for the next eligible clearance.")
            elif action == "reveal_errors":
                if not self.ready:
                    raise ValueError("Waiting for radio pipeline")
                self.voice.set_reveal_errors(bool(params.get("on", False)))
            elif action == "end_session":
                if self.rules is None:
                    raise ValueError("Waiting for rules module")
                if self.report is not None:
                    await self.hub.send(self.report)
                    return
                self.settings.paused = True
                self.emit("session_end")
                report = self.rules.report(self.code)
                if self.narrative is not None:
                    try:
                        report = await asyncio.wait_for(self.narrative.write(report), 5)
                    except Exception:
                        logger.exception("Narrative unavailable; keeping deterministic report")
                self.report = ReportMsg(report=report)
                await self.hub.send(self.report)
            else:
                raise ValueError("Unsupported session control")
            await self.hub.send(self.state())

    @staticmethod
    def validate_settings(settings: SessionSettings) -> None:
        if settings.sim_speed not in (1, 2):
            raise ValueError("Simulation speed must be 1 or 2")
        if not (0 <= settings.arr_per_hr <= 60 and 0 <= settings.dep_per_hr <= 60):
            raise ValueError("Traffic rates must be between 0 and 60")

    async def import_snapshot(self, params: dict) -> None:
        source = params.get("source", "demo")
        if source not in ("demo", "vatsim") or set(params) - {"source"}:
            await self.notice("Choose a demo or VATSIM snapshot; custom URLs and raw payloads are not accepted.")
            return
        if self.importing:
            await self.notice("A snapshot import is already in progress.")
            return
        world = self.world
        if world is None or not callable(getattr(world, "import_live", None)):
            await self.notice("Snapshot import is unavailable in this simulator.")
            return
        self.importing = True
        attribution = ("Synthetic demo snapshot · no live traffic" if source == "demo" else
                       "Traffic snapshot: VATSIM network data (data.vatsim.net), read-only")
        self.live_import = LiveImportStatus(source=source, phase="fetching", attribution=attribution, sim_t=world.t)
        try:
            await self.hub.send(self.state())
            snapshot = demo_snapshot(world) if source == "demo" else await self.snapshot_fetcher.fetch()
            async with self.lock:
                if world is not self.world:
                    return
                result = world.import_live(snapshot)
                result["attribution"] = attribution
                skipped = result.get("skipped", [])
                result["counts"] = dict(imported=len(result.get("imported", [])), skipped=len(skipped),
                                        skipped_by_reason=dict(Counter(item["reason"] for item in skipped)))
                result["skipped"] = skipped[:20]
                self.live_import = LiveImportStatus.model_validate(result)
                for imported in self.live_import.imported:
                    self.import_provenance[imported.callsign] = dict(traffic_source=source,
                        source_time=self.live_import.source_time, fetched_at=self.live_import.fetched_at,
                        source_attribution=attribution)
                await self.hub.send(self.state())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = str(error) if isinstance(error, SnapshotError) else "Snapshot could not be imported. Try the demo snapshot."
            self.live_import = LiveImportStatus(source=source, attribution=attribution, error=message,
                                               sim_t=world.t)
            await self.hub.send(self.state())
        finally:
            self.importing = False
