from decimal import Decimal

from app.schemas.extraction import CheckResult, Extracted, LineItemData
from app.services.confidence import compute_confidence, route


def data() -> Extracted:
    return Extracted(
        vendor="Blue Bottle Coffee",
        date="2026-10-28",
        dateRaw="10/28/2026",
        currency="USD",
        subtotal=Decimal("13.25"),
        tax=Decimal("1.09"),
        total=Decimal("14.34"),
        category="dining",
        lineItems=[
            LineItemData(
                description="Flat white",
                quantity=Decimal("2"),
                unitPrice=Decimal("4.50"),
                lineTotal=Decimal("9.00"),
            )
        ],
    )


def checks(passed: bool = True) -> list[CheckResult]:
    return [
        CheckResult(rule="required_fields_present", description="", passed=passed),
        CheckResult(rule="amounts_non_negative", description="", passed=passed),
        CheckResult(rule="total_grounded_in_source", description="", passed=passed, skipped=True),
        CheckResult(rule="vendor_grounded_in_source", description="", passed=passed, skipped=True),
    ]


def test_perfect_signals_score_one() -> None:
    assert compute_confidence(checks(True), data(), False) == 1.0


def test_failed_check_lowers_score() -> None:
    score = compute_confidence(checks(False), data(), False)
    assert score < 1.0
    assert route(checks(False), score, 0.85) == "needs_review"


def test_route_requires_threshold_and_all_checks() -> None:
    assert route(checks(True), 0.9, 0.85) == "auto_approved"
    assert route(checks(True), 0.8, 0.85) == "needs_review"


def test_source_text_grounding_weight() -> None:
    grounded = [
        CheckResult(rule="total_grounded_in_source", description="", passed=True),
        CheckResult(rule="vendor_grounded_in_source", description="", passed=True),
    ]
    assert compute_confidence(grounded, data(), True) == 1.0
