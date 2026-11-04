from decimal import Decimal

from app.schemas.extraction import Extracted, LineItemData
from app.services.validation import run_checks


def receipt(**overrides) -> Extracted:
    data = {
        "vendor": "Blue Bottle Coffee",
        "date": "2026-10-28",
        "dateRaw": "10/28/2026",
        "currency": "USD",
        "subtotal": Decimal("13.25"),
        "tax": Decimal("1.09"),
        "total": Decimal("14.34"),
        "category": "dining",
        "lineItems": [
            LineItemData(
                description="Flat white",
                quantity=Decimal("2"),
                unitPrice=Decimal("4.50"),
                lineTotal=Decimal("9.00"),
            ),
            LineItemData(
                description="Almond croissant",
                quantity=Decimal("1"),
                unitPrice=Decimal("4.25"),
                lineTotal=Decimal("4.25"),
            ),
        ],
    }
    data.update(overrides)
    return Extracted(**data)


def by_rule(checks, rule):
    return next(check for check in checks if check.rule == rule)


def test_consistent_receipt_passes_arithmetic() -> None:
    checks = run_checks(receipt(), None)
    for rule in (
        "line_items_sum_to_subtotal",
        "subtotal_plus_tax_equals_total",
        "line_math_consistent",
        "amounts_non_negative",
    ):
        assert by_rule(checks, rule).passed, rule


def test_total_mismatch_fails() -> None:
    checks = run_checks(receipt(total=Decimal("15.00")), None)
    assert not by_rule(checks, "subtotal_plus_tax_equals_total").passed


def test_missing_tax_is_skipped() -> None:
    checks = run_checks(receipt(tax=None), None)
    check = by_rule(checks, "subtotal_plus_tax_equals_total")
    assert check.skipped
    assert check.passed


def test_negative_amount_fails() -> None:
    checks = run_checks(receipt(total=Decimal("-1.00")), None)
    assert not by_rule(checks, "amounts_non_negative").passed


def test_grounding_uses_source_text() -> None:
    checks = run_checks(receipt(), "Blue Bottle Coffee\nTOTAL 14.34")
    assert by_rule(checks, "total_grounded_in_source").passed
    assert by_rule(checks, "vendor_grounded_in_source").passed


def test_grounding_fails_when_total_absent() -> None:
    checks = run_checks(receipt(), "Blue Bottle Coffee\nTOTAL 99.99")
    assert not by_rule(checks, "total_grounded_in_source").passed


def test_grounding_skipped_without_source_text() -> None:
    checks = run_checks(receipt(), None)
    assert by_rule(checks, "total_grounded_in_source").skipped
    assert by_rule(checks, "vendor_grounded_in_source").skipped
