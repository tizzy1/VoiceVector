import json

from fastapi.testclient import TestClient

from server.contracts import CaptionMsg, SessionMsg, StateMsg, Event
from server.hub import Client
from server.main import create_app
from server.session import EventLog
from server.tests.integration.stubs import FACTORIES


def test_typed_clearance_reaches_both_viewers_and_changes_state(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        assert client.get("/api/health").json()["ready"]
        with client.websocket_connect("/ws?role=radar") as radar:
            SessionMsg.model_validate(radar.receive_json())
            initial = StateMsg.model_validate(radar.receive_json())
            assert initial.aircraft[0].hdg == 75
            with client.websocket_connect("/ws?role=viz") as viz:
                SessionMsg.model_validate(viz.receive_json())
                StateMsg.model_validate(viz.receive_json())
                radar.send_json({"type": "typed", "text": "BAW117 turn left heading 240"})
                for viewer in (radar, viz):
                    caption = None
                    changed = False
                    for _ in range(12):
                        message = viewer.receive_json()
                        if message["type"] == "caption":
                            caption = CaptionMsg.model_validate(message)
                        if message["type"] == "state":
                            state = StateMsg.model_validate(message)
                            changed = state.aircraft[0].hdg == 240
                        if caption and changed:
                            break
                    assert caption.speaker == "BAW117"
                    assert changed


def test_event_log_survives_failed_subscriber(tmp_path):
    bus = EventLog(tmp_path / "events.jsonl")
    captured = []
    def broken(event):
        raise RuntimeError("subscriber failure")
    bus.subscribe(broken)
    bus.subscribe(captured.append)
    event = Event(t=2, type="session_start", actor="SYSTEM")
    bus.emit(event)
    bus.close()
    assert captured == [event]
    assert json.loads((tmp_path / "events.jsonl").read_text())["t"] == 2


def test_output_queue_replaces_stale_states_only():
    client = Client(None, "viz")
    for message in (
        {"type": "state", "t": 1}, {"type": "caption", "text": "keep"},
        {"type": "audio_chunk", "seq": 0}, {"type": "state", "t": 2},
    ):
        client.enqueue(message)
    assert [message["type"] for message in client.queue] == [
        "caption", "audio_chunk", "state",
    ]
    assert client.queue[-1]["t"] == 2


def test_viewer_cannot_transmit_and_bad_message_is_recoverable(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=viz") as viewer:
            viewer.receive_json()
            viewer.receive_json()
            viewer.send_json({"type": "typed", "text": "BAW117 heading 240"})
            assert "Only the radar" in viewer.receive_json()["text"]
            viewer.send_text("{")
            assert viewer.receive_json()["type"] == "caption"
        assert client.get("/api/data/egkk").status_code == 200
        assert client.get("/api/data/not-a-file").status_code == 404


def test_wrong_session_rejected(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    import pytest
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?role=viz&session=WRONG"):
                pass
