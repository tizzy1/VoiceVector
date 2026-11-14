"""Debrief narrative guard rails (offline; the Anthropic client is faked)."""
import json
from types import SimpleNamespace

import pytest

from server.agents.narrative import Narrative, compact, deterministic, validate
from server.contracts import ErrorRecord, Finding, Report


def report() -> Report:
    return Report(
        session_id="S1", duration_s=1260.0, score=72, score_breakdown={"safety": -10, "catches": 6},
        findings=[
            Finding(id="F1", rule_id="A1", category="A_SAFETY", severity="HIGH", t=412.0, actor="CONTROLLER",
                    callsigns=["BAW117", "EZY45AB"], title="Loss of separation: BAW117 / EZY45AB",
                    detail="Minimum 2.1 NM / 400 ft", correct_practice="Plan descent below converging traffic"),
            Finding(id="F2", rule_id="D1", category="D_READBACK", severity="POSITIVE", t=300.0, actor="CONTROLLER",
                    callsigns=["DLH4KA"], title="Readback error caught", detail="Altitude corrected",
                    correct_practice=""),
        ],
        errors=[ErrorRecord(id="E1", callsign="DLH4KA", kind="wrong_value_adjacent", mode="consistent",
                            element_type="altitude", intended=4000, readback_value=5000, flown_value=5000,
                            t_planned=290.0, outcome="caught", caught_by="readback_correction")],
        stats={"landings": 4, "transmissions": 31}, timeline=[])


class FakeClient:
    def __init__(self, output=None, error=None):
        self.output, self.error, self.calls = output, error, []
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.output if isinstance(self.output, str)
                                                         else json.dumps(self.output))])


GOOD = {"narrative": "You scored 72 and handled most traffic well, but separation between BAW117 and EZY45AB "
                     "dropped to 2.1 NM [F1]. Catching the altitude readback error was excellent [F2].",
        "coaching": ["Plan descents below converging traffic [F1].", "Keep checking readbacks as you did [F2].",
                     "Scan for conflicts before each new clearance."],
        "cited_finding_ids": ["F1", "F2"]}


@pytest.mark.asyncio
async def test_good_model_output_is_used_and_only_narrative_and_coaching_change():
    client = FakeClient(GOOD)
    original = report()
    result = await Narrative(client=client).write(original)
    assert result.narrative == GOOD["narrative"] and result.coaching == GOOD["coaching"]
    assert result.model_copy(update={"narrative": None, "coaching": []}) == original   # nothing else touched
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["thinking"] == {"type": "disabled"}
    assert "tool_choice" not in call and "tools" not in call and call["max_tokens"] <= 500
    assert "F1" in call["messages"][0]["content"] and "Do not invent" in call["system"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {**GOOD, "narrative": GOOD["narrative"] + " Landing rate was 7 per hour."},          # number not in the report
    {**GOOD, "narrative": GOOD["narrative"] + " See [F9]."},                             # unknown finding
    {**GOOD, "narrative": "word " * 200},                                                # too long
    {**GOOD, "coaching": ["only one"]},                                                  # wrong count
    {**GOOD, "coaching": ["x " * 60, "b", "c"]},                                         # coaching too long
    {**GOOD, "cited_finding_ids": ["F7"]},
    {},
    "I cannot comply with that.",                                                       # not JSON at all
])
async def test_unsafe_model_output_falls_back_to_the_deterministic_summary(bad):
    result = await Narrative(client=FakeClient(bad)).write(report())
    text, coaching = deterministic(report())
    assert result.narrative == text and result.coaching == coaching
    assert "72" in result.narrative and len(result.coaching) == 3


@pytest.mark.asyncio
async def test_api_failure_never_raises():
    result = await Narrative(client=FakeClient(error=TimeoutError("slow"))).write(report())
    assert result.narrative and len(result.coaching) == 3


@pytest.mark.asyncio
async def test_no_key_uses_the_deterministic_summary():
    result = await Narrative().write(report())          # conftest removes every API key
    assert result.narrative.startswith("Session score 72 out of 100.")
    assert result.coaching[0] == "Plan descent below converging traffic"


def test_validate_accepts_numbers_that_are_in_the_report():
    source = compact(report())
    assert validate(GOOD, source) is None
    assert "numbers not in the report" in validate({**GOOD, "narrative": "You made 99 transmissions [F1]."}, source)


def test_deterministic_summary_for_a_clean_session():
    clean = report().model_copy(update={"findings": [], "score": 100})
    text, coaching = deterministic(clean)
    assert "100" in text and "No controller findings" in text and len(coaching) == 3


@pytest.mark.asyncio
async def test_json_wrapped_in_a_code_fence_is_accepted():
    fenced = "Here you go:\n```json\n" + json.dumps(GOOD) + "\n```"
    result = await Narrative(client=FakeClient(fenced)).write(report())
    assert result.narrative == GOOD["narrative"]


@pytest.mark.asyncio
async def test_a_slow_model_is_abandoned_before_the_session_deadline(monkeypatch):
    import asyncio
    from server.agents import narrative

    class Slow(FakeClient):
        async def create(self, **kwargs):
            await asyncio.sleep(5)

    monkeypatch.setattr(narrative, "DEADLINE_S", 0.05)
    result = await Narrative(client=Slow(GOOD)).write(report())
    assert result.narrative.startswith("Session score 72")


def test_sonnet_keeps_default_thinking_and_haiku_disables_it():
    from server.agents import llm
    assert llm.model_kwargs("claude-haiku-5-5") == {"thinking": {"type": "disabled"}}
    assert llm.model_kwargs("claude-sonnet-5-5") == {}
