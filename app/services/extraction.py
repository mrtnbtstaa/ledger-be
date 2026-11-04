import re
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import LLMCall
from app.schemas.extraction import Extracted
from app.services import image_prep, pdf_text
from app.services.llm import budget, prompts
from app.services.llm.client import LLMAttempt, LLMUnavailable
from app.services.llm.groq_client import GroqLLMClient


class ExtractionFailed(Exception):
    pass


@dataclass
class ExtractionResult:
    data: Extracted
    source_text: str | None


@lru_cache
def get_client() -> GroqLLMClient:
    return GroqLLMClient(get_settings())


def _json_text(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _parse(content: str) -> Extracted:
    return Extracted.model_validate_json(_json_text(content))


def _is_empty(data: Extracted) -> bool:
    return data.vendor is None and data.total is None and not data.line_items


async def extract_receipt(
    *,
    receipt_id: UUID,
    content: bytes,
    file_type: str,
    source_text: str | None,
    session: AsyncSession,
    settings: Settings,
) -> ExtractionResult:
    if not settings.groq_api_key:
        raise LLMUnavailable()

    if file_type == "pdf" and source_text is None:
        images = [
            (image_prep.normalize_image(page, "jpg"), "image/jpeg")
            for page in pdf_text.render_pages(content, settings.max_pdf_pages)
        ]
        messages = prompts.build_vision_messages(images)
        model = settings.groq_vision_model
        fallback_model = None
    elif file_type == "pdf":
        messages = prompts.build_text_messages(source_text or "")
        model = settings.groq_text_model
        fallback_model = settings.groq_fallback_model
    else:
        mime = "image/png" if file_type == "png" else "image/jpeg"
        messages = prompts.build_vision_messages([(content, mime)])
        model = settings.groq_vision_model
        fallback_model = None

    async def before_attempt() -> None:
        await budget.reserve(session, settings)

    async def on_attempt(attempt: LLMAttempt) -> None:
        session.add(
            LLMCall(
                receipt_id=receipt_id,
                purpose=attempt.purpose,
                model=attempt.model,
                prompt_tokens=attempt.prompt_tokens,
                completion_tokens=attempt.completion_tokens,
                latency_ms=attempt.latency_ms,
                attempt=attempt.attempt,
                fallback_used=attempt.fallback_used,
                outcome=attempt.outcome,
            )
        )
        await session.commit()

    client = get_client()
    response = await client.complete(
        messages=messages,
        schema=prompts.RECEIPT_SCHEMA,
        purpose="extract",
        model=model,
        fallback_model=fallback_model,
        before_attempt=before_attempt,
        on_attempt=on_attempt,
    )

    try:
        data = _parse(response.content)
    except ValidationError as exc:
        repair = await client.complete(
            messages=prompts.build_repair_messages(response.content, str(exc)),
            schema=prompts.RECEIPT_SCHEMA,
            purpose="repair",
            model=model,
            fallback_model=fallback_model,
            before_attempt=before_attempt,
            on_attempt=on_attempt,
        )
        try:
            data = _parse(repair.content)
        except ValidationError as repair_exc:
            raise ExtractionFailed("The model returned data that did not match the schema.") from repair_exc

    if _is_empty(data):
        raise ExtractionFailed("No receipt data was found in the document.")
    return ExtractionResult(data=data, source_text=source_text)
