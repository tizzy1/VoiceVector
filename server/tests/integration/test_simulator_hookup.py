"""Real Tudor simulator joined to Bhavin's server without voice replacements."""
import time

from fastapi.testclient import TestClient

from server.contracts import StateMsg
from server.main import create_app
from server.pilot.engine import PilotEngine
from server.rules.engine import RulesEngine
from server.sim.world import World
from server.tests.integration.stubs import FACTORIES

SIM_FACTORIES = {"world": World, "pilot": PilotEngine, "rules": RulesEngine}


def receive_until(viewer, predicate):
    for _ in range(40):
        message = viewer.receive_json()
        if predicate(message):
            return message
    raise AssertionError("Expected message did not arrive")


def positions(state):
    return {a["callsign"]: (a["x"], a["y"]) for a in state["aircraft"]}


def test_real_simulator_streams_pauses_reconnects_and_resets(tmp_path):
    with TestClient(create_app(SIM_FACTORIES, tmp_path)) as client:
        health = client.get("/api/health").json()
        assert health["simulation_ready"] and not health["ready"]
        assert "server.voice.pipeline" in health["missing_modules"]
        with client.websocket_connect("/ws?role=radar") as radar:
            initial = receive_until(radar, lambda m: m["type"] == "state")
            StateMsg.model_validate(initial)
            assert len(initial["aircraft"]) == 5
            with client.websocket_connect("/ws?role=viz") as viz:
                receive_until(viz, lambda m: m["type"] == "state")
                moved = receive_until(
                    radar, lambda m: m["type"] == "state" and m["t"] > initial["t"],
                )
                matched = receive_until(
                    viz, lambda m: m["type"] == "state" and m["t"] == moved["t"],
                )
                assert positions(moved) == positions(matched)
                assert positions(moved) != positions(initial)
                radar.send_json({"type": "control", "action": "pause", "params": {}})
                paused = receive_until(
                    radar, lambda m: m["type"] == "state" and m["paused"],
                )
                time.sleep(1.1)
                with client.websocket_connect("/ws?role=viz") as reconnected:
                    snapshot = receive_until(reconnected, lambda m: m["type"] == "state")
                    assert snapshot["t"] == paused["t"]
                    assert positions(snapshot) == positions(paused)
                radar.send_json({"type": "control", "action": "resume", "params": {}})
                resumed = receive_until(
                    radar, lambda m: m["type"] == "state" and m["t"] > paused["t"],
                )
                assert not resumed["paused"]
                assert positions(resumed) != positions(paused)
                radar.send_json({
                    "type": "control", "action": "load_scenario",
                    "params": {"scenario": "demo", "paused": True},
                })
                reset = receive_until(
                    radar, lambda m: m["type"] == "state" and m["t"] == 0,
                )
                assert reset["paused"]
                assert len(reset["aircraft"]) == 5
                assert positions(reset) == positions(initial)


def test_radio_unavailable_does_not_apply_typed_clearance(tmp_path):
    with TestClient(create_app(SIM_FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            receive_until(radar, lambda m: m["type"] == "state")
            radar.send_json({"type": "typed", "text": "BAW117 turn left heading 240"})
            receive_until(
                radar, lambda m: m["type"] == "caption" and "Radio unavailable" in m["text"],
            )
            assert client.app.state.session.world.snapshot().aircraft["BAW117"]["tgt_hdg"] != 240


def test_narrative_is_optional_for_radio_readiness(tmp_path):
    factories = {key: value for key, value in FACTORIES.items() if key != "narrative"}
    with TestClient(create_app(factories, tmp_path)) as client:
        health = client.get("/api/health").json()
        assert health["ready"] and health["simulation_ready"]
        assert health["missing_modules"] == ["server.agents.narrative"]


def test_missing_simulator_keeps_platform_available(tmp_path):
    with TestClient(create_app({}, tmp_path)) as client:
        health = client.get("/api/health").json()
        assert not health["ready"] and not health["simulation_ready"]
        assert client.get("/api/session").status_code == 200
        assert client.get("/api/data/egkk").status_code == 200


def test_report_without_narrative_uses_real_rules_report(tmp_path):
    with TestClient(create_app(SIM_FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            receive_until(radar, lambda m: m["type"] == "state")
            radar.send_json({"type": "control", "action": "end_session", "params": {}})
            report = receive_until(radar, lambda m: m["type"] == "report")["report"]
            assert report["narrative"] is None
            assert client.app.state.session.settings.paused
