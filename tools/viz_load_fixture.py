"""Isolated forty-aircraft rendering fixture, never a real-user session.

Run: .venv/bin/python tools/viz_load_fixture.py --port 8002
Open /viz and inspect .viz-scene[data-render-fps] after initialization.
This reports rendered-frame frequency, not a formal cross-device GPU benchmark.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from server.main import create_app
from server.sim.world import World


class LoadWorld(World):
    def load_scenario(self, name, seed):
        super().load_scenario(name, seed)
        base = next(iter(self.aircraft.values())).model_copy(deep=True)
        self.aircraft.clear()
        self.alerts.clear()
        for i in range(40):
            altitude = 4000 + (i % 8) * 1000
            ac = base.model_copy(deep=True, update={
                "callsign": f"TST{i + 1:02d}", "spoken": f"test {i + 1}", "spoken_plain": f"Test aircraft {i + 1}",
                "x": (i % 8 - 3.5) * 4, "y": (i // 8 - 2) * 5, "alt": altitude,
                "cleared_alt": altitude, "hdg": 90, "track": 90, "tgt_hdg": 90, "phase": "VECTORS", "route": [],
            })
            self.add_aircraft(ac, checkin=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    uvicorn.run(create_app({"world": LoadWorld}), host="127.0.0.1", port=args.port)
