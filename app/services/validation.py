import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.schemas.extraction import CheckResult, Extracted
from app.services import money


def _check(
    rule: str, description: str, passed: bool, details: str | None = None, skipped: bool = False
) -> CheckResult:
    return CheckResult(rule=rule, description=description, passed=passed, skipped=skipped, details=details)


def _sum(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0"))


def run_checks(data: Extracted, source_text: str | None) -> list[CheckResult]:
    currency = data.currency
    items = data.line_items
    tol = money.tolerance(currency, len(items))
    results: list[CheckResult] = []

    results.append(_check("schema_valid", "Output matches the receipt schema", True))

    missing = [
        name
        for name, value in (("vendor", data.vendor), ("date", data.date), ("total", data.total))
        if value is None or value == ""
    ]
    results.append(
        _check(
            "required_fields_present",
            "Vendor, date and total are present",
            not missing,
            f"Missing: {', '.join(missing)}." if missing else None,
        )
    )

    if data.date is None:
        results.append(
            _check("date_plausible", "The date is plausible", True, "No date to check.", skipped=True)
        )
    else:
        today = datetime.now(UTC).date()
        earliest = today - timedelta(days=365 * 10)
        latest = today + timedelta(days=1)
        plausible = earliest <= data.date <= latest
        results.append(
            _check(
                "date_plausible",
                "The date is within the last 10 years",
                plausible,
                None if plausible else f"{data.date.isoformat()} is outside the accepted range.",
            )
        )

    if data.currency is None:
        results.append(
            _check(
                "currency_valid",
                "The currency is an ISO 4217 code",
                True,
                "No currency to check.",
                skipped=True,
            )
        )
    else:
        valid = money.is_currency(data.currency)
        results.append(
            _check(
                "currency_valid",
                "The currency is an ISO 4217 code",
                valid,
                None if valid else f"{data.currency} is not a known ISO 4217 code.",
            )
        )

    if not items or data.subtotal is None:
        results.append(
            _check(
                "line_items_sum_to_subtotal",
                "Line items add up to the subtotal",
                True,
                "No line items or subtotal to compare.",
                skipped=True,
            )
        )
    elif any(item.line_total is None for item in items):
        results.append(
            _check(
                "line_items_sum_to_subtotal",
                "Line items add up to the subtotal",
                True,
                "At least one line item has no total.",
                skipped=True,
            )
        )
    else:
        item_sum = _sum([item.line_total for item in items if item.line_total is not None])
        passed = abs(item_sum - data.subtotal) <= tol
        results.append(
            _check(
                "line_items_sum_to_subtotal",
                "Line items add up to the subtotal",
                passed,
                None
                if passed
                else f"Items sum to {money.format_money(item_sum, currency)}, subtotal is "
                f"{money.format_money(data.subtotal, currency)}.",
            )
        )

    if data.subtotal is None or data.tax is None or data.total is None:
        results.append(
            _check(
                "subtotal_plus_tax_equals_total",
                "Subtotal plus tax equals the total",
                True,
                "Subtotal, tax or total is missing.",
                skipped=True,
            )
        )
    else:
        expected = data.subtotal + data.tax
        passed = abs(expected - data.total) <= tol
        results.append(
            _check(
                "subtotal_plus_tax_equals_total",
                "Subtotal plus tax equals the total",
                passed,
                None
                if passed
                else f"Subtotal plus tax is {money.format_money(expected, currency)}, but the total reads "
                f"{money.format_money(data.total, currency)}.",
            )
        )

    priced = [
        item
        for item in items
        if item.quantity is not None and item.unit_price is not None and item.line_total is not None
    ]
    if not priced:
        results.append(
            _check(
                "line_math_consistent",
                "Quantity times unit price equals the line total",
                True,
                "No line item has quantity, unit price and total.",
                skipped=True,
            )
        )
    else:
        bad = [
            item
            for item in priced
            if item.quantity is not None
            and item.unit_price is not None
            and item.line_total is not None
            and abs(item.quantity * item.unit_price - item.line_total) > money.tolerance(currency, 0)
        ]
        results.append(
            _check(
                "line_math_consistent",
                "Quantity times unit price equals the line total",
                not bad,
                f"{len(bad)} line item(s) do not multiply out." if bad else None,
            )
        )

    negative: list[str] = []
    for name, value in (("subtotal", data.subtotal), ("tax", data.tax), ("total", data.total)):
        if value is not None and value < 0:
            negative.append(name)
    for index, item in enumerate(items, start=1):
        if item.line_total is not None and item.line_total < 0:
            negative.append(f"line item {index}")
    results.append(
        _check(
            "amounts_non_negative",
            "No amount is negative",
            not negative,
            f"Negative: {', '.join(negative)}." if negative else None,
        )
    )

    if source_text is None:
        results.append(
            _check(
                "total_grounded_in_source",
                "The total appears in the source text",
                True,
                "No source text available.",
                skipped=True,
            )
        )
        results.append(
            _check(
                "vendor_grounded_in_source",
                "The vendor appears in the source text",
                True,
                "No source text available.",
                skipped=True,
            )
        )
    else:
        normalized = money.normalize_source(source_text)
        if data.total is None:
            results.append(
                _check(
                    "total_grounded_in_source",
                    "The total appears in the source text",
                    True,
                    "No total to check.",
                    skipped=True,
                )
            )
        else:
            variants = money.amount_variants(data.total, currency)
            found = any(variant in normalized for variant in variants)
            results.append(
                _check(
                    "total_grounded_in_source",
                    "The total appears in the source text",
                    found,
                    None
                    if found
                    else f"{money.format_money(data.total, currency)} was not found in the text.",
                )
            )

        if not data.vendor:
            results.append(
                _check(
                    "vendor_grounded_in_source",
                    "The vendor appears in the source text",
                    True,
                    "No vendor to check.",
                    skipped=True,
                )
            )
        else:
            tokens = re.findall(r"[a-z0-9]{3,}", data.vendor.lower())
            missing_tokens = [token for token in tokens if token not in normalized]
            results.append(
                _check(
                    "vendor_grounded_in_source",
                    "The vendor appears in the source text",
                    not missing_tokens,
                    f"Not found: {', '.join(missing_tokens)}." if missing_tokens else None,
                )
            )

    return results
