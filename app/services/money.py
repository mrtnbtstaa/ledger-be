import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import pycountry

# Currencies whose minor unit is not 2. Everything else is assumed to be 2.
MINOR_UNITS: dict[str, int] = {
    "BIF": 0,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "ISK": 0,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
}


def minor_units(currency: str | None) -> int:
    return MINOR_UNITS.get((currency or "").upper(), 2)


def is_currency(code: str | None) -> bool:
    if not code or len(code) != 3:
        return False
    return pycountry.currencies.get(alpha_3=code.upper()) is not None


def parse_decimal(value: Any) -> Decimal | None:
    """Parses 1,234.56 and 1.234,56 style amounts. Ambiguous three-digit tails are thousands."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))

    text = re.sub(r"[^\d,.\-]", "", str(value).strip())
    if not text or text in {"-", ".", ","}:
        return None
    negative = text.startswith("-")
    text = text.lstrip("-")

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            text = text.replace(".", "")

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def quantize_money(value: Decimal, currency: str | None) -> Decimal:
    exponent = Decimal(1).scaleb(-minor_units(currency))
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def format_money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return "not found"
    return f"{quantize_money(value, currency):.{minor_units(currency)}f}"


def tolerance(currency: str | None, item_count: int = 0) -> Decimal:
    return Decimal(1).scaleb(-minor_units(currency)) * (item_count + 1)


def amount_variants(value: Decimal, currency: str | None) -> set[str]:
    minor = minor_units(currency)
    quantized = quantize_money(value, currency)
    plain = f"{quantized:.{minor}f}"
    variants = {plain, plain.replace(".", ",")}
    if minor > 0:
        trimmed = plain.rstrip("0").rstrip(".")
        variants.add(trimmed)
        variants.add(trimmed.replace(".", ","))
    if abs(quantized) >= 1000:
        grouped = f"{quantized:,.{minor}f}"
        european = grouped.replace(",", "X").replace(".", ",").replace("X", ".")
        variants.update({grouped, european, grouped.replace(",", ""), european.replace(".", "")})
    return {variant for variant in variants if variant}


def normalize_source(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())
