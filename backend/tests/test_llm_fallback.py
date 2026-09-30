"""LLM client: model fallback on quota/overload, busy signal on rate limits, Gemini reasoning switch. No network."""
import httpx
import pytest

from app.core import config
from app.services import llm


class Resp:
    def __init__(self, status, text="ok"):
        self.status_code, self._text = status, text

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("POST", "http://x"), response=httpx.Response(self.status_code))


@pytest.fixture()
def calls(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://x")
    monkeypatch.setattr(config, "LLM_MODEL", "m1")
    monkeypatch.setattr(config, "LLM_FALLBACK_MODELS", ["m2", "m3"])
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return seen


def _script(monkeypatch, seen, statuses):
    it = iter(statuses)

    def post(url, headers=None, json=None, timeout=None):
        seen.append(dict(json))
        return Resp(next(it))

    monkeypatch.setattr(llm.httpx, "post", post)


def test_falls_through_models_until_one_answers(monkeypatch, calls):
    _script(monkeypatch, calls, [429, 503, 200])
    assert llm.complete("s", "u") == "ok"
    assert [c["model"] for c in calls] == ["m1", "m2", "m3"]


def test_first_model_success_makes_one_call(monkeypatch, calls):
    _script(monkeypatch, calls, [200])
    llm.complete("s", "u")
    assert len(calls) == 1 and "reasoning_effort" not in calls[0]


def test_all_rate_limited_is_busy_and_last_model_is_retried_once(monkeypatch, calls):
    _script(monkeypatch, calls, [429, 429, 429, 429])
    with pytest.raises(llm.LLMBusy):
        llm.complete("s", "u")
    assert [c["model"] for c in calls] == ["m1", "m2", "m3", "m3"]


def test_real_errors_are_not_masked(monkeypatch, calls):
    _script(monkeypatch, calls, [401])
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.complete("s", "u")
    assert not isinstance(e.value, llm.LLMBusy) and len(calls) == 1


def test_reasoning_effort_is_sent_when_configured(monkeypatch, calls):
    monkeypatch.setattr(config, "LLM_REASONING_EFFORT", "none")
    _script(monkeypatch, calls, [200])
    llm.complete("s", "u")
    assert calls[0]["reasoning_effort"] == "none"
