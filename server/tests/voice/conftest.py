"""Voice tests are strictly offline: API keys from a developer's .env must never leak into them."""
import pytest

KEYS = ("ELEVENLABS_API_KEY", "ANTHROPIC_API_KEY", "FAL_KEY")


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    from server.agents import llm
    monkeypatch.setattr(llm, "_client", None)
