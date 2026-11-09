from pydantic import BaseModel

from app.schemas.receipt import ExtractedData, ValidationCheck


class ValidateRequest(BaseModel):
    data: ExtractedData


class ValidationResult(BaseModel):
    checks: list[ValidationCheck]
    confidence: float
