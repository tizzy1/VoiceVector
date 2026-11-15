"""Within-sentence pacing (server/voice/prosody.py): slow spots that never change what is said."""
import random
import re

import pytest

from server.pilot.readback import Composer
from server.tests.voice.test_composer import CALLSIGNS, DATA, ELEMENT_TYPES, PERSONAS, plan_for, random_element
from server.voice.captions import spoken_word_times
from server.voice.parser import parse
from server.voice.prosody import PROFILES, compensated_speed, restore_word_times, shape


@pytest.fixture(scope="module")
def composer():
    return Composer(DATA)


def strip_markup(text: str) -> str:
    """What a listener transcribing the shaped speech would write down."""
    text = re.sub(r"\s*<break[^>]*/>", "", text)
    return text.replace("...", "")


def words_of(text: str) -> list[str]:
    return [w for w in text.replace(",", "").split() if w not in ("uh", "um", "er")]


def corpus(composer, n=1500, seed=11):
    rng = random.Random(seed)
    for _ in range(n):
        elements = [random_element(rng, rng.choice(ELEMENT_TYPES)) for _ in range(rng.randint(1, 3))]
        plan = plan_for(rng, elements)
        readback = composer.compose(plan)
        if readback.tts_text:
            yield plan, readback


def key_of(instructions):
    return tuple((i.type, i.value, i.turn_dir, i.fix, i.runway, i.frequency, i.unit) for i in instructions)


def key(tx):
    return (tx.receiver, key_of(tx.instructions))


def test_shaping_never_changes_the_words_or_what_the_controller_would_parse(composer):
    changed = 0
    for plan, readback in corpus(composer):
        shaped = shape(readback.tts_text, plan.persona, seed=plan.seed,
                       callsign_speech=composer.callsign_speech(plan.callsign)[1])
        heard = strip_markup(shaped.text)
        assert words_of(heard) == words_of(readback.tts_text), shaped.text
        times = [k * 0.01 for k in range(len(shaped.text))]
        restored = restore_word_times(readback.tts_text, spoken_word_times(shaped.text, list(shaped.text), times, times))
        assert restored and [w for w, _, _ in restored] == readback.tts_text.split(), shaped.text
        got, base = key(parse(heard)), key(parse(readback.tts_text))
        if got != base:                            # allowed only if the slow spot did not make it parse worse
            want = set(key_of(plan.elements))
            assert len(want & set(got[1])) >= len(want & set(base[1])), (shaped.text, readback.tts_text)
        changed += shaped.text != readback.tts_text
    assert changed > 500, "most transmissions get at least one slow spot"


def test_callsign_is_never_slowed_and_shaping_is_deterministic(composer):
    for plan, readback in corpus(composer, 600):
        cs = composer.callsign_speech(plan.callsign)[1]
        a = shape(readback.tts_text, plan.persona, seed=plan.seed, callsign_speech=cs)
        assert a == shape(readback.tts_text, plan.persona, seed=plan.seed, callsign_speech=cs)
        if cs in readback.tts_text:
            assert cs in a.text, a.text                # no "...", comma or break inside the callsign
        assert len(a.moves) <= PROFILES[plan.persona]["max"]
        assert not re.search(r"\b(left|right|contact|hold|on|to)\.\.\.", a.text), a.text
        if cs in readback.tts_text and not readback.tts_text.startswith(cs) and "filler" in a.moves:
            head, _, tail = a.text.partition(cs)
            assert tail.count("uh,") <= readback.tts_text.partition(cs)[2].count("uh,"), a.text


def test_relaxed_and_stressed_crews_hesitate_more_than_rushed_ones(composer):
    """(Students' own templates already say "uh" and run as one clause; their slowness is TARGET_WPS.)"""
    moves = {p: 0 for p in PERSONAS}
    rng = random.Random(3)
    for _ in range(800):
        elements = [random_element(rng, rng.choice(["heading", "altitude", "qnh", "squawk", "contact"]))
                    for _ in range(2)]
        for persona in PERSONAS:
            plan = plan_for(random.Random(rng.random()), elements, persona=persona, callsign="BAW117", seed=7)
            rb = composer.compose(plan)
            moves[persona] += shape(rb.tts_text, persona, seed=7, callsign_speech="speedbird one one seven").extra_s
    assert moves["calm_longhaul"] > moves["crisp_airline"] > moves["rushed"] > 0
    assert moves["stressed"] > moves["crisp_airline"] and moves["student"] > moves["rushed"]
    fillers = sum(m == "filler" for m in shape("altitude four thousand, heading two four zero, speedbird one one seven",
                                               "crisp_airline", seed=1).moves)
    assert fillers <= 1


def test_word_times_of_shaped_speech_map_back_onto_the_plain_words():
    plain = "descend altitude four thousand feet, QNH one zero one three, speedbird one one seven"
    shaped = 'descend... altitude <break time="0.20s"/> four thousand feet, uh, QNH one zero, one three, speedbird one one seven'
    times = [k * 0.05 for k in range(len(shaped))]
    spoken = spoken_word_times(shaped, list(shaped), times, [t + 0.04 for t in times])
    restored = restore_word_times(plain, spoken)
    assert [w for w, _, _ in restored] == plain.split()
    assert restored[0][1] == 0 and restored[2][1] == pytest.approx(shaped.index("four") * 0.05)
    assert restore_word_times("something else entirely", spoken) is None


def test_slow_spots_are_paid_back_by_a_slightly_quicker_rest():
    base = {"speed": 1.05, "stability": 0.4}
    assert compensated_speed(base, 12, 3.9, 0.0) == base
    faster = compensated_speed(base, 12, 3.9, 0.3)
    assert 1.05 < faster["speed"] <= 1.05 * 1.12 and faster["stability"] == 0.4
    assert compensated_speed({"speed": 1.19}, 4, 3.9, 2.0)["speed"] == 1.2


@pytest.mark.asyncio
async def test_pipeline_sends_shaped_text_only_to_prosody_engines_and_keeps_captions_plain():
    from server.tests.voice.fakes import FakeTimedTTS
    from server.tests.voice.test_pipeline import make, type_and_settle
    for seed in range(12):                     # find a seed whose readback gets shaped
        pipe, world, pilot, hub, bus, _ = make(tts=FakeTimedTTS(chunks=3, prosody=True))
        pipe.pilot.seed = seed if hasattr(pipe.pilot, "seed") else None
        await type_and_settle(pipe, hub, f"speedbird one one seven turn left heading two four zero descend altitude {seed % 4 + 3} thousand")
        sent = pipe.tts.requests[0][0]
        text = hub.captions("pilot_text")[0].text
        words = hub.captions("pilot_words")[0].words
        assert [w.w for w in words] == text.split()
        assert "<" not in text and "..." not in text
        if sent != strip_markup(sent) or "," in sent:
            numeric = next(w for w in words if any(c.isdigit() for c in w.w))
            assert numeric.t1 > numeric.t0
    plain = make(tts=FakeTimedTTS(chunks=3))
    await type_and_settle(plain[0], plain[3])
    assert "..." not in plain[0].tts.requests[0][0] and "<break" not in plain[0].tts.requests[0][0]
