"""The real FastAPI app + WebSocket + World + PilotEngine + RulesEngine + my pipeline (fake TTS).

This is the closest offline approximation of the live round trip: typed clearance in, captions,
a validated spoken readback streamed as audio messages, and the aircraft actually turning.
"""
import time

import pytest
from starlette.testclient import TestClient

from server.agents.narrative import Narrative
from server.main import create_app
from server.pilot.engine import PilotEngine
from server.pilot.readback import Composer
from server.rules.engine import RulesEngine
from server.sim.world import World
from server.tests.voice.fakes import FakeSTT, FakeTTS
from server.voice.pipeline import VoicePipeline


class WarmWorld(World):
    """Skip the quiet start: have the demo traffic on frequency straight away."""

    def load_scenario(self, name, seed):
        super().load_scenario(name, seed)
        for _ in range(60):
            self.tick(1.0)


class FastPilot(PilotEngine):
    """Real decisions and errors; only the think-time is shortened so the test stays quick."""

    def handle_transmission(self, tx, evals):
        out = super().handle_transmission(tx, evals)
        for resp in out:
            resp.reaction_delay_s = 0.05
            resp.apply_delay_s = 0.2
        return out


def factories(stt_text="speedbird one one seven turn left heading two four zero"):
    created = {}

    def voice(world, pilot, composer, bus, hub):
        created["voice"] = VoicePipeline(
            world, pilot, composer, bus, hub, tts=FakeTTS(chunks=3), seed=1,
            stt_factory=lambda terms, on_partial: FakeSTT(text=stt_text, on_partial=on_partial))
        return created["voice"]

    return dict(world=WarmWorld, pilot=FastPilot, rules=RulesEngine, composer=Composer,
                voice=voice, narrative=Narrative), created


def collect(ws, until, limit=300):
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if until(seen):
            return seen
    raise AssertionError(f"condition not reached; last types: {[m['type'] for m in seen[-10:]]}")


def has(seen, kind, **match):
    return any(m["type"] == kind and all(m.get(k) == v for k, v in match.items()) for m in seen)


def test_typed_clearance_round_trip_through_the_real_app(tmp_path):
    fac, created = factories()
    with TestClient(create_app(fac, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar, client.websocket_connect("/ws?role=viz") as viz:
            collect(radar, lambda s: has(s, "state"))
            collect(viz, lambda s: has(s, "state"))
            radar.send_json({"type": "typed", "text": "BAW117 turn left heading 240"})
            seen = collect(viz, lambda s: has(s, "audio_end"))
            final = next(m for m in seen if m["type"] == "caption" and m["stage"] == "final")
            assert final["speaker"] == "CTRL" and [c["label"] for c in final["chips"]][:2] == ["BAW117", "HDG 240 L"]
            start = next(m for m in seen if m["type"] == "audio_start")
            assert start["callsign"] == "BAW117" and start["format"] == "pcm_s16le" and start["sample_rate"] == 22050
            pilot_text = next(m for m in seen if m["type"] == "caption" and m["stage"] == "pilot_text")
            assert "240" in pilot_text["text"] and pilot_text["latency"]["perceived_s"] >= 0
            assert sum(m["type"] == "audio_chunk" for m in seen) == 3
            order = [m["type"] for m in seen]
            assert order.index("audio_start") < order.index("audio_chunk") < order.index("audio_end")
            events = {m["event"]["type"] for m in seen if m["type"] == "event"}
            assert "ctrl_transmission" in events
            # the aircraft actually obeys the (spoken) clearance
            seen2 = collect(viz, lambda s: any(
                m["type"] == "state" and any(a["callsign"] == "BAW117" and a.get("tgt_hdg") == 240
                                             for a in m["aircraft"]) for m in s), limit=2000)
            assert has(seen2, "state")
            assert created["voice"].metrics.summary()["perceived_s"]["n"] >= 1


def test_push_to_talk_round_trip_with_binary_audio(tmp_path):
    fac, created = factories()
    with TestClient(create_app(fac, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            collect(radar, lambda s: has(s, "state"))
            radar.send_json({"type": "ptt", "state": "down"})
            for _ in range(6):
                radar.send_bytes(b"\x01\x00" * 1600)            # 6 x 100 ms of mic audio
            time.sleep(0.05)
            radar.send_json({"type": "ptt", "state": "up"})
            seen = collect(radar, lambda s: has(s, "audio_end"))
            final = next(m for m in seen if m["type"] == "caption" and m["stage"] == "final")
            assert final["text"].startswith("speedbird one one seven") and final["chips"][0]["label"] == "BAW117"
            events = [m["event"]["type"] for m in seen if m["type"] == "event"]
            assert events.index("ctrl_ptt_down") < events.index("ctrl_ptt_up") < events.index("ctrl_transmission")


def test_audio_without_ptt_is_rejected_and_the_session_survives(tmp_path):
    fac, _ = factories()
    with TestClient(create_app(fac, tmp_path)) as client:
        with client.websocket_connect("/ws?role=radar") as radar:
            collect(radar, lambda s: has(s, "state"))
            radar.send_bytes(b"\x00\x00" * 100)
            seen = collect(radar, lambda s: any(m["type"] == "caption" and m["speaker"] == "SYSTEM" for m in s))
            assert any("push-to-talk" in m.get("text", "") for m in seen if m["type"] == "caption")
            radar.send_json({"type": "typed", "text": "BAW117 turn left heading 240"})
            collect(radar, lambda s: has(s, "audio_end"))


def test_the_app_reports_ready_with_every_module_present(tmp_path):
    fac, _ = factories()
    with TestClient(create_app(fac, tmp_path)) as client:
        health = client.get("/api/health").json()
        assert health["ready"] is True and health["missing_modules"] == []


def test_real_module_set_has_no_missing_modules():
    from server.session import load_factories
    _, missing = load_factories()
    assert missing == []


def test_force_overlap_inject_produces_two_simultaneous_streams_in_one_group(tmp_path):
    fac, created = factories()
    with TestClient(create_app(fac, tmp_path)) as client:
        with client.websocket_connect("/ws?role=viz") as viz:
            collect(viz, lambda s: has(s, "state"))
            viz.send_json({"type": "control", "action": "inject", "params": {"kind": "force_overlap"}})
            seen = collect(viz, lambda s: sum(bool(m["type"] == "audio_start" and m.get("overlap_group")) for m in s) >= 2
                           and any(m["type"] == "event" and m["event"]["type"] == "overlap" for m in s), limit=3000)
            pair = [m for m in seen if m["type"] == "audio_start" and m.get("overlap_group")]
            assert pair[0]["overlap_group"] == pair[1]["overlap_group"] and pair[0]["callsign"] != pair[1]["callsign"]
            overlap = next(m["event"] for m in seen if m["type"] == "event" and m["event"]["type"] == "overlap")
            assert overlap["data"]["with_ctrl"] is False and len(overlap["data"]["tx_ids"]) == 2
