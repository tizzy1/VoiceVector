import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from server.tests.integration.test_session_controls import FACTORIES, receive_until
from tools.traffic_snapshot import SnapshotError, SnapshotFetcher, demo_snapshot


def request(viewer, source="demo", **extra):
    viewer.send_json({"type": "control", "action": "import_live", "params": {"source": source, **extra}})


def completed(viewer):
    return receive_until(viewer, lambda m: m["type"] == "state" and m.get("live_import")
                         and m["live_import"]["phase"] == "complete")


def test_demo_imports_simulated_aircraft_with_provenance_to_both_views(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            with client.websocket_connect("/ws?role=viz") as viz:
                request(radar)
                for viewer in (radar, viz):
                    state = completed(viewer)
                    assert state["live_import"]["counts"]["imported"] == 2
                    assert state["live_import"]["ok"]
                    imported = [a for a in state["aircraft"] if a["traffic_source"]]
                    assert {a["callsign"] for a in imported} == {"SBX901", "SBX902"}
                    assert all(a["traffic_source"] == "demo" and a["source_time"] and a["fetched_at"] for a in imported)
                    assert "Synthetic" in imported[0]["source_attribution"]
                with client.websocket_connect("/ws?role=viz") as reconnected:
                    state = receive_until(reconnected, lambda m: m["type"] == "state")
                    assert state["live_import"]["counts"]["imported"] == 2
                request(radar)
                state = completed(radar)
                assert state["live_import"]["counts"]["imported"] == 0
                assert state["live_import"]["counts"]["skipped_by_reason"] == {"callsign_in_use": 2}
                assert len([a for a in state["aircraft"] if a["callsign"] == "SBX901"]) == 1


def test_fetch_does_not_block_controller_messages_and_reset_cancels_it(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        gate = client.portal.call(asyncio.Event)

        async def fetch():
            await gate.wait()
            raise AssertionError("reset must cancel the fetch")

        client.app.state.session.snapshot_fetcher = SimpleNamespace(fetch=fetch)
        with client.websocket_connect("/ws?role=radar") as radar:
            request(radar, "vatsim")
            receive_until(radar, lambda m: m["type"] == "state" and m.get("live_import")
                          and m["live_import"]["phase"] == "fetching")
            radar.send_json({"type": "control", "action": "pause", "params": {}})
            receive_until(radar, lambda m: m["type"] == "state" and m["paused"])
            radar.send_json({"type": "control", "action": "beat", "params": {"key": 1}})
            state = receive_until(radar, lambda m: m["type"] == "state" and not m["paused"]
                                  and m["live_import"] is None)
            assert not any(a["traffic_source"] for a in state["aircraft"])


def test_provider_failure_preserves_existing_aircraft_and_publishes_error(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        async def fetch():
            raise SnapshotError("Traffic feed timed out.")

        client.app.state.session.snapshot_fetcher = SimpleNamespace(fetch=fetch)
        with client.websocket_connect("/ws?role=radar") as radar:
            initial = receive_until(radar, lambda m: m["type"] == "state")
            request(radar, "vatsim")
            state = completed(radar)
            assert not state["live_import"]["ok"]
            assert "timed out" in state["live_import"]["error"]
            assert {a["callsign"] for a in state["aircraft"]} == {a["callsign"] for a in initial["aircraft"]}


def test_skipped_details_are_bounded_but_counts_are_complete(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        snapshot = demo_snapshot(client.app.state.session.world)
        snapshot["source"] = "vatsim"
        snapshot["targets"] += [{**snapshot["targets"][0], "callsign": f"TST{i}", "lat": 0, "lon": 0}
                                for i in range(50)]
        async def fetch():
            return snapshot

        client.app.state.session.snapshot_fetcher = SimpleNamespace(fetch=fetch)
        with client.websocket_connect("/ws?role=radar") as radar:
            request(radar, "vatsim")
            status = completed(radar)["live_import"]
            assert status["counts"]["imported"] == 2
            assert status["counts"]["skipped"] == 50
            assert len(status["skipped"]) == 20


def test_raw_payloads_and_arbitrary_urls_are_rejected(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            request(radar, url="http://localhost/private")
            receive_until(radar, lambda m: m["type"] == "caption" and "custom URLs" in m["text"])
            assert client.app.state.session.live_import is None


@pytest.mark.asyncio
async def test_fetch_normalizes_drops_personal_fields_and_rate_limits(monkeypatch):
    stamp = datetime.now(timezone.utc).isoformat()
    raw = {"general": {"update_timestamp": stamp}, "pilots": [{
        "callsign": "BAW123", "cid": 999999, "name": "PRIVATE PILOT", "latitude": 51.1,
        "longitude": -0.2, "altitude": 7000, "groundspeed": 220, "heading": 240,
        "flight_plan": {"departure": "EGLL", "arrival": "EGKK", "aircraft_short": "A320"},
    }]}
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw)), **kwargs))
    fetcher = SnapshotFetcher()
    snapshot = await fetcher.fetch()
    assert len(snapshot["targets"]) == 1
    assert "PRIVATE PILOT" not in json.dumps(snapshot)
    assert "cid" not in snapshot["targets"][0]
    with pytest.raises(SnapshotError, match="15 seconds"):
        await fetcher.fetch()


@pytest.mark.asyncio
async def test_bad_provider_timestamp_and_coordinates_do_not_enter_simulator(monkeypatch):
    original = httpx.AsyncClient
    raw = {"general": {}, "pilots": []}
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw)), **kwargs))
    with pytest.raises(SnapshotError, match="observation time"):
        await SnapshotFetcher().fetch()
    raw["general"]["update_timestamp"] = datetime.now(timezone.utc).isoformat()
    raw["pilots"] = [{"callsign": "BAW123", "latitude": 999, "longitude": 0, "altitude": 7000,
                      "groundspeed": 200, "heading": 240}]
    snapshot = await SnapshotFetcher().fetch()
    assert not snapshot["targets"]
    assert snapshot["rejected"][0]["reason"] == "invalid_record"
