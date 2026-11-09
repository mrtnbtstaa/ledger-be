from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from uuid import UUID, uuid4

from PIL import Image
from sqlalchemy import insert

from app.api import admin
from app.config import Settings, get_settings
from app.db.models import Receipt, SheetSync
from app.db.session import SessionLocal, engine
from app.schemas.extraction import Extracted
from app.services import pipeline, sheets
from app.services.extraction import ExtractionResult


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, format="PNG")
    return output.getvalue()


class FakeWorksheet:
    def __init__(self) -> None:
        self.rows: list[list[object]] = []

    def col_values(self, column: int) -> list[str]:
        return [str(row[0]) for row in self.rows]

    def append_row(self, row: list[object], value_input_option: str | None = None) -> None:
        self.rows.append(row)


class FailingWorksheet(FakeWorksheet):
    def append_row(self, row: list[object], value_input_option: str | None = None) -> None:
        raise RuntimeError("sheets down")


def test_sheets_credentials_accept_file_path(tmp_path) -> None:
    key = tmp_path / "credentials.json"
    key.write_text('{"type": "service_account"}', encoding="utf-8")
    settings = Settings(google_service_account_json=str(key), google_sheet_id="sheet")
    assert sheets._credentials(settings) == {"type": "service_account"}


def test_sheets_credentials_accept_json_value() -> None:
    settings = Settings(
        google_service_account_json='{"type": "service_account"}', google_sheet_id="sheet"
    )
    assert sheets._credentials(settings) == {"type": "service_account"}


async def test_auto_approved_sample_is_logged_once(client, monkeypatch) -> None:
    worksheet = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda settings: worksheet)

    processed = await client.post("/samples/blue-bottle/process")
    assert processed.status_code == 202
    assert [row[0] for row in worksheet.rows] == ["blue-bottle"]

    async with SessionLocal() as session:
        sync = await session.get(SheetSync, "blue-bottle")
        assert sync is not None
        assert sync.state == "synced"
        sync.state = "pending"
        await session.commit()
        await sheets.sync_pending(session, get_settings())

    assert [row[0] for row in worksheet.rows] == ["blue-bottle"]


async def test_needs_review_sample_is_not_synced(client, monkeypatch) -> None:
    worksheet = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda settings: worksheet)

    pharmacy = await client.post("/samples/corner-pharmacy/process")
    assert pharmacy.status_code == 202
    assert pharmacy.json()["status"] == "needs_review"
    assert worksheet.rows == []

    async with SessionLocal() as session:
        assert await session.get(SheetSync, "corner-pharmacy") is None


async def test_sheet_failure_does_not_block_sample_and_retries(client, monkeypatch) -> None:
    monkeypatch.setattr(sheets, "_worksheet", lambda settings: FailingWorksheet())

    processed = await client.post("/samples/blue-bottle/process")
    assert processed.status_code == 202
    assert processed.json()["status"] == "auto_approved"

    async with SessionLocal() as session:
        sync = await session.get(SheetSync, "blue-bottle")
        assert sync is not None
        assert sync.state == "failed"
        assert sync.attempts == 1
        assert "sheets down" in (sync.last_error or "")
        sync.updated_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    worksheet = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda settings: worksheet)
    async with SessionLocal() as session:
        assert await sheets.sync_pending(session, get_settings()) == 1

    assert [row[0] for row in worksheet.rows] == ["blue-bottle"]


async def test_maintenance_retries_sheet_sync(client, monkeypatch) -> None:
    monkeypatch.setattr(sheets, "_worksheet", lambda settings: FailingWorksheet())
    await client.post("/samples/blue-bottle/process")

    async with SessionLocal() as session:
        sync = await session.get(SheetSync, "blue-bottle")
        assert sync is not None
        sync.updated_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    worksheet = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda settings: worksheet)
    monkeypatch.setattr(admin, "get_settings", lambda: Settings(admin_token="secret"))
    response = await client.post(
        "/admin/maintenance",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    assert response.json()["sheetSynced"] == 1
    assert [row[0] for row in worksheet.rows] == ["blue-bottle"]


async def test_health(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["db"] == "ok"


async def test_samples_are_served_without_llm(client) -> None:
    response = await client.get("/samples")
    assert response.status_code == 200
    assert {sample["id"] for sample in response.json()} == {
        "blue-bottle",
        "harbor-hardware",
        "corner-pharmacy",
    }

    processed = await client.post("/samples/blue-bottle/process")
    assert processed.status_code == 202
    body = processed.json()
    assert body["status"] == "auto_approved"
    assert body["cached"] is True
    assert body["isSample"] is True
    assert body["fileUrl"] is None
    assert body["data"]["vendor"] == "Blue Bottle Coffee"

    detail = await client.get(f"/receipts/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["isSample"] is True
    assert detail.json()["fileUrl"] is None


async def test_samples_are_read_only(client, owner_headers) -> None:
    receipt = (await client.post("/samples/corner-pharmacy/process")).json()

    approved = await client.post(
        f"/receipts/{receipt['id']}/approve", headers=owner_headers
    )
    assert approved.status_code == 404
    assert approved.json()["error"]["code"] == "receipt_not_found"

    edited = await client.patch(
        f"/receipts/{receipt['id']}", json={"data": receipt["data"]}, headers=owner_headers
    )
    assert edited.status_code == 404

    deleted = await client.delete(f"/receipts/{receipt['id']}", headers=owner_headers)
    assert deleted.status_code == 404

    file_response = await client.get(
        f"/receipts/{receipt['id']}/file", headers=owner_headers
    )
    assert file_response.status_code == 404


async def test_upload_rejects_unknown_file_type(client, owner_headers) -> None:
    response = await client.post(
        "/upload",
        files={"file": ("note.txt", b"not a receipt", "text/plain")},
        headers=owner_headers,
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_file_type"


def extraction(total: str) -> ExtractionResult:
    return ExtractionResult(
        data=Extracted(
            vendor="Test Vendor",
            date="2026-11-01",
            dateRaw="11/01/2026",
            currency="USD",
            subtotal=Decimal("10.00"),
            tax=Decimal("1.00"),
            total=Decimal(total),
            category="other",
            lineItems=[],
        ),
        source_text=None,
    )


async def test_upload_dedupes_and_processes(client, owner_headers, monkeypatch) -> None:
    async def fake_extract(**kwargs):
        return extraction("11.00")

    monkeypatch.setattr(pipeline, "extract_receipt", fake_extract)
    content = png_bytes()

    first = await client.post(
        "/upload",
        files={"file": ("receipt.png", content, "image/png")},
        headers=owner_headers,
    )
    assert first.status_code == 202
    assert first.json()["cached"] is False
    assert first.json()["isSample"] is False

    detail = await client.get(f"/receipts/{first.json()['id']}", headers=owner_headers)
    assert detail.json()["status"] == "auto_approved"
    assert detail.json()["data"]["vendor"] == "Test Vendor"
    assert detail.json()["fileUrl"] is None

    second = await client.post(
        "/upload",
        files={"file": ("receipt.png", content, "image/png")},
        headers=owner_headers,
    )
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["cached"] is True


async def test_file_endpoint_serves_original_bytes(client, owner_headers, monkeypatch) -> None:
    async def fake_extract(**kwargs):
        return extraction("15.00")

    monkeypatch.setattr(pipeline, "extract_receipt", fake_extract)
    uploaded = await client.post(
        "/upload",
        files={"file": ("receipt.png", png_bytes(), "image/png")},
        headers=owner_headers,
    )
    assert uploaded.status_code == 202

    detail = await client.get(f"/receipts/{uploaded.json()['id']}", headers=owner_headers)
    body = detail.json()
    assert body["status"] == "needs_review"
    assert body["fileUrl"].endswith(f"/receipts/{body['id']}/file")

    response = await client.get(f"/receipts/{body['id']}/file", headers=owner_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


async def insert_receipts(owner_id: str, rows: list[dict]) -> None:
    async with engine.begin() as connection:
        for row in rows:
            await connection.execute(
                insert(Receipt).values(
                    user_id=UUID(owner_id),
                    is_sample=False,
                    file_sha256=uuid4().hex + uuid4().hex,
                    filename=row.get("filename", "receipt.png"),
                    file_type="png",
                    status=row.get("status", "auto_approved"),
                    data=row["data"],
                )
            )


async def test_list_receipts_filters_sorts_and_pages(client, owner_tokens, owner_headers) -> None:
    await insert_receipts(
        owner_tokens["user"]["id"],
        [
            {
                "filename": "blue.png",
                "data": {
                    "vendor": "Blue Bottle Coffee",
                    "date": "2026-11-01",
                    "category": "dining",
                    "total": 14.34,
                    "currency": "USD",
                    "lineItems": [],
                },
            },
            {
                "filename": "corner-pharmacy.png",
                "status": "needs_review",
                "data": {
                    "vendor": "Corner Pharmacy",
                    "date": "2026-11-02",
                    "category": "health",
                    "total": 22.4,
                    "currency": "USD",
                    "lineItems": [],
                },
            },
        ],
    )

    needs_review = await client.get(
        "/receipts", params={"status": "needs_review"}, headers=owner_headers
    )
    assert [item["filename"] for item in needs_review.json()["items"]] == [
        "corner-pharmacy.png"
    ]

    dining = await client.get("/receipts", params={"category": "dining"}, headers=owner_headers)
    assert [item["filename"] for item in dining.json()["items"]] == ["blue.png"]

    by_vendor = await client.get(
        "/receipts", params={"q": "blue bottle"}, headers=owner_headers
    )
    assert [item["filename"] for item in by_vendor.json()["items"]] == ["blue.png"]
    by_filename = await client.get(
        "/receipts", params={"q": "corner-pharmacy"}, headers=owner_headers
    )
    assert [item["filename"] for item in by_filename.json()["items"]] == [
        "corner-pharmacy.png"
    ]
    no_match = await client.get(
        "/receipts", params={"q": "does-not-exist"}, headers=owner_headers
    )
    assert no_match.json()["items"] == []
    literal_percent = await client.get("/receipts", params={"q": "%"}, headers=owner_headers)
    assert literal_percent.json()["items"] == []

    by_total = await client.get(
        "/receipts", params={"sort": "total", "order": "desc"}, headers=owner_headers
    )
    assert [item["total"] for item in by_total.json()["items"]] == [22.4, 14.34]

    page = await client.get(
        "/receipts", params={"limit": 1, "offset": 1}, headers=owner_headers
    )
    body = page.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


async def test_ledger_filter_returns_only_approved(client, owner_tokens, owner_headers) -> None:
    await insert_receipts(
        owner_tokens["user"]["id"],
        [
            {"status": "auto_approved", "data": {"vendor": "A", "category": "other"}},
            {"status": "approved", "data": {"vendor": "B", "category": "other"}},
            {"status": "needs_review", "data": {"vendor": "C", "category": "other"}},
            {"status": "rejected", "data": {"vendor": "D", "category": "other"}},
        ],
    )

    ledger = await client.get("/receipts", params={"ledger": True}, headers=owner_headers)
    assert ledger.json()["total"] == 2
    assert {item["vendor"] for item in ledger.json()["items"]} == {"A", "B"}


async def test_list_receipts_puts_missing_totals_last_descending(
    client, owner_tokens, owner_headers
) -> None:
    await insert_receipts(
        owner_tokens["user"]["id"],
        [
            {"data": {"vendor": "No Total", "category": "other"}},
            {"data": {"vendor": "With Total", "category": "other", "total": 12.5}},
        ],
    )

    ascending = await client.get(
        "/receipts", params={"sort": "total", "order": "asc"}, headers=owner_headers
    )
    assert [item["total"] for item in ascending.json()["items"]] == [None, 12.5]

    descending = await client.get(
        "/receipts", params={"sort": "total", "order": "desc"}, headers=owner_headers
    )
    assert [item["total"] for item in descending.json()["items"]] == [12.5, None]
