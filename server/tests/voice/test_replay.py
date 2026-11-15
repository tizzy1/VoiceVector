"""'Hear what you missed' (server/voice/replay.py): clips of readbacks with planted errors."""
import io
import wave

import pytest

from server.contracts import ErrorRecord
from server.tests.voice.fakes import FakeTTS, heading_response
from server.voice.replay import ReplayStore


def err(eid, mode="consistent"):
    return ErrorRecord(id=eid, callsign="BAW117", kind="wrong_value_adjacent", mode=mode,
                       element_type="heading", intended=240.0, readback_value=250.0, flown_value=250.0, t_planned=0)


def test_only_spoken_errors_are_kept_and_served_as_radio_wav():
    store = ReplayStore(max_clips=2)
    resp = heading_response(errors=[err("e1"), err("e2", "execution_only")])
    pcm = (b"\x10\x00\xf0\xff" * 4000)
    assert store.keep(resp, pcm, 22050, "pcm_s16le", "Left heading 250, Speedbird 117") == ["e1"]
    with wave.open(io.BytesIO(store.wav("e1"))) as w:
        assert w.getframerate() == 22050 and w.getnframes() >= len(pcm) // 2     # + squelch tail
    assert store.wav("e2") is None and store.wav("nope") is None
    assert store.keep(resp, pcm, 22050, "mp3", "x") == []
    for k in range(3, 6):
        store.keep(heading_response(errors=[err(f"e{k}")]), pcm, 22050, "pcm_s16le", "x")
    assert list(store.clips) == ["e4", "e5"]                                    # oldest dropped


@pytest.mark.asyncio
async def test_pipeline_keeps_the_audio_of_an_erroneous_readback():
    from server.tests.voice.fakes import FakePilot
    from server.tests.voice.test_pipeline import make, type_and_settle
    pipe, world, pilot, hub, bus, _ = make(pilot=FakePilot([heading_response(errors=[err("e9")])]),
                                           tts=FakeTTS(chunks=3, chunk_bytes=490))
    await type_and_settle(pipe, hub)
    assert "e9" in pipe.replays.clips and len(pipe.replays.clips["e9"]["pcm"]) == 3 * 490
    pipe2, _, _, hub2, _, _ = make()
    await type_and_settle(pipe2, hub2)
    assert not pipe2.replays.clips                                              # clean readbacks: nothing kept


def test_replay_route_serves_wav_and_404s(tmp_path):
    from fastapi.testclient import TestClient
    from server.main import create_app
    app = create_app(runs_dir=tmp_path)
    with TestClient(app) as client:
        voice = app.state.session.voice
        if voice is None:
            pytest.skip("voice pipeline unavailable in this environment")
        voice.replays.keep(heading_response(errors=[err("e1")]), b"\x10\x00" * 8000, 22050, "pcm_s16le", "x")
        r = client.get("/api/replay/e1")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
        assert client.get("/api/replay/missing").status_code == 404
