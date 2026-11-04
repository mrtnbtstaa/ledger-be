from app.schemas.extraction import CheckResult, Extracted


def compute_confidence(checks: list[CheckResult], data: Extracted, has_source_text: bool) -> float:
    evaluated = [check for check in checks if not check.skipped]
    check_score = sum(1 for check in evaluated if check.passed) / len(evaluated) if evaluated else 1.0

    key_fields = [
        data.vendor,
        data.date,
        data.subtotal if data.subtotal is not None else data.total,
        data.currency,
        data.line_items[0] if data.line_items else None,
    ]
    completeness = sum(1 for value in key_fields if value is not None and value != "") / len(key_fields)

    if has_source_text:
        grounding = [
            check
            for check in checks
            if check.rule in {"total_grounded_in_source", "vendor_grounded_in_source"} and not check.skipped
        ]
        grounding_score = sum(1 for check in grounding if check.passed) / len(grounding) if grounding else 0.0
        confidence = 0.5 * check_score + 0.3 * completeness + 0.2 * grounding_score
    else:
        confidence = (0.5 * check_score + 0.3 * completeness) / 0.8

    return round(min(1.0, max(0.0, confidence)), 4)


def route(checks: list[CheckResult], confidence: float, threshold: float) -> str:
    all_passed = all(check.passed for check in checks if not check.skipped)
    return "auto_approved" if all_passed and confidence >= threshold else "needs_review"
