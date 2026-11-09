from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.extraction import Extracted, LineItemData, LineItemType


class ReceiptStatus(str, Enum):
    processing = "processing"
    auto_approved = "auto_approved"
    needs_review = "needs_review"
    approved = "approved"
    rejected = "rejected"
    failed = "failed"


class ReceiptStage(str, Enum):
    uploading = "uploading"
    extracting = "extracting"
    validating = "validating"
    done = "done"


class SampleExpected(str, Enum):
    clean = "clean"
    fails_validation = "fails_validation"


class LineItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str | None
    quantity: float | None
    unit_price: float | None = Field(alias="unitPrice")
    line_total: float | None = Field(alias="lineTotal")
    type: LineItemType | None = None


class ExtractedData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    vendor: str | None
    date: date_type | None
    date_raw: str | None = Field(alias="dateRaw")
    currency: str | None
    subtotal: float | None
    tax: float | None
    total: float | None
    category: str | None
    line_items: list[LineItem] = Field(alias="lineItems")


class ValidationCheck(BaseModel):
    rule: str
    description: str
    passed: bool
    skipped: bool | None = None
    details: str | None = None


class Receipt(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    is_sample: bool = Field(alias="isSample")
    filename: str
    file_type: str = Field(alias="fileType")
    uploaded_at: datetime = Field(alias="uploadedAt")
    status: ReceiptStatus
    stage: ReceiptStage | None = None
    confidence: float | None
    failure_reason: str | None = Field(alias="failureReason")
    edited: bool | None = None
    cached: bool
    file_url: str | None = Field(alias="fileUrl")
    file_expires_at: datetime | None = Field(alias="fileExpiresAt")
    data: ExtractedData | None
    checks: list[ValidationCheck]


class ReceiptSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    filename: str
    uploaded_at: datetime = Field(alias="uploadedAt")
    vendor: str | None
    date: date_type | None
    total: float | None
    currency: str | None
    category: str | None
    status: ReceiptStatus
    confidence: float | None
    failure_reason: str | None = Field(alias="failureReason")


class ReceiptList(BaseModel):
    items: list[ReceiptSummary]
    total: int


class Sample(BaseModel):
    id: str
    label: str
    description: str
    expected: SampleExpected


class ReceiptUpdate(BaseModel):
    data: ExtractedData


class Health(BaseModel):
    status: str
    version: str
    time: datetime
    db: str


class MaintenanceResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    purged_files: int = Field(alias="purgedFiles")
    purged_records: int = Field(alias="purgedRecords")
    purged_tokens: int = Field(alias="purgedTokens")
    failed_stuck: int = Field(alias="failedStuck")
    sheet_synced: int = Field(alias="sheetSynced")


def _money(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def to_extracted_out(data: Extracted) -> ExtractedData:
    return ExtractedData(
        vendor=data.vendor,
        date=data.date,
        dateRaw=data.date_raw,
        currency=data.currency,
        subtotal=_money(data.subtotal),
        tax=_money(data.tax),
        total=_money(data.total),
        category=data.category,
        lineItems=[
            LineItem(
                description=item.description,
                quantity=float(item.quantity) if item.quantity is not None else None,
                unitPrice=_money(item.unit_price),
                lineTotal=_money(item.line_total),
                type=item.type,
            )
            for item in data.line_items
        ],
    )


def to_checks_out(checks: list[dict[str, Any]]) -> list[ValidationCheck]:
    return [ValidationCheck.model_validate(check) for check in checks]


def to_domain(data: ExtractedData) -> Extracted:
    def dec(value: float | None) -> Decimal | None:
        return Decimal(str(value)) if value is not None else None

    return Extracted(
        vendor=data.vendor,
        date=data.date,
        dateRaw=data.date_raw,
        currency=data.currency,
        subtotal=dec(data.subtotal),
        tax=dec(data.tax),
        total=dec(data.total),
        category=data.category,
        lineItems=[
            LineItemData(
                description=item.description,
                quantity=dec(item.quantity),
                unitPrice=dec(item.unit_price),
                lineTotal=dec(item.line_total),
                type=item.type or LineItemType.item,
            )
            for item in data.line_items
        ],
    )
