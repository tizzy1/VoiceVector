"""Composer + validator acceptance tests (VOICE_PIPELINE §17, ASSIGNMENT_SANJU §7)."""
import random
import re
from pathlib import Path

import pytest

from server.contracts import CallsignInfo, Instruction, ReadbackPlan
from server.pilot import validator as V
from server.pilot.readback import Composer
from server.pilot.variants_build import DEFAULT_BANK, REPLIES, REASON_PHRASES
from server.voice import numbers as N
from server.voice.normalise import normalise_tokens
from server.voice.parser import parse

DATA = Path(__file__).resolve().parents[3] / "data"
FIXES = ["WILLO", "TIMBA", "HOLLY", "BOGNA", "MAY", "SFD"]
CALLSIGNS = ["BAW117", "EZY45AB", "DLH4KA", "UAE15", "WZZ4471", "RYR8KP", "TOM1AB"]
PERSONAS = ["crisp_airline", "calm_longhaul", "student", "rushed", "stressed"]


@pytest.fixture(scope="module")
def composer():
    return Composer(DATA)


def random_element(rng: random.Random, etype: str) -> Instruction:
    verb = rng.choice(["climb", "descend", None])
    if etype == "heading":
        return Instruction(type="heading", value=rng.randint(1, 360), turn_dir=rng.choice(["L", "R", None]))
    if etype == "turn_by":
        return Instruction(type="turn_by", value=rng.choice([5, 10, 15, 20, 30, 40]), turn_dir=rng.choice(["L", "R"]))
    if etype == "altitude":
        return Instruction(type="altitude", value=rng.choice(range(1000, 12500, 500)), verb=verb)
    if etype == "flight_level":
        return Instruction(type="flight_level", value=rng.choice(range(30, 251, 10)), verb=verb)
    if etype == "maintain_level":
        value = rng.choice([rng.choice(range(2000, 9000, 1000)), rng.choice(range(60, 200, 10))])
        return Instruction(type="maintain_level", value=value, verb="maintain")
    if etype == "vertical_rate":
        return Instruction(type="vertical_rate", value=rng.choice([500, 1000, 1500, 2000]),
                           verb=rng.choice(["climb", "descend"]), constraint=rng.choice(["max", "min", None]))
    if etype == "speed":
        return Instruction(type="speed", value=rng.randrange(130, 330, 10),
                           constraint=rng.choice(["or_greater", "or_less", None]))
    if etype in ("direct", "hold"):
        return Instruction(type=etype, fix=rng.choice(FIXES))
    if etype == "approach_clearance":
        return Instruction(type=etype, approach_type="ILS", runway=rng.choice(["26L", "26R", "08L", "08R"]))
    if etype in ("intercept_localiser", "cleared_to_land", "cleared_takeoff"):
        return Instruction(type=etype, runway=rng.choice(["26L", "26R", "08L", "08R"]))
    if etype == "contact":
        return Instruction(type="contact", unit=rng.choice(["tower", "london", "director"]),
                           frequency=rng.choice(["124.225", "134.125", "119.6", "126.825", None]))
    if etype == "squawk":
        return Instruction(type="squawk", value=rng.choice(["ident", f"{rng.randint(0, 7777):04d}".replace("8", "7").replace("9", "7")]))
    if etype == "qnh":
        return Instruction(type="qnh", value=rng.randint(980, 1040))
    if etype == "expect":
        return Instruction(type="expect", expect_what=rng.choice(["ILS 26L", "ILS 08R", "FL70", "FL90", "vectors"]))
    if etype == "report":
        return Instruction(type="report", report_what=rng.choice(["established", "heading", "speed", "level"]))
    return Instruction(type=etype)


ELEMENT_TYPES = sorted(set(DEFAULT_BANK) - {"traffic_info"})


def plan_for(rng, elements, kind="full", persona=None, callsign=None, seed=None) -> ReadbackPlan:
    return ReadbackPlan(callsign=callsign or rng.choice(CALLSIGNS), persona=persona or rng.choice(PERSONAS),
                        kind=kind, elements=elements, seed=rng.randint(0, 10**6) if seed is None else seed)


@pytest.mark.parametrize("etype", ELEMENT_TYPES + ["maintain_level"])
def test_every_template_validates_across_200_random_values(composer, etype):
    rng = random.Random(f"values-{etype}")
    for _ in range(200):
        el = random_element(rng, etype)
        readback = composer.compose(plan_for(rng, [el]))
        assert readback.validated and not readback.fallback_used, (el, readback)
        assert not any(c.isdigit() for c in readback.tts_text), readback.tts_text


@pytest.mark.parametrize("etype", ELEMENT_TYPES)
def test_each_template_in_the_bank_round_trips(composer, etype):
    """Force every template of every element type and re-check it with the validator."""
    rng = random.Random(f"templates-{etype}")
    for template in DEFAULT_BANK[etype]:
        used = 0
        for _ in range(60):
            el = random_element(rng, etype)
            if etype == "maintain_level":
                continue
            slots = composer.slots_for(el)
            from server.pilot.readback import _usable
            if not _usable(template, slots):
                continue
            used += 1
            disp, speech, _ = composer.render_phrase(template["t"], slots)
            assert V.check_element(el, speech) == [], (template["id"], el, speech)
        assert used > 0, f"template {template['id']} can never be used"


def test_bank_literals_never_contain_numbers_or_directions():
    number_words = set(N.DIGIT_WORDS) | set(N.TENS_WORDS) | set(N.MULTIPLIERS)
    texts = [t["t"] for ts in DEFAULT_BANK.values() for t in ts] + [t for ts in REPLIES.values() for t in ts]
    texts += [t for options in REASON_PHRASES.values() for t, _ in options]
    for text in texts:
        literal = re.sub(r"\{\w+\}", " ", text).lower()
        words = set(re.findall(r"[a-z]+", literal))
        assert not (words & number_words), text
        assert not (words & {"left", "right"}), text
        assert not re.search(r"\d", literal), text


def test_bank_has_at_least_six_phrasings_for_common_elements():
    for etype in ("heading", "altitude", "flight_level", "speed", "direct", "approach_clearance", "contact"):
        assert len(DEFAULT_BANK[etype]) >= 6, etype


@pytest.mark.parametrize("etype", ["heading", "altitude", "flight_level", "speed", "direct", "hold",
                                   "approach_clearance", "squawk", "qnh", "contact", "speed_cancel",
                                   "intercept_localiser", "resume_nav", "turn_by"])
def test_full_readback_reparses_with_the_independent_controller_parser(composer, etype):
    """Second opinion: the whole spoken readback, parsed by the full controller parser."""
    rng = random.Random(f"parse-{etype}")
    checked = 0
    for _ in range(80):
        el = random_element(rng, etype)
        cs = rng.choice(["BAW117", "EZY45AB", "DLH4KA"])
        readback = composer.compose(plan_for(rng, [el], callsign=cs))
        spoken, _ = composer.callsign_speech(cs)[1], None
        info = CallsignInfo(icao=cs, telephony="x", spoken=composer.callsign_speech(cs)[1],
                            spoken_digits=composer.callsign_speech(cs)[0])
        tx = parse(readback.tts_text, callsigns=[info])
        if tx.receiver != cs or not tx.instructions:
            continue   # bare phrasings ("four thousand") are valid pilot speech the controller grammar cannot type
        checked += 1
        got = tx.instructions[0]
        if el.type in V.LEVEL_TYPES:
            assert float(got.value) == float(el.value)
        else:
            assert got.type == el.type
            for name in ("value", "turn_dir", "runway", "fix", "frequency", "unit"):
                want = getattr(el, name)
                if name == "value" and want is not None and not isinstance(want, str):
                    assert float(getattr(got, name)) == float(want), (readback.tts_text, name)
                elif name in ("turn_dir", "runway", "fix", "frequency", "unit", "value"):
                    assert getattr(got, name) == want, (readback.tts_text, name, el)
    assert checked >= 20, f"{etype}: only {checked} readbacks were typeable"


def test_planned_errors_are_rendered_not_corrected(composer):
    rng = random.Random(3)
    plan = plan_for(rng, [Instruction(type="altitude", value=5000, verb="descend")], persona="crisp_airline")
    readback = composer.compose(plan)   # controller said 4000; the plan carries the planned error 5000
    assert "five thousand" in readback.tts_text and "four thousand" not in readback.tts_text
    swapped = plan_for(rng, [Instruction(type="heading", value=240, turn_dir="R")])
    text = composer.compose(swapped).tts_text
    assert "right" in text and "left" not in text
    runway = plan_for(rng, [Instruction(type="approach_clearance", approach_type="ILS", runway="26R")])
    assert "two six right" in composer.compose(runway).tts_text


def test_1000_random_plans_have_zero_unplanned_discrepancies(composer):
    rng = random.Random("thousand")
    types = [t for t in ELEMENT_TYPES if t not in ("expect",)]
    for n in range(1000):
        elements = [random_element(rng, rng.choice(types)) for _ in range(rng.randint(1, 4))]
        kind = rng.choice(["full", "full", "partial_correction"])
        plan = plan_for(rng, elements, kind=kind)
        readback = composer.compose(plan)
        assert readback.validated and not readback.fallback_used, (plan, readback)
        tokens = normalise_tokens(readback.tts_text)
        said = sum(1 for t in tokens if V.is_numberish(t))
        planned = 0
        for el in elements:
            slots = composer.slots_for(el)
            for name, (disp, speech) in slots.items():
                if name not in ("QNHW",) and speech:
                    planned += sum(1 for t in normalise_tokens(speech) if V.is_numberish(t))
        planned += sum(1 for t in normalise_tokens(composer.callsign_speech(plan.callsign)[1]) if V.is_numberish(t)) * readback_callsign_count(readback, composer, plan)
        assert said == planned, (readback.tts_text, said, planned)
        # every element's own spoken value appears verbatim
        for el in elements:
            slots = composer.slots_for(el)
            for name in ("HDG", "ALT", "FL", "SPD", "RWY", "SQ", "QNH", "FREQ", "RATE", "N"):
                speech = slots[name][1]
                if speech and speech != "ident":
                    assert speech in readback.tts_text, (name, speech, readback.tts_text)


def readback_callsign_count(readback, composer, plan) -> int:
    return readback.tts_text.count(composer.callsign_speech(plan.callsign)[1])


def test_partial_correction_reads_back_only_the_corrected_element_and_callsign(composer):
    rng = random.Random("partial")
    for _ in range(100):
        el = Instruction(type="altitude", value=rng.choice([3000, 4000, 5000, 6000]), verb="descend")
        plan = plan_for(rng, [el], kind="partial_correction", callsign="BAW117")
        text = composer.compose(plan).tts_text
        assert "speedbird one one seven" in text
        assert not any(w in text for w in ("heading", "speed ", "runway", "squawk"))
        assert len([t for t in normalise_tokens(text) if t in N.MULTIPLIERS]) == 1


def test_validator_actually_rejects_wrong_readbacks(composer):
    el = Instruction(type="heading", value=240, turn_dir="L")
    assert V.check_element(el, "left heading two four zero") == []
    assert V.check_element(el, "right heading two four zero")        # wrong direction
    assert V.check_element(el, "left heading two five zero")         # wrong value
    assert V.check_element(el, "heading two four zero")              # direction dropped
    alt = Instruction(type="altitude", value=4000, verb="descend")
    assert V.check_element(alt, "descend flight level four zero")    # level type confusion
    assert V.check_element(alt, "descend altitude five thousand")
    rwy = Instruction(type="approach_clearance", approach_type="ILS", runway="26L")
    assert V.check_element(rwy, "cleared I L S approach runway two six right")


def test_validator_rejects_stray_numbers_and_wrong_callsigns(composer):
    el = Instruction(type="altitude", value=4000, verb="descend")
    cs_display, cs = composer.callsign_speech("BAW117")
    pieces = [V.Piece("element", "descend four thousand", el, ["four thousand"]),
              V.Piece("filler", ", then two"), V.Piece("callsign", cs, slot_speeches=[cs])]
    problems = V.validate("BAW117", pieces, "descend four thousand, then two" + ", " + cs, composer.tele_words, 1)
    assert any("number words differ" in p for p in problems)
    wrong = [V.Piece("element", "descend four thousand", el, ["four thousand"]),
             V.Piece("callsign", "speedbird one one nine", slot_speeches=["speedbird one one nine"])]
    assert any("does not decode" in p for p in V.validate(
        "BAW117", wrong, "descend four thousand, speedbird one one nine", composer.tele_words, 1))


def test_composition_is_deterministic_and_varied(composer):
    el = [Instruction(type="heading", value=240, turn_dir="L"), Instruction(type="altitude", value=4000, verb="descend")]
    plan = ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="full", elements=el, seed=7)
    assert Composer(DATA).compose(plan) == Composer(DATA).compose(plan)
    variants = {composer.compose(plan.model_copy(update={"seed": s})).tts_text for s in range(40)}
    assert len(variants) >= 10


def test_no_back_to_back_repeat_of_the_same_variant_per_aircraft():
    comp = Composer(DATA)
    el = [Instruction(type="heading", value=240, turn_dir="L")]
    last = None
    for seed in range(60):
        r = comp.compose(ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="full", elements=el, seed=seed))
        assert r.variant_ids[0] != last
        last = r.variant_ids[0]


def test_callsign_placement_and_element_order_distribution(composer):
    el = [Instruction(type="heading", value=240, turn_dir="L"), Instruction(type="altitude", value=4000, verb="descend")]
    cs = composer.callsign_speech("BAW117")[1]
    ends = starts = both = shuffled = 0
    n = 600
    for seed in range(n):
        r = composer.compose(ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="full", elements=el, seed=seed))
        text = r.tts_text
        starts_with, ends_with = text.startswith(cs), text.endswith(cs)
        both += starts_with and ends_with
        starts += starts_with and not ends_with
        ends += ends_with and not starts_with
        shuffled += text.index("four thousand") < text.index("two four zero")   # order flipped
    assert 0.70 < ends / n < 0.90
    assert 0.07 < starts / n < 0.25
    assert both / n < 0.12
    # 30% of readbacks are shuffled; with two elements half of those keep the original order
    assert 0.08 < shuffled / n < 0.22


@pytest.mark.parametrize("kind,kwargs", [
    ("unable", dict(reason_code="UNABLE_SPEED_LOW", reason_values={"min_speed": 210})),
    ("unable", dict(reason_code="UNABLE_NOT_MODELLED")),
    ("unable", dict(reason_code="UNABLE_TOO_HIGH_GS")),
    ("query", dict(reason_code="QUERY_AMBIGUOUS")),
    ("query", dict(reason_code="QUERY_CALLSIGN")),
    ("query", dict(reason_code="QUERY_LEVEL_TYPE")),
    ("checkin", dict(report_values={"level_fl": 90, "star": "WILLO1G", "atis": "K"})),
    ("report", dict(report_values={"heading": 240, "speed": 210, "cleared_ft": 5000})),
    ("report", dict(report_values={"cleared_fl": 80})),
    ("request", dict(report_values={"request": "direct"})),
    ("looking", {}), ("no_further_clearance", {}), ("going_around", {}), ("recall", {}),
    ("say_again_request", {}), ("established", dict(report_values={"runway": "26L"})),
    ("established", {}), ("mayday", dict(report_values={"nature": "engine failure"})),
    ("pan", {}),
])
def test_reply_kinds_compose_and_validate(composer, kind, kwargs):
    for seed in range(25):
        plan = ReadbackPlan(callsign="EZY45AB", persona="stressed", kind=kind, seed=seed, **kwargs)
        r = composer.compose(plan)
        assert r.validated and r.tts_text, (kind, r)
        assert not any(c.isdigit() for c in r.tts_text)


def test_repeat_last_without_history_stands_by(composer):
    fresh = Composer(DATA)
    r = fresh.compose(ReadbackPlan(callsign="EZY45AB", persona="crisp_airline", kind="repeat_last", seed=1))
    assert r.validated and "standby" in r.tts_text and "easy four five alpha bravo" in r.tts_text


def test_silence_and_repeat_last(composer):
    assert composer.compose(ReadbackPlan(callsign="BAW117", persona="crisp_airline", kind="silence")).tts_text == ""
    el = [Instruction(type="heading", value=240, turn_dir="L")]
    first = composer.compose(ReadbackPlan(callsign="UAE15", persona="crisp_airline", kind="full", elements=el, seed=1))
    again = composer.compose(ReadbackPlan(callsign="UAE15", persona="crisp_airline", kind="repeat_last"))
    assert again.tts_text == first.tts_text


def test_spoken_callsigns():
    comp = Composer(DATA)
    assert comp.callsign_speech("BAW117") == ("Speedbird 117", "speedbird one one seven")
    assert comp.callsign_speech("EZY45AB")[1] == "easy four five alpha bravo"
    assert comp.callsign_speech("DLH4KA")[1] == "lufthansa four kilo alpha"
    assert comp.callsign_speech("WZZ4471")[1] == "wizz air four four seven one"
