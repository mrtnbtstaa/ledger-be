from datetime import date as date_type
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class LineItemType(str, Enum):
    item = "item"
    discount = "discount"
    tip = "tip"
    fee = "fee"


class LineItemData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = Field(None, alias="unitPrice")
    line_total: Decimal | None = Field(None, alias="lineTotal")
    type: LineItemType = LineItemType.item


class Extracted(BaseModel):
    """Model output after schema validation. Money stays Decimal until the API boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    vendor: str | None = None
    date: date_type | None = None
    date_raw: str | None = Field(None, alias="dateRaw")
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    category: str | None = None
    line_items: list[LineItemData] = Field(default_factory=list, alias="lineItems")


class CheckResult(BaseModel):
    rule: str
    description: str
    passed: bool
    skipped: bool = False
    details: str | None = None


CATEGORIES = [
    "groceries",
    "dining",
    "transport",
    "utilities",
    "office_supplies",
    "software",
    "travel",
    "health",
    "entertainment",
    "other",
]
