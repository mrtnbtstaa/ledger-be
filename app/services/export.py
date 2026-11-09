import csv
from io import BytesIO, StringIO
from typing import Any

from openpyxl import Workbook  # type: ignore[import-untyped]

from app.db.models import Receipt
from app.schemas.extraction import Extracted

_HEADERS = [
    "id",
    "uploadedAt",
    "status",
    "vendor",
    "date",
    "category",
    "currency",
    "subtotal",
    "tax",
    "total",
    "confidence",
    "filename",
]


def escape_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _rows(receipts: list[Receipt]) -> list[list[str]]:
    rows = [list(_HEADERS)]
    for receipt in receipts:
        data = Extracted.model_validate(receipt.data) if receipt.data else None
        rows.append(
            [
                escape_cell(receipt.id),
                escape_cell(receipt.uploaded_at.isoformat()),
                escape_cell(receipt.status),
                escape_cell(data.vendor if data else None),
                escape_cell(data.date.isoformat() if data and data.date else None),
                escape_cell(data.category if data else None),
                escape_cell(data.currency if data else None),
                escape_cell(data.subtotal if data else None),
                escape_cell(data.tax if data else None),
                escape_cell(data.total if data else None),
                escape_cell(receipt.confidence),
                escape_cell(receipt.filename),
            ]
        )
    return rows


def to_csv(receipts: list[Receipt]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerows(_rows(receipts))
    return output.getvalue().encode("utf-8")


def to_xlsx(receipts: list[Receipt]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ledger"
    for row in _rows(receipts):
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
