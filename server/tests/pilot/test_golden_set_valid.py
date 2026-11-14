"""The parser golden set must itself be valid: contract-shaped, in scope, real fixes/runways."""
import json
from pathlib import Path

from server.contracts import Instruction, Transmission

GOLDEN = Path(__file__).resolve().parents[1] / "voice" / "golden_parse.jsonl"


def rows():
    lines = [json.loads(x) for x in GOLDEN.read_text().splitlines() if x.strip()]
    return lines[0]["_meta"], lines[1:]


def test_golden_set_is_valid(world):
    meta, cases = rows()
    scope = {c.icao for c in world.callsigns()}
    assert set(meta["scope"]) == scope
    fixes, runways = set(world.fixes()), set(world.egkk["runways"])
    assert 60 <= len(cases) <= 100
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["text"] for c in cases}) == len(cases)
    for c in cases:
        assert c["receiver"] is None or c["receiver"] in scope, c["id"]
        tx = Transmission(tx_id=c["id"], t=0, receiver=c["receiver"], intent=c["intent"],
                          instructions=c["instructions"], raw_text=c["text"],
                          self_corrections=c.get("self_corrections", 0))
        for raw, ins in zip(c["instructions"], tx.instructions):
            assert isinstance(ins, Instruction)
            assert ins.model_dump(exclude_none=True, exclude={"confidence"}) == raw, c["id"]
            assert ins.fix is None or ins.fix in fixes, c["id"]
            assert ins.runway is None or ins.runway in runways, c["id"]
            if ins.condition and ins.condition.after_fix:
                assert ins.condition.after_fix in fixes, c["id"]


def test_golden_set_covers_every_instruction_type():
    from typing import get_args

    from server.contracts import InstrType
    _, cases = rows()
    seen = {i["type"] for c in cases for i in c["instructions"]}
    assert seen == set(get_args(InstrType))
