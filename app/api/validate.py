from fastapi import APIRouter, Request

from app.config import get_settings
from app.core.rate_limit import limiter
from app.schemas.receipt import to_checks_out, to_domain
from app.schemas.validation import ValidateRequest, ValidationResult
from app.services import confidence, validation

router = APIRouter(tags=["validation"])


@router.post("/validate", response_model=ValidationResult)
@limiter.limit(get_settings().rate_limit_auth)
async def validate(request: Request, body: ValidateRequest) -> ValidationResult:
    data = to_domain(body.data)
    checks = validation.run_checks(data, None)
    score = confidence.compute_confidence(checks, data, False)
    return ValidationResult(
        checks=to_checks_out([check.model_dump() for check in checks]),
        confidence=score,
    )
