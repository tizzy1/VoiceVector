"""Spoken ATIS (server/voice/atis.py, VoicePipeline.play_atis)."""
import asyncio

import pytest

from server.voice import atis
from server.voice.parser import parse

METAR = {"observed": "17:50Z", "wind_dir_deg": 250, "wind_kt": 12, "calm": False, "visibility_m": 9999,
         "cloud": [{"cover": "BKN", "base_ft": 1200}], "cavok": False, "temp_c": -2, "dewpoint_c": -4,
         "qnh_hpa": 998}


def test_text_matches_the_simulator_weather_and_letter():
    shown, said = atis.build(METAR, "26L", "K")
    assert "information Kilo" in shown and "Runway 26L" in shown and "Wind 250 degrees 12 knots" in shown
    assert "QNH 998" in shown and "Cloud broken 1,200 feet" in shown
    assert not any(c.isdigit() for c in said)
    assert "minus two" in said and "niner niner eight" in said
    assert "Leema" in atis.build(METAR, "26L", "L")[1]                  # ICAO respelling


def test_calm_and_cavok():
    shown, said = atis.build({**METAR, "calm": True, "wind_kt": 0, "cavok": True}, "08R", "A")
    assert "Wind calm" in shown and "CAVOK" in shown and "Cloud" not in shown and "eight right" in said


@pytest.mark.asyncio
async def test_pipeline_streams_atis_without_taking_the_director_frequency():
    from server.tests.voice.fakes import FakeTTS
    from server.tests.voice.test_pipeline import make
    pipe, world, pilot, hub, bus, _ = make(tts=FakeTTS(chunks=4, delay=0.05))
    world.metar = lambda: METAR
    world.runway = "26L"
    shown, again = await asyncio.gather(pipe.play_atis("K"), pipe.play_atis("K"))
    assert "information Kilo" in shown and again == ""                   # one at a time
    assert [m.callsign for m in hub.of("audio_start")] == ["ATIS"] and len(hub.of("audio_chunk")) == 4
    assert hub.captions("pilot_text")[0].speaker == "ATIS"
    assert not pipe.freq.active and pipe.tts.requests[0][1] == atis.ATIS_VOICE_ID
