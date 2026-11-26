import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


_FIELDS = (
    "medicine_name",
    "manufacturer",
    "strength",
    "dosage_form",
    "batch_number",
    "manufactured_on",
    "expiry_date",
)

_ERROR_TYPES = {
    "medicine_name": "medicine_name",
    "manufacturer": "manufacturer",
    "strength": "strength",
    "dosage_form": "dosage_form",
    "batch_number": "batch_number",
    "expiry_date": "expiry_date",
}

_STRENGTH_PATTERN = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(mg|mcg|[µμ]g|g|ml|iu|%)"
    r"(?:\s*/\s*(\d+(?:[.,]\d+)?)\s*(mg|mcg|[µμ]g|g|ml|iu))?\s*$",
    re.IGNORECASE,
)


def _clean_text(value):
    return " ".join(str(value).split()).casefold()


def _display_value(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    return str(value).strip()


def _parse_date(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.year, value.month, value.day
    if not isinstance(value, str):
        return None

    text = " ".join(value.split())
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text, pattern).date()
            return parsed.year, parsed.month, parsed.day
        except ValueError:
            continue

    year_month = re.fullmatch(r"(\d{4})[-/](\d{1,2})", text)
    if year_month:
        year, month = map(int, year_month.groups())
        if 1 <= month <= 12:
            return year, month, None

    month_year = re.fullmatch(r"(\d{1,2})[-/](\d{4})", text)
    if month_year:
        month, year = map(int, month_year.groups())
        if 1 <= month <= 12:
            return year, month, None

    for pattern in ("%b %Y", "%B %Y"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.year, parsed.month, None
        except ValueError:
            continue

    numeric_date = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if numeric_date:
        first, second, year = map(int, numeric_date.groups())
        if first > 12 and 1 <= second <= 12:
            day, month = first, second
        elif second > 12 and 1 <= first <= 12:
            month, day = first, second
        else:
            return None
        try:
            parsed = date(year, month, day)
            return parsed.year, parsed.month, parsed.day
        except ValueError:
            return None

    for pattern in ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
        try:
            parsed = datetime.strptime(text, pattern).date()
            return parsed.year, parsed.month, parsed.day
        except ValueError:
            continue
    return None


def _normalize_strength(value):
    text = _clean_text(value)
    match = _STRENGTH_PATTERN.fullmatch(text)
    if not match:
        return text, False

    def normalized_number(number):
        normalized = str(Decimal(number.replace(",", ".")))
        return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized

    amount, unit, secondary_amount, secondary_unit = match.groups()

    def normalized_unit(unit_value):
        unit_value = unit_value.casefold().replace("μ", "µ")
        return "mcg" if unit_value in {"µg", "mcg"} else unit_value

    normalized = normalized_number(amount) + normalized_unit(unit)
    if secondary_amount:
        normalized += "/" + normalized_number(secondary_amount) + normalized_unit(secondary_unit)
    return normalized, True


def _same_date(expected, detected):
    expected_date = _parse_date(expected)
    detected_date = _parse_date(detected)
    if expected_date is None or detected_date is None:
        return None
    if expected_date[:2] != detected_date[:2]:
        return False
    if expected_date[2] is None or detected_date[2] is None:
        return True
    return expected_date[2] == detected_date[2]


def _confidence_value(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not confidence.is_finite() or confidence < 0 or confidence > 100:
        return None
    return confidence


def _reported_confidence(value):
    return float(value) if value is not None else None


def _expiry_is_past(expiry_value, inspected_at):
    expiry = _parse_date(expiry_value)
    inspected = _parse_date(inspected_at)
    if expiry is None or inspected is None:
        return False
    if expiry[2] is None:
        return expiry[:2] < inspected[:2]
    return expiry < inspected


def compare_inspection_fields(expected, detected, confidence_threshold, inspected_at):
    """Compare required package fields and produce schema-compatible findings."""
    try:
        threshold = Decimal(str(confidence_threshold))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("OCR confidence threshold must be a number from 0 to 100.") from exc
    if not threshold.is_finite() or threshold < 0 or threshold > 100:
        raise ValueError("OCR confidence threshold must be a number from 0 to 100.")

    findings = []
    error_logs = []
    has_mismatch = False
    needs_review = False

    def add_review(field_name, expected_value, detected_value, reason, confidence):
        nonlocal needs_review
        needs_review = True
        finding = {
            "field_name": field_name,
            "outcome": "NEEDS_REVIEW",
            "expected_value": _display_value(expected_value),
            "detected_value": detected_value,
            "reason": reason,
            "ocr_confidence": _reported_confidence(confidence),
        }
        findings.append(finding)
        error_type = _ERROR_TYPES.get(field_name)
        if error_type:
            error_logs.append(
                {
                    "error_type": error_type,
                    "expected_value": _display_value(expected_value),
                    "detected_value": detected_value,
                    "severity": "medium",
                }
            )

    for field_name in _FIELDS:
        expected_value = expected.get(field_name)
        field_result = detected.get(field_name) or {}
        value = field_result.get("detected_value")
        status = field_result.get("extraction_status")
        confidence = _confidence_value(field_result.get("ocr_confidence"))

        if expected_value is None or (
            isinstance(expected_value, str) and not expected_value.strip()
        ):
            trusted_value = (
                value.strip()
                if status == "READ"
                and isinstance(value, str)
                and value.strip()
                and confidence is not None
                and confidence >= threshold
                else None
            )
            add_review(
                field_name,
                None,
                trusted_value,
                "expected_value_unavailable",
                confidence,
            )
            continue

        if status != "READ" or not isinstance(value, str) or not value.strip():
            add_review(
                field_name,
                expected_value,
                None,
                "detected_value_unavailable",
                confidence,
            )
            continue

        if confidence is None or confidence < threshold:
            add_review(
                field_name,
                expected_value,
                None,
                "ocr_confidence_below_threshold",
                confidence,
            )
            continue

        value = value.strip()
        if field_name in {"manufactured_on", "expiry_date"}:
            values_match = _same_date(expected_value, value)
            if values_match is None:
                add_review(
                    field_name,
                    expected_value,
                    None,
                    "date_format_unrecognized_or_ambiguous",
                    confidence,
                )
                continue
        elif field_name == "strength":
            expected_normalized, expected_parseable = _normalize_strength(expected_value)
            detected_normalized, detected_parseable = _normalize_strength(value)
            if not expected_parseable or not detected_parseable:
                if expected_normalized == detected_normalized:
                    values_match = True
                else:
                    add_review(
                        field_name,
                        expected_value,
                        None,
                        "strength_format_unrecognized",
                        confidence,
                    )
                    continue
            else:
                values_match = expected_normalized == detected_normalized
        else:
            values_match = _clean_text(expected_value) == _clean_text(value)

        if values_match:
            findings.append(
                {
                    "field_name": field_name,
                    "outcome": "MATCH",
                    "expected_value": _display_value(expected_value),
                    "detected_value": value,
                    "reason": None,
                    "ocr_confidence": _reported_confidence(confidence),
                }
            )
            continue

        if field_name == "manufactured_on":
            needs_review = True
            findings.append(
                {
                    "field_name": field_name,
                    "outcome": "NEEDS_REVIEW",
                    "expected_value": _display_value(expected_value),
                    "detected_value": value,
                    "reason": "manufacturing_date_difference_requires_manual_review",
                    "ocr_confidence": confidence,
                }
            )
            continue

        has_mismatch = True
        findings.append(
            {
                "field_name": field_name,
                "outcome": "MISMATCH",
                "expected_value": _display_value(expected_value),
                "detected_value": value,
                "reason": "values_differ",
                "ocr_confidence": _reported_confidence(confidence),
            }
        )
        error_logs.append(
            {
                "error_type": _ERROR_TYPES[field_name],
                "expected_value": _display_value(expected_value),
                "detected_value": value,
                "severity": "high",
            }
        )

    expected_expiry = expected.get("expiry_date")
    expired = (
        expected_expiry is not None
        and _expiry_is_past(expected_expiry, inspected_at)
    )
    if expired:
        expiry_detection = detected.get("expiry_date") or {}
        expiry_confidence = _confidence_value(expiry_detection.get("ocr_confidence"))
        expiry_value = expiry_detection.get("detected_value")
        trusted_expiry = (
            expiry_value.strip()
            if expiry_detection.get("extraction_status") == "READ"
            and isinstance(expiry_value, str)
            and expiry_value.strip()
            and expiry_confidence is not None
            and expiry_confidence >= threshold
            else None
        )
        findings.append(
            {
                "field_name": "expired_batch",
                "outcome": "EXPIRED",
                "expected_value": _display_value(expected_expiry),
                "detected_value": trusted_expiry,
                "reason": "registered_batch_expired_at_inspection",
                "ocr_confidence": _reported_confidence(expiry_confidence),
            }
        )
        error_logs.append(
            {
                "error_type": "expired_batch",
                "expected_value": _display_value(expected_expiry),
                "detected_value": trusted_expiry,
                "severity": "critical",
            }
        )

    if expired:
        status = "EXPIRED"
    elif has_mismatch:
        status = "MISMATCH"
    elif needs_review:
        status = "NEEDS_REVIEW"
    else:
        status = "MATCH"

    return {
        "status": status,
        "confidence_threshold": float(threshold),
        "findings": findings,
        "error_logs": error_logs,
    }
