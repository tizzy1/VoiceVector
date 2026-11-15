"""Real TCP/WebSocket check using test doubles until team modules arrive.

Run from the repository root: python server/tests/integration/smoke_test.py
Add --real to require and exercise the actual team modules.
"""
import argparse
import asyncio
import json
import math
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import httpx
from websockets.asyncio.client import connect


async def check(port: int, timeout_s: float = 5) -> None:
    async with connect(f"ws://127.0.0.1:{port}/ws?role=radar") as radar:
        while json.loads(await radar.recv())["type"] != "state":
            pass
        async with connect(f"ws://127.0.0.1:{port}/ws?role=viz") as viz:
            while json.loads(await viz.recv())["type"] != "state":
                pass
            started = time.monotonic()
            await radar.send(json.dumps({
                "type": "typed", "text": "BAW117 turn left heading 240",
            }))
            async def observe(viewer):
                caption = False
                changed = False
                async with asyncio.timeout(timeout_s):
                    while not (caption and changed):
                        message = json.loads(await viewer.recv())
                        caption |= (
                            message["type"] == "caption"
                            and message["speaker"] == "BAW117"
                        )
                        if message["type"] == "state":
                            changed |= any(
                                a["callsign"] == "BAW117"
                                and (a["tgt_hdg"] == 240 or a["hdg"] == 240)
                                for a in message["aircraft"]
                            )
                return time.monotonic() - started
            elapsed = await asyncio.gather(observe(radar), observe(viz))
            print(f"Readback and heading target received after {max(elapsed):.2f} s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--timeout", type=float, help="Clearance timeout in seconds "
                        "(default: 15 with --real, otherwise 5)")
    args = parser.parse_args()
    timeout_s = args.timeout if args.timeout is not None else (15 if args.real else 5)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        parser.error("--timeout must be a finite positive number")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="voicevector-smoke-") as directory:
        if args.real:
            setup = "from server.main import app"
        else:
            setup = (
                "from pathlib import Path; from server.main import create_app; "
                "from server.tests.integration.stubs import FACTORIES; "
                f"app=create_app(FACTORIES, Path({directory!r}))"
            )
        source = setup + f"; import uvicorn; uvicorn.run(app, host='127.0.0.1', port={port})"
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-c", source], cwd=ROOT, stdout=log, stderr=log,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        health = httpx.get(f"http://127.0.0.1:{port}/api/health").json()
                        break
                    except httpx.TransportError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        time.sleep(.1)
                if not health["ready"]:
                    raise RuntimeError(f"Missing team modules: {health['missing_modules']}")
                asyncio.run(check(port, timeout_s))
                print("PASS: radar and viz receive readback and heading target"
                      + (" (real team modules)" if args.real else " (test doubles)"))
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
