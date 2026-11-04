from decimal import Decimal
from io import BytesIO

from PIL import Image
from sqlalchemy import insert

from app.db.models import Receipt
from app.db.session import engine
from app.schemas.extraction import Extracted
from app.services import pipeline
from app.services.extraction import ExtractionResult


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, format="PNG")
    return output.getvalue()


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
    assert body["data"]["vendor"] == "Blue Bottle Coffee"
    assert body["fileUrl"].endswith(f"/receipts/{body['id']}/file")


async def test_review_approval_is_one_way(client) -> None:
    processed = await client.post("/samples/corner-pharmacy/process")
    receipt = processed.json()
    assert receipt["status"] == "needs_review"

    approved = await client.post(f"/receipts/{receipt['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    again = await client.post(f"/receipts/{receipt['id']}/approve")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_state"


async def test_edit_rejected_when_not_awaiting_review(client) -> None:
    processed = await client.post("/samples/blue-bottle/process")
    receipt = processed.json()
    response = await client.patch(f"/receipts/{receipt['id']}", json={"data": receipt["data"]})
    assert response.status_code == 409


async def test_upload_rejects_unknown_file_type(client) -> None:
    response = await client.post(
        "/upload",
        files={"file": ("note.txt", b"not a receipt", "text/plain")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_file_type"


async def test_upload_dedupes_and_processes(client, monkeypatch) -> None:
    async def fake_extract(**kwargs):
        return ExtractionResult(
            data=Extracted(
                vendor="Test Vendor",
                date="2026-11-01",
                dateRaw="11/01/2026",
                currency="USD",
                subtotal=Decimal("10.00"),
                tax=Decimal("1.00"),
                total=Decimal("11.00"),
                category="other",
                lineItems=[],
            ),
            source_text=None,
        )

    monkeypatch.setattr(pipeline, "extract_receipt", fake_extract)
    content = png_bytes()

    first = await client.post("/upload", files={"file": ("receipt.png", content, "image/png")})
    assert first.status_code == 202
    assert first.json()["cached"] is False

    detail = await client.get(f"/receipts/{first.json()['id']}")
    assert detail.json()["status"] == "auto_approved"
    assert detail.json()["data"]["vendor"] == "Test Vendor"

    second = await client.post("/upload", files={"file": ("receipt.png", content, "image/png")})
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["cached"] is True


async def test_file_endpoint_serves_original_bytes(client) -> None:
    processed = await client.post("/samples/blue-bottle/process")
    receipt = processed.json()
    response = await client.get(f"/receipts/{receipt['id']}/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_list_receipts_filters_sorts_and_pages(client) -> None:
    blue = (await client.post("/samples/blue-bottle/process")).json()
    pharmacy = (await client.post("/samples/corner-pharmacy/process")).json()

    needs_review = await client.get("/receipts", params={"status": "needs_review"})
    assert [item["id"] for item in needs_review.json()["items"]] == [pharmacy["id"]]

    dining = await client.get("/receipts", params={"category": "dining"})
    assert [item["id"] for item in dining.json()["items"]] == [blue["id"]]

    by_vendor = await client.get("/receipts", params={"q": "blue bottle"})
    assert [item["id"] for item in by_vendor.json()["items"]] == [blue["id"]]
    by_filename = await client.get("/receipts", params={"q": "corner-pharmacy"})
    assert [item["id"] for item in by_filename.json()["items"]] == [pharmacy["id"]]
    no_match = await client.get("/receipts", params={"q": "does-not-exist"})
    assert no_match.json()["items"] == []
    literal_percent = await client.get("/receipts", params={"q": "%"})
    assert literal_percent.json()["items"] == []

    by_total = await client.get("/receipts", params={"sort": "total", "order": "desc"})
    assert [item["total"] for item in by_total.json()["items"]] == [22.4, 14.34]

    page = await client.get("/receipts", params={"limit": 1, "offset": 1})
    body = page.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


async def test_list_receipts_puts_missing_totals_last_descending(client) -> None:
    blue = (await client.post("/samples/blue-bottle/process")).json()
    async with engine.begin() as connection:
        await connection.execute(
            insert(Receipt).values(
                file_sha256="0" * 64,
                filename="no-total.png",
                file_type="png",
                status="needs_review",
                data={"vendor": "No Total", "category": "other"},
            )
        )

    total = blue["data"]["total"]
    ascending = await client.get("/receipts", params={"sort": "total", "order": "asc"})
    assert [item["total"] for item in ascending.json()["items"]] == [None, total]

    descending = await client.get("/receipts", params={"sort": "total", "order": "desc"})
    assert [item["total"] for item in descending.json()["items"]] == [total, None]
