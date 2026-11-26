import re
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import request

from services.api_errors import ApiError


def json_object():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("A JSON object is required.")
    return payload


def required_text(payload, key, max_length):
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(f"{key} is required and must be text.")
    value = value.strip()
    if len(value) > max_length:
        raise ApiError(f"{key} must be at most {max_length} characters.")
    return value


def optional_text(payload, key, max_length):
    value = payload.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ApiError(f"{key} must be text or null.")
    value = value.strip()
    if len(value) > max_length:
        raise ApiError(f"{key} must be at most {max_length} characters.")
    return value or None


def positive_int(value, field):
    if isinstance(value, bool):
        raise ApiError(f"{field} must be a positive integer.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        number = int(value.strip())
    else:
        raise ApiError(f"{field} must be a positive integer.") from None
    if number < 1:
        raise ApiError(f"{field} must be a positive integer.")
    return number


def optional_positive_int(value, field):
    if value is None or value == "":
        return None
    return positive_int(value, field)


def iso_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ApiError(f"{field} must use YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(f"{field} must use YYYY-MM-DD format.") from None


def optional_email(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 254:
        raise ApiError("email must be a valid email address.")
    value = value.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ApiError("email must be a valid email address.")
    return value


def ocr_confidence(value, field="ocr_confidence"):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ApiError(f"{field} must be a number from 0 to 100 or null.")
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ApiError(f"{field} must be a number from 0 to 100 or null.") from None
    if not confidence.is_finite() or confidence < 0 or confidence > 100:
        raise ApiError(f"{field} must be a number from 0 to 100 or null.")
    return confidence


def json_safe(value):
    from datetime import date, datetime

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
