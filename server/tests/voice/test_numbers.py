"""Number round-trip and normaliser tests (VOICE_PIPELINE §17.3)."""
import pytest

from server.voice import numbers as n
from server.voice.normalise import normalise


@pytest.mark.parametrize("heading", list(range(1, 361)))
def test_heading_round_trip(heading):
    assert n.speech_to_number(n.heading_to_speech(heading)) == heading


@pytest.mark.parametrize("feet", [1000, 1500, 2000, 3500, 4000, 5000, 6000, 7000, 12000])
def test_altitude_round_trip(feet):
    assert n.speech_to_number(n.altitude_to_speech(feet)) == feet


@pytest.mark.parametrize("fl", [30, 70, 80, 90, 100, 120, 250])
def test_flight_level_round_trip(fl):
    text = n.flight_level_to_speech(fl)
    assert text.startswith("flight level ")
    assert n.speech_to_number(text.removeprefix("flight level ")) == fl


@pytest.mark.parametrize("kt", [120, 160, 180, 210, 250, 320])
def test_speed_round_trip(kt):
    assert n.speech_to_number(n.speed_to_speech(kt)) == kt


@pytest.mark.parametrize("qnh", [980, 998, 1013, 1021, 1040])
def test_qnh_round_trip(qnh):
    assert n.speech_to_number(n.qnh_to_speech(qnh)) == qnh


def test_squawk_keeps_leading_zeros():
    tokens = n.squawk_to_speech("0421").split()
    assert n.parse_run(tokens, 0)[1] == "0421"


def test_speech_never_contains_digits():
    for text in (n.heading_to_speech(90), n.altitude_to_speech(3500), n.speed_to_speech(210),
                 n.frequency_to_speech("119.6"), n.runway_to_speech("26L"), n.squawk_to_speech("4521")):
        assert not any(c.isdigit() for c in text)


@pytest.mark.parametrize("value,spoken", [
    (n.heading_to_speech(240), "two four zero"),
    (n.heading_to_speech(90), "zero niner zero"),
    (n.altitude_to_speech(4000), "four thousand"),
    (n.altitude_to_speech(3500), "three thousand five hundred"),
    (n.flight_level_to_speech(80), "flight level eight zero"),
    (n.runway_to_speech("26L"), "two six left"),
    (n.frequency_to_speech("119.6"), "one one niner decimal six"),
])
def test_speech_forms(value, spoken):
    assert value == spoken


def test_frequency_round_trip():
    assert n.parse_frequency(n.frequency_to_speech("124.225").split(), 0)[0] == "124.225"


@pytest.mark.parametrize("raw,norm", [
    ("Speed bird 117, turn left!", "speedbird 117 turn left"),
    ("easy for five alpha bravo", "easy four five alpha bravo"),
    ("two for zero", "two four zero"),
    ("descend altitude for thousand feet", "descend altitude four thousand feet"),
    ("slow down to one eight zero knots", "slow to one eight zero knots"),
    ("descend to altitude 5,000 feet", "descend to altitude 5000 feet"),
    ("q n h one zero one three", "qnh one zero one three"),
    ("Wizz Air 4471", "wizzair 4471"),
])
def test_normalise(raw, norm):
    assert normalise(raw) == norm



@pytest.mark.parametrize("raw,norm", [
    ("EZ45ABQNH1013.", "ez 45 ab qnh 1013"),            # Scribe glued a whole transmission (benchmark #12)
    ("Lufthansa 4KA descend FL80", "lufthansa 4 ka descend fl 80"),
    ("cleared ILS runway 26L", "cleared ils runway 26l"),  # runway tokens stay whole
    ("contact tower 124.225", "contact tower 124.225"),    # decimals stay whole
    ("the engine fails details", "the engine fails details"),   # plain words are never split
])
def test_glued_stt_tokens_are_split(raw, norm):
    assert normalise(raw) == norm


def test_glued_transmission_parses():
    from server.voice.parser import parse
    tx = parse("EZ45ABQNH1013.")
    assert tx.receiver == "EZY45AB" and [(i.type, i.value) for i in tx.instructions] == [("qnh", 1013)]
