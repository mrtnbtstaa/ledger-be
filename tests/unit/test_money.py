from decimal import Decimal

from app.services import money


def test_parses_us_and_european_amounts() -> None:
    assert money.parse_decimal("1,234.56") == Decimal("1234.56")
    assert money.parse_decimal("1.234,56") == Decimal("1234.56")
    assert money.parse_decimal("$44.30") == Decimal("44.30")
    assert money.parse_decimal("44,30 EUR") == Decimal("44.30")
    assert money.parse_decimal("-3.50") == Decimal("-3.50")
    assert money.parse_decimal("not a number") is None


def test_quantizes_by_currency_minor_unit() -> None:
    assert money.quantize_money(Decimal("1.005"), "USD") == Decimal("1.01")
    assert money.quantize_money(Decimal("100.6"), "JPY") == Decimal("101")
    assert money.quantize_money(Decimal("1.2345"), "KWD") == Decimal("1.235")


def test_tolerance_scales_with_line_items() -> None:
    assert money.tolerance("USD", 0) == Decimal("0.01")
    assert money.tolerance("USD", 2) == Decimal("0.03")
    assert money.tolerance("JPY", 2) == Decimal("3")


def test_currency_validation() -> None:
    assert money.is_currency("USD")
    assert money.is_currency("eur")
    assert not money.is_currency("ZZZ")
    assert not money.is_currency(None)


def test_amount_variants_cover_separators_and_grouping() -> None:
    variants = money.amount_variants(Decimal("1234.56"), "USD")
    assert "1234.56" in variants
    assert "1234,56" in variants
    assert "1,234.56" in variants
    assert "1.234,56" in variants
