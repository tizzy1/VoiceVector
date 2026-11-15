"""§11.1 pronunciation: ICAO respellings in tts_text only, and spelled RNAV points (SIM_SPEC §2.1.1)."""
import json
import random
from pathlib import Path

import pytest

from server.contracts import CallsignInfo, Instruction, ReadbackPlan
from server.pilot.readback import Composer
from server.voice.normalise import normalise_tokens
from server.voice.parser import parse
from server.voice.parser import default_fixes
from server.voice.pronounce import RESPELL, is_spelled_point, respell, spell
from server.voice.prosody import shape

DATA = Path(__file__).resolve().parents[3] / "data"
POINTS = sorted(f for f in default_fixes() if is_spelled_point(f))


@pytest.fixture(scope="module")
def composer():
    return Composer(DATA)


def test_lima_is_leema_in_speech_only_and_maps_back(composer):
    rb = composer.compose(ReadbackPlan(callsign="DLH4LQ", persona="crisp_airline", kind="full",
                                       elements=[Instruction(type="heading", value=240, turn_dir="L")], seed=2))
    assert rb.validated and not rb.fallback_used
    assert "leema kebeck" in rb.tts_text and "lima" not in rb.tts_text
    assert "4LQ" in rb.text and "eema" not in rb.text                 # display unchanged
    assert normalise_tokens("lufthansa four leema kebeck") == normalise_tokens("lufthansa four lima quebec")
    info = CallsignInfo(icao="DLH4LQ", telephony="Lufthansa", spoken="lufthansa four lima quebec",
                        spoken_digits="lufthansa 4 lima quebec")
    tx = parse(rb.tts_text, callsigns=[info])
    assert tx.receiver == "DLH4LQ" and [i.value for i in tx.instructions] == [240]


def test_respell_keeps_capitals_and_whole_words():
    assert respell("Lima, lima lima") == "Leema, leema leema"
    assert respell("climate") == "climate" and respell("Quebec") == "Kebeck"
    assert all(" " not in v and "-" not in v for v in RESPELL.values())   # word counts never shift
    from server.voice.pronounce import CANDIDATES
    # capitals get spelled out by the TTS ("pahPAH" -> "Papa H"), so respellings are lowercase words
    assert all(a.isalpha() and a.islower() for alts in CANDIDATES.values() for a in alts)
    assert all(v.isalpha() and v.islower() for v in RESPELL.values())


def test_atis_letter_is_respelled(composer):
    rb = composer.compose(ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="checkin",
                                       report_values={"level_fl": 90, "atis": "L"}, seed=1))
    if "information" in rb.tts_text:
        assert "leema" in rb.tts_text


@pytest.mark.parametrize("point", POINTS)
def test_every_rnav_point_is_spelled_in_readbacks_and_parses_back(composer, point):
    for etype in ("direct", "hold"):
        rb = composer.compose(ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="full",
                                           elements=[Instruction(type=etype, fix=point)], seed=5))
        assert rb.validated and not rb.fallback_used, rb
        assert respell(spell(point)) in rb.tts_text and point in rb.text
        verb = "proceed direct" if etype == "direct" else "hold at"
        said = f"speedbird one one seven {verb} {respell(spell(point))}"
        assert [(i.type, i.fix) for i in parse(said).instructions] == [(etype, point)]


def test_spelled_points_parse_from_words_glued_and_letters():
    for said in ("proceed direct kilo kilo sierra two five", "direct KKS25", "direct kks 25",
                 "direct k k s 2 5", "direct kilo kilo sierra two five, descend flight level seven zero"):
        tx = parse("speedbird one one seven " + said)
        assert tx.instructions[0].fix == "KKS25", said
    assert parse("speedbird one one seven direct leema").instructions == []     # unknown: no guess


def test_numbers_that_are_not_points_are_never_swallowed():
    rng = random.Random(4)
    for _ in range(200):
        hdg = rng.randint(1, 360)
        tx = parse(f"speedbird one one seven turn left heading {' '.join('zero one two three four five six seven eight niner'.split()[int(d)] for d in f'{hdg:03d}')}")
        assert [i.value for i in tx.instructions] == [hdg]
    tx = parse("speedbird one one seven squawk two five one three, QNH one zero one three")
    assert [(i.type, i.value) for i in tx.instructions] == [("squawk", "2513"), ("qnh", 1013)]


def test_prosody_never_touches_a_respelled_callsign(composer):
    cs = respell(composer.callsign_speech("DLH4LQ")[1])
    for seed in range(60):
        rb = composer.compose(ReadbackPlan(callsign="DLH4LQ", persona="student", kind="full", seed=seed,
                                           elements=[Instruction(type="qnh", value=1013),
                                                     Instruction(type="heading", value=240, turn_dir="L")]))
        assert cs in shape(rb.tts_text, "student", seed=seed, callsign_speech=cs).text


def test_check_page_covers_every_letter_navaid_fix_and_point(composer):
    from server.voice.pronounce_check import items, page
    todo = items(composer)
    ids = {i["id"] for i in todo}
    assert {f"letter:{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"} <= ids
    assert {f"navaid:{n}" for n in composer.navaid_names} <= ids
    assert {f"point:{p}" for p in POINTS} <= ids and "fix:WILLO" in ids
    lima = next(i for i in todo if i["id"] == "letter:L")
    assert "Leema" in lima["text"] and lima["live"]
    for i in todo:
        i["file"] = "x.wav"
    html = page(todo, "Sweatbox test", radio=False)
    assert html.count("<audio") == len(todo) and "Export" in html


def test_direct_to_a_passed_fix_is_queried_in_words_end_to_end(composer):
    """Tudor's evaluate (QUERY_UNKNOWN_FIX + what=fix_passed) -> pilot -> "confirm direct TUFOZ, we've passed it"."""
    from server.contracts import SessionSettings, Transmission
    from server.pilot.engine import PilotEngine
    from server.sim.world import World
    from server.tests.voice.bench_latency import Bus
    bus = Bus()
    world = World(bus, DATA)
    bus.clock = lambda: world.t
    world.configure(SessionSettings(error_rate="off"))
    world.inject("spawn_arrival", params={"entry": "DISIT"})
    ac = next(a for a in world.aircraft.values() if a.route_name == "DISIT1G")
    ac.route, ac.route_idx = ["KIDLI", "MID", "TUFOZ", "HOLLY", "WILLO"], 3
    pilot = PilotEngine(world, bus, DATA)
    pilot.set_error_rate("off")
    tx = Transmission(tx_id="d", t=0, receiver=ac.callsign, instructions=[Instruction(type="direct", fix="TUFOZ")])
    responses = pilot.handle_transmission(tx, world.evaluate(tx))
    plan = next(r.plan for r in responses if r.plan is not None)
    rb = composer.compose(plan)
    assert rb.validated and rb.variant_ids == ["query.QUERY_FIX_PASSED"], rb
    assert "confirm direct TUFOZ" in rb.text or "passed TUFOZ" in rb.text
    assert "tufoz" in rb.tts_text and ("passed" in rb.tts_text or "past" in rb.tts_text)


def test_fix_passed_reply_also_works_with_the_future_contract_code(composer):
    plan = ReadbackPlan.model_construct(callsign="BAW117", persona="crisp_airline", kind="query",
                                        reason_code="QUERY_FIX_PASSED", reason_values={"fix": "KKS25"},
                                        elements=[], report_values={}, seed=1)
    rb = composer.compose(plan)
    assert rb.validated and "kilo kilo sierra two five" in rb.tts_text and "KKS25" in rb.text
