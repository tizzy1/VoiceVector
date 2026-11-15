"""Trained span tagger behind ML_PARSER (CUSTOM_PARSER_MODEL.md §5).

Model-dependent tests are skipped when models/crf.pkl or sklearn-crfsuite is not present
(models/ is gitignored); the flag-off and never-crash tests always run.
"""
import importlib.util
import json
import statistics
import time
from pathlib import Path

import pytest

from server.voice import ml_tagger
from server.voice.parser import parse

GOLDEN = Path(__file__).with_name("golden_parse.jsonl")
CASES = [json.loads(x) for x in GOLDEN.read_text().splitlines()][1:]
HAS_MODEL = ml_tagger.DEFAULT_MODEL.exists() and importlib.util.find_spec("sklearn_crfsuite")
needs_model = pytest.mark.skipif(not HAS_MODEL, reason="models/crf.pkl not trained here")


def _snapshot(tx):
    return (tx.receiver, tx.intent, tx.self_corrections, tuple(tx.ambiguities),
            tuple(i.model_dump_json() for i in tx.instructions))


def _matches(case, tx):
    want = [{k: v for k, v in i.items()} for i in case["instructions"]]
    got = [i.model_dump(exclude_none=True, exclude={"confidence", "span"}) for i in tx.instructions]
    return tx.receiver == case["receiver"] and tx.intent == case["intent"] and got == want


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ML_PARSER", raising=False)
    monkeypatch.delenv("ML_MODEL", raising=False)
    ml_tagger.reset()
    yield
    ml_tagger.reset()


def test_flag_off_is_identical_to_the_regex_parser(monkeypatch):
    before = [_snapshot(parse(c["text"])) for c in CASES]
    monkeypatch.setenv("ML_PARSER", "0")
    after = [_snapshot(parse(c["text"])) for c in CASES]
    assert before == after
    assert ml_tagger._TAGGER is None                     # the model is never even loaded


def test_missing_model_never_crashes_and_falls_back(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("ML_PARSER", "1")
    monkeypatch.setenv("ML_MODEL", str(tmp_path / "nope.pkl"))
    ml_tagger.reset()
    for c in CASES[:20]:
        tx = parse(c["text"])
        assert _snapshot(tx) == _snapshot(_regex_only(monkeypatch, c["text"]))
        monkeypatch.setenv("ML_PARSER", "1")
    assert "ML parser disabled" in caplog.text


def _regex_only(monkeypatch, text):
    monkeypatch.setenv("ML_PARSER", "0")
    return parse(text)


@needs_model
def test_flag_on_golden_set_has_no_regressions(monkeypatch):
    monkeypatch.setenv("ML_PARSER", "1")
    failures = [c["id"] for c in CASES if not _matches(c, parse(c["text"]))]
    assert failures == []


@needs_model
def test_flag_on_records_the_decision(monkeypatch):
    monkeypatch.setenv("ML_PARSER", "1")
    tx = parse("speedbird one one seven turn left heading two four zero")
    assert "ml_conf" in tx.timings and tx.timings["ml_valid"] == 1.0
    assert tx.timings["parser_ml"] == 1.0                # model and rules agree


@needs_model
def test_fabricated_out_of_range_span_is_rejected():
    from server.voice.parser import default_fixes
    from server.voice.parser.callsign import DEMO_CALLSIGNS
    tokens = "speedbird one one seven heading four hundred".split()
    spans = [("CALLSIGN", 0, 4), ("HEADING", 4, 7)]
    valid, _, _ = ml_tagger.spans_to_parse(tokens, spans, DEMO_CALLSIGNS, set(default_fixes()))
    assert not valid
    bad_label = [("CALLSIGN", 0, 4), ("SPEED", 4, 8)]    # a heading labelled as a speed
    tokens = "speedbird one one seven heading two four zero".split()
    valid, _, _ = ml_tagger.spans_to_parse(tokens, bad_label, DEMO_CALLSIGNS, set(default_fixes()))
    assert not valid


@needs_model
def test_tag_latency_p50_under_15_ms():
    tagger = ml_tagger.MLTagger()
    lines = [" ".join(parse(c["text"]).norm_text.split()) for c in CASES] * 5
    times = []
    for line in lines:
        s = time.perf_counter()
        tagger.tag(line.split())
        times.append((time.perf_counter() - s) * 1000)
    assert statistics.median(times) < 15.0


@needs_model
def test_model_rescues_a_line_the_rules_cannot_resolve(monkeypatch):
    """A callsign said in a form the resolver only half-matches is still a regex miss;
    the model proposes spans, the grammar validates them, and nothing unvalidated gets through."""
    monkeypatch.setenv("ML_PARSER", "1")
    for c in CASES:
        tx = parse(c["text"])
        for i in tx.instructions:
            assert not __import__("server.voice.parser.grammar",
                                  fromlist=["out_of_range"]).out_of_range(i)
