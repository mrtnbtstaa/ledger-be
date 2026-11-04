from dataclasses import dataclass
from datetime import date
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.config import get_settings
from app.schemas.extraction import CheckResult, Extracted, LineItemData
from app.schemas.receipt import Sample, SampleExpected
from app.services import confidence, money, validation


@dataclass(frozen=True)
class SampleSpec:
    id: str
    label: str
    description: str
    expected: SampleExpected
    data: Extracted


def _item(description: str, quantity: str, unit: str, total: str) -> LineItemData:
    return LineItemData(
        description=description,
        quantity=money.parse_decimal(quantity),
        unitPrice=money.parse_decimal(unit),
        lineTotal=money.parse_decimal(total),
    )


SAMPLES: dict[str, SampleSpec] = {
    "blue-bottle": SampleSpec(
        id="blue-bottle",
        label="Blue Bottle Coffee",
        description="Clean photo. Every check passes, so it is auto-approved.",
        expected=SampleExpected.clean,
        data=Extracted(
            vendor="Blue Bottle Coffee",
            date=date(2026, 10, 28),
            dateRaw="10/28/2026",
            currency="USD",
            subtotal=money.parse_decimal("13.25"),
            tax=money.parse_decimal("1.09"),
            total=money.parse_decimal("14.34"),
            category="dining",
            lineItems=[
                _item("Flat white", "2", "4.50", "9.00"),
                _item("Almond croissant", "1", "4.25", "4.25"),
            ],
        ),
    ),
    "harbor-hardware": SampleSpec(
        id="harbor-hardware",
        label="Harbor Hardware",
        description="The printed total disagrees with subtotal plus tax.",
        expected=SampleExpected.fails_validation,
        data=Extracted(
            vendor="Harbor Hardware",
            date=date(2026, 10, 30),
            dateRaw="30.10.2026",
            currency="USD",
            subtotal=money.parse_decimal("13.97"),
            tax=money.parse_decimal("1.26"),
            total=money.parse_decimal("16.35"),
            category="office_supplies",
            lineItems=[
                _item("Wood screws, 40 mm", "1", "6.99", "6.99"),
                _item("Sandpaper, 120 grit", "2", "3.49", "6.98"),
            ],
        ),
    ),
    "corner-pharmacy": SampleSpec(
        id="corner-pharmacy",
        label="Corner Pharmacy",
        description="Tax is missing, so the total cannot be verified.",
        expected=SampleExpected.fails_validation,
        data=Extracted(
            vendor="Corner Pharmacy",
            date=date(2026, 11, 2),
            dateRaw="Nov 2, 2026",
            currency="USD",
            subtotal=money.parse_decimal("22.40"),
            tax=None,
            total=money.parse_decimal("22.40"),
            category="health",
            lineItems=[_item("Ibuprofen 200 mg", "1", "22.40", "22.40")],
        ),
    ),
}


def public_samples() -> list[Sample]:
    return [
        Sample(id=spec.id, label=spec.label, description=spec.description, expected=spec.expected)
        for spec in SAMPLES.values()
    ]


def golden(sample_id: str) -> tuple[Extracted, list[CheckResult], float, str] | None:
    spec = SAMPLES.get(sample_id)
    if spec is None:
        return None
    checks = validation.run_checks(spec.data, None)
    # A missing tax line cannot be verified. Keep that visible instead of silently skipping it.
    if spec.data.tax is None:
        checks = [
            CheckResult(
                rule="subtotal_plus_tax_equals_total",
                description="Subtotal plus tax equals the total",
                passed=False,
                details="Tax was not found, so the total cannot be verified.",
            )
            if check.rule == "subtotal_plus_tax_equals_total"
            else check
            for check in checks
        ]
    score = confidence.compute_confidence(checks, spec.data, False)
    status = confidence.route(checks, score, get_settings().auto_approve_threshold)
    return spec.data, checks, score, status


def sample_image(sample_id: str) -> bytes:
    spec = SAMPLES[sample_id]
    data = spec.data
    image = Image.new("RGB", (620, 820), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=22)
        small = ImageFont.load_default(size=18)
    except TypeError:  # Pillow < 10
        font = ImageFont.load_default()
        small = font

    y = 40
    draw.text((40, y), data.vendor or "Receipt", fill="black", font=font)
    y += 40
    draw.text(
        (40, y), data.date_raw or (data.date.isoformat() if data.date else ""), fill="black", font=small
    )
    y += 60
    for item in data.line_items:
        amount = money.format_money(item.line_total, data.currency)
        draw.text((40, y), f"{item.description}  x{item.quantity}", fill="black", font=small)
        draw.text((480, y), amount, fill="black", font=small)
        y += 34
    y += 20
    for label, value in (
        ("Subtotal", data.subtotal),
        ("Tax", data.tax),
        ("Total", data.total),
    ):
        draw.text((340, y), label, fill="black", font=small)
        draw.text((480, y), money.format_money(value, data.currency), fill="black", font=small)
        y += 34

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
