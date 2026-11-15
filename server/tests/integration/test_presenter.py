import pytest
from fastapi.testclient import TestClient

from server.contracts import Instruction, Transmission
from server.main import create_app
from server.tests.integration.test_session_controls import FACTORIES, receive_until


def beat(viewer, key):
    viewer.send_json({"type": "control", "action": "beat", "params": {"key": key}})


def test_reset_beat_restores_seeded_demo_for_both_views(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            initial = receive_until(radar, lambda m: m["type"] == "state")
            with client.websocket_connect("/ws?role=viz") as viz:
                receive_until(viz, lambda m: m["type"] == "state")
                radar.send_json({"type": "control", "action": "inject",
                                 "params": {"kind": "mayday", "callsign": "BAW117"}})
                for viewer in (radar, viz):
                    receive_until(viewer, lambda m: m["type"] == "state"
                                  and any(a["emergency"] for a in m["aircraft"]))
                beat(viz, 1)
                for viewer in (radar, viz):
                    reset = receive_until(viewer, lambda m: m["type"] == "state"
                                          and m["t"] == 0 and not any(a["emergency"] for a in m["aircraft"]))
                    assert not reset["paused"]
                    assert [(a["callsign"], a["x"], a["y"]) for a in reset["aircraft"]] == [
                        (a["callsign"], a["x"], a["y"]) for a in initial["aircraft"]]


def test_error_beat_arms_real_pilot_for_next_numeric_clearance(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            beat(radar, 4)
            receive_until(radar, lambda m: m["type"] == "caption" and "error armed" in m["text"])

            def respond():
                session = client.app.state.session
                tx = Transmission(tx_id="presenter", t=session.world.t, receiver="BAW117",
                                  receiver_confidence=1, source="typed", instructions=[
                                      Instruction(type="heading", value=240, turn_dir="L")])
                return session.pilot.handle_transmission(tx, session.world.evaluate(tx))

            replies = client.portal.call(respond)
            errors = [error for reply in replies for error in reply.errors]
            assert len(errors) == 1
            assert errors[0].kind == "wrong_value_adjacent"
            assert errors[0].mode == "consistent"
            assert errors[0].readback_value != errors[0].intended


def test_end_beat_publishes_report_and_pauses_both_views(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            with client.websocket_connect("/ws?role=viz") as viz:
                beat(radar, 8)
                reports = [receive_until(viewer, lambda m: m["type"] == "report")["report"]
                           for viewer in (radar, viz)]
                assert reports[0] == reports[1]
                assert reports[0]["session_id"] == client.app.state.session.code
                assert client.app.state.session.settings.paused
                beat(radar, 8)
                assert receive_until(radar, lambda m: m["type"] == "report")["report"] == reports[0]
                radar.send_json({"type": "control", "action": "resume", "params": {}})
                receive_until(radar, lambda m: m["type"] == "state" and not m["paused"])
                assert client.app.state.session.report is None
                beat(radar, 8)
                receive_until(radar, lambda m: m["type"] == "report")
                assert client.app.state.session.settings.paused


def test_report_page_reconnection_receives_current_report_and_reset_clears_it(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            beat(radar, 8)
            report = receive_until(radar, lambda m: m["type"] == "report")["report"]
            code = client.app.state.session.code
            with client.websocket_connect(f"/ws?role=viz&session={code}") as page:
                assert receive_until(page, lambda m: m["type"] == "report")["report"] == report
            beat(radar, 1)
            receive_until(radar, lambda m: m["type"] == "event" and m["event"]["type"] == "session_start")
            assert client.app.state.session.report is None


@pytest.mark.parametrize("key", [3, "1", True, None])
def test_unknown_or_mistyped_beats_do_not_change_session(tmp_path, key):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            world = client.app.state.session.world
            beat(radar, key)
            receive_until(radar, lambda m: m["type"] == "caption" and "Available presenter beats" in m["text"])
            assert client.app.state.session.world is world
            assert client.app.state.session.report is None
