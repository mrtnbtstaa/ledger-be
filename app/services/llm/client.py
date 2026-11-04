from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMAttempt:
    model: str
    purpose: str
    attempt: int
    fallback_used: bool
    outcome: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class LLMResponse:
    content: str
    model: str
    attempts: list[LLMAttempt] = field(default_factory=list)


class LLMUnavailable(Exception):
    def __init__(self, attempts: list[LLMAttempt] | None = None) -> None:
        super().__init__("LLM unavailable")
        self.attempts = attempts or []


class LLMQuotaExhausted(Exception):
    pass


class LLMClient(Protocol):
    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        purpose: str,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
        on_attempt: Callable[[LLMAttempt], Awaitable[None]] | None = None,
    ) -> LLMResponse: ...
