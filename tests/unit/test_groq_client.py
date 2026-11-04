from types import SimpleNamespace
from typing import Any

import groq
import httpx
import pytest

from app.config import Settings
from app.services.llm import groq_client
from app.services.llm.client import LLMAttempt, LLMUnavailable
from app.services.llm.groq_client import GroqLLMClient


class FakeCompletions:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeGroq:
    def __init__(self, results: list[Any]) -> None:
        self.completions = FakeCompletions(results)
        self.chat = SimpleNamespace(completions=self.completions)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=_request(), headers={"retry-after": "0"})


def _rate_limit() -> groq.RateLimitError:
    return groq.RateLimitError("rate limited", response=_response(429), body=None)


def _bad_request() -> groq.BadRequestError:
    return groq.BadRequestError("bad request", response=_response(400), body=None)


def _server_error() -> groq.APIStatusError:
    return groq.APIStatusError("server error", response=_response(500), body=None)


def _connection_error() -> groq.APIConnectionError:
    return groq.APIConnectionError(request=_request())


def _completion(content: str = "{}") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _client(results: list[Any], *, retries: int = 1) -> tuple[GroqLLMClient, FakeGroq]:
    client = GroqLLMClient(Settings(groq_api_key="test", llm_max_retries=retries))
    fake = FakeGroq(results)
    client.client = fake  # type: ignore[assignment]
    return client, fake


async def _no_backoff(*args: object, **kwargs: object) -> None:
    return None


async def test_retries_rate_limit_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_client, "_backoff", _no_backoff)
    client, fake = _client([_rate_limit(), _completion("ok")], retries=1)
    seen: list[LLMAttempt] = []
    before_attempts = 0

    async def before_attempt() -> None:
        nonlocal before_attempts
        before_attempts += 1

    async def on_attempt(attempt: LLMAttempt) -> None:
        seen.append(attempt)

    response = await client.complete(
        messages=[],
        schema={},
        purpose="extract",
        model="primary",
        before_attempt=before_attempt,
        on_attempt=on_attempt,
    )

    assert response.content == "ok"
    assert response.model == "primary"
    assert [attempt.outcome for attempt in response.attempts] == ["rate_limited", "ok"]
    assert [attempt.attempt for attempt in response.attempts] == [1, 2]
    assert seen == response.attempts
    assert before_attempts == 2
    assert len(fake.completions.calls) == 2


async def test_strict_bad_request_gets_best_effort_attempt_without_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(groq_client, "_backoff", _no_backoff)
    client, fake = _client([_bad_request(), _completion("ok")], retries=0)

    response = await client.complete(messages=[], schema={}, purpose="extract", model="primary")

    assert [attempt.outcome for attempt in response.attempts] == ["bad_request", "ok"]
    assert fake.completions.calls[0]["response_format"]["type"] == "json_schema"
    assert fake.completions.calls[1]["response_format"] == {"type": "json_object"}


async def test_falls_back_to_second_model_after_primary_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(groq_client, "_backoff", _no_backoff)
    client, _ = _client([_rate_limit(), _rate_limit(), _completion("fallback")], retries=1)

    response = await client.complete(
        messages=[],
        schema={},
        purpose="extract",
        model="primary",
        fallback_model="fallback",
    )

    assert response.model == "fallback"
    assert [attempt.model for attempt in response.attempts] == ["primary", "primary", "fallback"]
    assert [attempt.fallback_used for attempt in response.attempts] == [False, False, True]
    assert [attempt.outcome for attempt in response.attempts] == [
        "rate_limited",
        "rate_limited",
        "ok",
    ]


async def test_retries_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_client, "_backoff", _no_backoff)
    client, _ = _client([_connection_error(), _completion("ok")], retries=1)

    response = await client.complete(messages=[], schema={}, purpose="extract", model="primary")

    assert [attempt.outcome for attempt in response.attempts] == ["connection_error", "ok"]


async def test_raises_unavailable_with_all_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groq_client, "_backoff", _no_backoff)
    client, _ = _client([_server_error(), _server_error()], retries=1)

    with pytest.raises(LLMUnavailable) as exc:
        await client.complete(messages=[], schema={}, purpose="extract", model="primary")

    assert [attempt.outcome for attempt in exc.value.attempts] == ["http_500", "http_500"]
