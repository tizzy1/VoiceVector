"""Real TCP/WebSocket check using test doubles until team modules arrive.

Run from the repository root: python server/tests/integration/smoke_test.py
Add --real to require and exercise the actual team modules.
"""
import argparse
import asyncio
import json
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


async def check(port: int) -> None:
    async with connect(f"ws://127.0.0.1:{port}/ws?role=radar") as radar:
        while json.loads(await radar.recv())["type"] != "state":
            pass
        async with connect(f"ws://127.0.0.1:{port}/ws?role=viz") as viz:
            while json.loads(await viz.recv())["type"] != "state":
                pass
            await radar.send(json.dumps({
                "type": "typed", "text": "BAW117 turn left heading 240",
            }))
            async def observe(viewer):
                caption = False
                changed = False
                async with asyncio.timeout(5):
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
            await asyncio.gather(observe(radar), observe(viz))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="sweatbox-smoke-") as directory:
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
                asyncio.run(check(port))
                print("PASS: radar and viz receive readback and heading update"
                      + (" (real team modules)" if args.real else " (test doubles)"))
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
