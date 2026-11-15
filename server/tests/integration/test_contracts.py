import json

from fastapi.testclient import TestClient

from server.contracts import (
    CaptionMsg, ControlMsg, Evaluation, Event, PipelineTrace, SessionMsg, StateMsg,
    TraceMsg, TraceStage,
)
from server.hub import Client, Hub
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


def test_aircraft_extensions_preserve_legacy_state_and_explicit_units(tmp_path):
    with TestClient(create_app(FACTORIES, tmp_path)) as client:
        with client.websocket_connect("/ws?role=viz") as viewer:
            viewer.receive_json()
            state = StateMsg.model_validate(viewer.receive_json())
    legacy = state.aircraft[0]
    assert legacy.adep is None and legacy.selected_level is None and legacy.strip == {}
    raw = legacy.model_dump() | {
        "flight_rules": "I", "adep": "LEMD", "ades": "EGKK", "rfl": 350,
        "assigned_squawk": "0421", "selected_level": 8000,
        "strip": {"cfl": 7000, "rfl": 350, "clr": False},
    }
    state.aircraft[0] = type(legacy).model_validate(raw)
    actual = StateMsg.model_validate_json(state.model_dump_json()).aircraft[0]
    assert actual.rfl == 350 and actual.selected_level == 8000
    assert actual.assigned_squawk == "0421"
    assert actual.strip["cfl"] == 7000 and actual.cleared_alt == legacy.cleared_alt


def test_trace_roundtrip_preserves_negative_speech_start_and_partial_final_updates():
    speech = TraceStage(name="speech", category="capture", t0_ms=-3000, t1_ms=0)
    provisional = TraceMsg(trace=PipelineTrace(tx_id="tx-1", speaker="CTRL",
        reply_callsign="BAW117", stages=[speech]))
    payload = TraceMsg.model_validate_json(provisional.model_dump_json())
    assert payload.type == "trace" and not payload.trace.final
    assert payload.trace.stages[0].t0_ms == -3000
    assert payload.trace.stages[0].status == "ok"
    assert payload.trace.stages[0].detail == {}
    final = payload.model_copy(deep=True)
    final.trace.total_ms = 642
    final.trace.final = True
    final.trace.stages.append(TraceStage(name="tts_first_audio", category="voice",
        t0_ms=200, t1_ms=600, detail={"machine_ms": 600, "model": "test-model"}))
    restored = TraceMsg.model_validate_json(final.model_dump_json())
    assert restored.trace.total_ms == 642 and restored.trace.final
    assert restored.trace.stages[-1].detail["machine_ms"] == 600
    assert not provisional.trace.final and len(provisional.trace.stages) == 1


def test_trace_categories_statuses_and_blocked_transmission_shape():
    from pydantic import ValidationError
    import pytest
    for field, value in [("category", "unknown"), ("status", "invented")]:
        with pytest.raises(ValidationError):
            TraceStage.model_validate({"name": "parse", "category": "language",
                "t0_ms": 0, "t1_ms": 5, field: value})
    blocked = TraceMsg(trace=PipelineTrace(tx_id="blocked-1", speaker="CTRL", final=True,
        stages=[TraceStage(name="blocked", category="sim", t0_ms=50, t1_ms=50,
            status="error")]))
    assert TraceMsg.model_validate_json(blocked.model_dump_json()).trace.stages[0].name == "blocked"


def test_edit_strip_and_fix_passed_reason_are_valid_contract_values():
    control = ControlMsg.model_validate({"type": "control", "action": "edit_strip",
        "params": {"callsign": "BAW117", "field": "cfl", "value": "F70"}})
    assert control.params["value"] == "F70"
    event = Event(t=1, type="strip_edit", actor="CONTROLLER", callsign="BAW117",
        data={"field": "cfl", "value": 7000, "by": "controller"})
    assert Event.model_validate_json(event.model_dump_json()).type == "strip_edit"
    query = Evaluation(instr_idx=0, status="QUERY", reason_code="QUERY_FIX_PASSED",
        reason_values={"fix": "TUFOZ"})
    assert query.reason_values["fix"] == "TUFOZ"


async def test_trace_updates_reach_both_roles_in_order_without_displacing_audio():
    hub = Hub()
    radar, viz = Client(None, "radar"), Client(None, "viz")
    hub.clients = {"radar": radar, "viz": viz}
    await hub.send(TraceMsg(trace=PipelineTrace(tx_id="t", speaker="CTRL")))
    await hub.send({"type": "audio_chunk", "tx_id": "t", "seq": 0, "data_b64": "AA=="})
    await hub.send(TraceMsg(trace=PipelineTrace(tx_id="t", speaker="CTRL", final=True)))
    for client in (radar, viz):
        messages = list(client.queue)
        assert [message["type"] for message in messages] == ["trace", "audio_chunk", "trace"]
        assert messages[0]["trace"]["final"] is False
        assert messages[-1]["trace"]["final"] is True
