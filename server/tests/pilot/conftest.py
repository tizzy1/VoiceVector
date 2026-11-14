"""Shared fixtures for Tudor's tests (sim / pilot / rules). Keep the three copies identical."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.contracts import Event  # noqa: E402
from server.sim.world import World  # noqa: E402

DATA_DIR = ROOT / "data"


class FakeBus:
    """Minimal EventBus: records events and fans out synchronously."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.subs = []
        self.clock = lambda: 0.0

    def emit(self, e: Event) -> None:
        self.events.append(e)
        for cb in self.subs:
            cb(e)

    def subscribe(self, cb) -> None:
        self.subs.append(cb)

    def now(self) -> float:
        return self.clock()

    def of(self, type_: str) -> list[Event]:
        return [e for e in self.events if e.type == type_]


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def world(bus: FakeBus) -> World:
    w = World(bus, DATA_DIR)
    bus.clock = lambda: w.t
    return w
