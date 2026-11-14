"""Parser golden-set harness (golden_parse.jsonl is Tudor's: read only, never edited here)."""
import json
from collections import Counter
from pathlib import Path

import pytest

from server.voice.parser import parse

GOLDEN = Path(__file__).with_name("golden_parse.jsonl")
COMPARE = ("type", "value", "turn_dir", "verb", "constraint", "approach_type", "runway",
           "fix", "unit", "frequency", "condition", "expect_what", "report_what")
MIN_ACCURACY = 0.90


def load_cases():
    cases = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "_meta" not in row:
            cases.append(row)
    return cases


def project(instr) -> dict:
    out = {}
    for field in COMPARE:
        value = getattr(instr, field)
        if field == "condition" and value is not None:
            value = value.model_dump(exclude_none=True)
        if isinstance(value, float) and value == int(value):
            value = int(value)
        out[field] = value
    return out


def expected_projection(instr: dict) -> dict:
    return {field: instr.get(field) for field in COMPARE}


def diff(case: dict, tx) -> list[str]:
    problems = []
    if tx.receiver != case["receiver"]:
        problems.append(f"receiver {tx.receiver!r} != {case['receiver']!r}")
    if tx.intent != case["intent"]:
        problems.append(f"intent {tx.intent!r} != {case['intent']!r}")
    if tx.self_corrections != case.get("self_corrections", 0):
        problems.append(f"self_corrections {tx.self_corrections} != {case.get('self_corrections', 0)}")
    got = [project(i) for i in tx.instructions]
    want = [expected_projection(i) for i in case["instructions"]]
    if got != want:
        problems.append(f"instructions {got} != {want}")
    return problems


CASES = load_cases()


def test_golden_set_is_loaded():
    assert len(CASES) >= 60


def test_golden_accuracy_and_confusion_report(capsys):
    failures, confusion = [], Counter()
    for case in CASES:
        problems = diff(case, parse(case["text"]))
        if problems:
            failures.append((case["id"], case["text"], problems))
            confusion.update(case.get("tags", ["untagged"]))
    accuracy = 1 - len(failures) / len(CASES)
    report = "\n".join(f"{i} | {text}\n    " + "\n    ".join(p) for i, text, p in failures)
    with capsys.disabled():
        print(f"\nparser golden accuracy: {accuracy:.1%} ({len(CASES) - len(failures)}/{len(CASES)})")
        if failures:
            print("failures by tag:", dict(confusion))
            print(report)
    assert accuracy >= MIN_ACCURACY, report


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case):
    problems = diff(case, parse(case["text"]))
    assert not problems, "; ".join(problems)


def test_parse_is_fast_enough_for_the_regex_path():
    tx = parse("speedbird one one seven turn left heading two four zero descend altitude four thousand feet")
    assert tx.timings["parse_s"] < 0.05


# "degrees" only makes a relative turn when a direction is given (Tudor, 14 Nov)
@pytest.mark.parametrize("text, expected", [
    ("speedbird one one seven heading one two zero degrees", [("heading", 120, None)]),
    ("Speedbird 117, fly heading 300 degrees", [("heading", 300, None)]),
    ("speedbird one one seven turn one two zero degrees", [("heading", 120, None)]),
    ("speedbird one one seven turn left heading two four zero degrees", [("heading", 240, "L")]),
    ("speedbird one one seven turn right two zero degrees", [("turn_by", 20, "R")]),
    ("speedbird one one seven turn left ten degrees", [("turn_by", 10, "L")]),
    ("speedbird 117 head 120", [("heading", 120, None)]),
])
def test_degrees_without_direction_is_an_absolute_heading(text, expected):
    tx = parse(text)
    assert tx.receiver == "BAW117" and not tx.ambiguities
    assert [(i.type, i.value, i.turn_dir) for i in tx.instructions] == expected


# navaids are spoken by station name: "Seaford" = SFD (Tudor, 15 Nov; data/spoken_names.json)
@pytest.mark.parametrize("text, kind, fix", [
    ("speedbird one one seven proceed direct seaford", "direct", "SFD"),
    ("speedbird one one seven direct midhurst", "direct", "MID"),
    ("speedbird one one seven hold at goodwood", "hold", "GWC"),
    ("speedbird one one seven direct sfd", "direct", "SFD"),
])
def test_navaid_station_names(text, kind, fix):
    tx = parse(text)
    assert [(i.type, i.fix) for i in tx.instructions] == [(kind, fix)] and not tx.ambiguities
