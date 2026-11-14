"""Platform-side snapshot fetching. No network login, writes, or raw-feed persistence."""
import asyncio
from datetime import datetime, timezone
import json
import math
import time

import httpx

from server.sim.live_import import normalize_vatsim

VATSIM_URL = "https://data.vatsim.net/v3/vatsim-data.json"
MAX_BYTES = 16 * 1024 * 1024


class SnapshotError(ValueError):
    pass


class SnapshotFetcher:
    def __init__(self):
        self.last_request = float("-inf")

    async def fetch(self) -> dict:
        if time.monotonic() - self.last_request < 15:
            raise SnapshotError("Wait 15 seconds between live-feed requests.")
        self.last_request = time.monotonic()
        try:
            async with asyncio.timeout(10):
                async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                    async with client.stream("GET", VATSIM_URL, headers={
                        "User-Agent": "SweatboxAI/1.0 (non-commercial flight simulation)",
                    }) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_BYTES:
                                raise SnapshotError("Traffic feed exceeds the supported size.")
            raw = json.loads(body)
            if not isinstance(raw, dict) or not isinstance(raw.get("pilots"), list):
                raise SnapshotError("Traffic feed has an unsupported format.")
            snapshot = normalize_vatsim(raw)
            observed = datetime.fromisoformat(str(snapshot["source_time"]).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("missing timezone")
            if (observed - datetime.now(timezone.utc)).total_seconds() > 60:
                raise ValueError("future snapshot")
            # Reject malformed coordinates before they can enter the simulator.
            valid = []
            for target in snapshot["targets"]:
                finite = all(math.isfinite(target[key]) for key in ("lat", "lon", "alt_ft", "gs_kt", "hdg_mag"))
                if finite and -90 <= target["lat"] <= 90 and -180 <= target["lon"] <= 180:
                    valid.append(target)
                else:
                    snapshot["rejected"].append({"callsign": target["callsign"], "reason": "invalid_record"})
            snapshot["targets"] = valid
            return snapshot
        except SnapshotError:
            raise
        except (TimeoutError, httpx.TimeoutException) as error:
            raise SnapshotError("Traffic feed timed out. Existing simulated traffic is unchanged.") from error
        except httpx.HTTPError as error:
            raise SnapshotError("Traffic feed is unavailable. Try the demo snapshot.") from error
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise SnapshotError("Traffic feed has invalid data or no valid observation time.") from error


def demo_snapshot(world) -> dict:
    """Synthetic local fixture, never a recording or claim of real traffic."""
    stamp = datetime.now(timezone.utc).isoformat()
    x, y = world.fix_xy("WILLO")
    targets = []
    for i, callsign in enumerate(("SBX901", "SBX902")):
        lat, lon = world.proj.to_latlon(x + i * 6, y + i * 2)
        targets.append(dict(callsign=callsign, lat=lat, lon=lon, alt_ft=7000 + i * 2000,
                            gs_kt=220, hdg_mag=240, actype="A320", dep="EGLL", arr="EGKK",
                            route="WILLO", squawk="2000", flight_rules="I", last_updated=stamp))
    return dict(source="demo", source_time=stamp, fetched_at=stamp, targets=targets, rejected=[])
