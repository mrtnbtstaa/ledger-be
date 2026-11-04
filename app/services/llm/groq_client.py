import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import groq

from app.config import Settings
from app.services.llm.client import LLMAttempt, LLMResponse, LLMUnavailable


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("retry-after")
        if value:
            try:
                return min(float(value), 10.0)
            except ValueError:
                return None
    return None


async def _backoff(attempt: int, exc: Exception | None = None) -> None:
    delay = _retry_after(exc) if exc is not None else None
    if delay is None:
        delay = min(2**attempt + random.random(), 8.0)
    await asyncio.sleep(delay)


class GroqLLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = groq.AsyncGroq(
            api_key=settings.groq_api_key or "missing",
            timeout=settings.llm_timeout_seconds,
        )

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        purpose: str,
        model: str,
        fallback_model: str | None = None,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
        on_attempt: Callable[[LLMAttempt], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        models = [model] + ([fallback_model] if fallback_model and fallback_model != model else [])
        attempts: list[LLMAttempt] = []
        attempt_number = 0
        strict = True

        for model_index, current_model in enumerate(models):
            retry = 0
            while retry <= self.settings.llm_max_retries:
                attempt_number += 1
                if before_attempt is not None:
                    await before_attempt()

                response_format: dict[str, Any]
                if strict:
                    response_format = {
                        "type": "json_schema",
                        "json_schema": {"name": "receipt", "strict": True, "schema": schema},
                    }
                else:
                    response_format = {"type": "json_object"}

                started = time.perf_counter()

                async def record(
                    outcome: str,
                    *,
                    prompt_tokens: int | None = None,
                    completion_tokens: int | None = None,
                    current_model: str = current_model,
                    attempt_number: int = attempt_number,
                    model_index: int = model_index,
                    started: float = started,
                ) -> None:
                    attempt = LLMAttempt(
                        model=current_model,
                        purpose=purpose,
                        attempt=attempt_number,
                        fallback_used=model_index > 0,
                        outcome=outcome,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                    attempts.append(attempt)
                    if on_attempt is not None:
                        await on_attempt(attempt)

                try:
                    completion = await self.client.chat.completions.create(  # type: ignore[call-overload]
                        model=current_model,
                        messages=messages,
                        response_format=response_format,
                        temperature=0,
                        max_completion_tokens=2048,
                    )
                except groq.RateLimitError as exc:
                    await record("rate_limited")
                    if retry < self.settings.llm_max_retries:
                        await _backoff(retry, exc)
                        retry += 1
                        continue
                    break
                except groq.BadRequestError as exc:
                    await record("bad_request")
                    if strict:
                        # Strict schema may be unsupported; try best-effort JSON once outside retries.
                        strict = False
                        continue
                    if retry < self.settings.llm_max_retries:
                        await _backoff(retry, exc)
                        retry += 1
                        continue
                    break
                except groq.APIStatusError as exc:
                    await record(f"http_{exc.status_code}")
                    if exc.status_code >= 500 and retry < self.settings.llm_max_retries:
                        await _backoff(retry, exc)
                        retry += 1
                        continue
                    break
                except (groq.APIConnectionError, groq.APITimeoutError) as exc:
                    await record("connection_error")
                    if retry < self.settings.llm_max_retries:
                        await _backoff(retry, exc)
                        retry += 1
                        continue
                    break
                except Exception:
                    await record("error")
                    break

                await record(
                    "ok",
                    prompt_tokens=getattr(completion.usage, "prompt_tokens", None),
                    completion_tokens=getattr(completion.usage, "completion_tokens", None),
                )
                content = completion.choices[0].message.content or ""
                return LLMResponse(content=content, model=current_model, attempts=attempts)

        raise LLMUnavailable(attempts)
