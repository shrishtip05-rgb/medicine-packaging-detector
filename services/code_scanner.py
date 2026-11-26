import re
from datetime import date
from pathlib import Path

import cv2
import numpy as np


class CodeScanError(ValueError):
    """Raised when an inspection image cannot be decoded for code scanning."""


def scan_codes(image_path):
    try:
        encoded = np.frombuffer(Path(image_path).read_bytes(), dtype=np.uint8)
    except OSError as exc:
        raise CodeScanError("The package image could not be read for code scanning.") from exc

    try:
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise CodeScanError("The package image could not be decoded for code scanning.") from exc
    if image is None:
        raise CodeScanError("The package image is unreadable or corrupt.")

    codes = []
    qr_detector = cv2.QRCodeDetector()
    try:
        found, decoded, _points, _straight = qr_detector.detectAndDecodeMulti(image)
        if found:
            codes.extend(
                _make_code("QR_CODE", value)
                for value in decoded
                if isinstance(value, str) and value.strip()
            )
    except cv2.error:
        value, _points, _straight = qr_detector.detectAndDecode(image)
        if isinstance(value, str) and value.strip():
            codes.append(_make_code("QR_CODE", value))

    try:
        detector_type = getattr(cv2, "barcode_BarcodeDetector", None)
        if detector_type is not None:
            barcode_detector = detector_type()
            found, decoded_values, formats, _points = barcode_detector.detectAndDecodeWithType(
                image
            )
            if found:
                for index, value in enumerate(decoded_values):
                    if not isinstance(value, str) or not value.strip():
                        continue
                    code_format = formats[index] if index < len(formats) else "BARCODE"
                    codes.append(_make_code(str(code_format), value))
    except cv2.error:
        # QR scanning remains available even if the OpenCV barcode detector
        # cannot process a particular image.
        pass

    unique_codes = []
    seen = set()
    for code in codes:
        key = (code["format"], code["value"])
        if key not in seen:
            unique_codes.append(code)
            seen.add(key)

    duplicate_serials = _duplicate_serials(unique_codes)
    warnings = []
    if not unique_codes:
        warnings.append("No supported QR code or barcode could be decoded.")
    if duplicate_serials:
        warnings.append("The same serial identifier appears more than once in this image.")

    return {
        "status": "READ" if unique_codes else "NOT_FOUND",
        "codes": unique_codes,
        "duplicate_serials_in_image": duplicate_serials,
        "warnings": warnings,
        "limitations": (
            "Decoded content is not manufacturer authentication. QR URLs are displayed "
            "as text only and are never opened."
        ),
    }


def _make_code(code_format, value):
    value = value.strip()
    identifiers = parse_gs1_identifiers(value)
    return {
        "format": code_format,
        "value": value,
        "identifiers": identifiers,
        "is_url": value.lower().startswith(("http://", "https://")),
    }


def parse_gs1_identifiers(value):
    identifiers = {}
    if value.lower().startswith(("http://", "https://")):
        return identifiers
    parenthesized = re.findall(r"\(\s*(01|10|17|21)\s*\)([^()]+)", value)
    if parenthesized:
        pairs = parenthesized
    else:
        # GS1 variable-length values require a separator when another AI
        # follows; without it the parser deliberately avoids guessing.
        text = value
        has_symbology_identifier = False
        for prefix in ("]C1", "]d2", "]Q3"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
                has_symbology_identifier = True
                break
        if not has_symbology_identifier and not text.startswith(("01", "10", "17", "21")):
            return identifiers
        pairs = []
        index = 0
        while index < len(text):
            ai = next((candidate for candidate in ("01", "17", "10", "21") if text.startswith(candidate, index)), None)
            if ai is None:
                break
            index += 2
            if ai in {"01", "17"}:
                length = 14 if ai == "01" else 6
                content = text[index : index + length]
                index += len(content)
            else:
                end = text.find("\x1d", index)
                if end < 0:
                    content = text[index:]
                    index = len(text)
                else:
                    content = text[index:end]
                    index = end + 1
            if content:
                pairs.append((ai, content))

    for ai, content in pairs:
        ai = ai.strip()
        content = content.strip()
        if ai == "01" and len(content) == 14 and content.isdigit():
            identifiers["gtin"] = content
        elif ai == "10" and content:
            identifiers["batch_number"] = content
        elif ai == "17" and len(content) == 6 and content.isdigit():
            try:
                year = 2000 + int(content[:2])
                month = int(content[2:4])
                day = int(content[4:6])
                identifiers["expiry_date"] = date(year, month, day).isoformat()
            except ValueError:
                identifiers["malformed_expiry"] = content
        elif ai == "17":
            identifiers["malformed_expiry"] = content
        elif ai == "21" and content:
            identifiers["serial_number"] = content
    return identifiers


def compare_scanned_codes(scan_result, expected_barcode, expected_batch, expected_expiry, duplicates=None):
    codes = scan_result.get("codes", [])
    findings = []
    error_logs = []
    status = "MATCH"
    expected_barcode = (
        str(expected_barcode).strip() if expected_barcode is not None else None
    )

    def add_review(field_name, reason, expected=None, detected=None):
        nonlocal status
        if status == "MATCH":
            status = "NEEDS_REVIEW"
        findings.append(
            {
                "field_name": field_name,
                "outcome": "NEEDS_REVIEW",
                "reason": reason,
                "expected_value": expected,
                "detected_value": detected,
            }
        )
        error_logs.append(
            {
                "error_type": "barcode" if field_name == "barcode" else field_name,
                "expected_value": expected,
                "detected_value": detected,
                "severity": "medium",
            }
        )

    if not codes:
        add_review("barcode", "barcode_not_decoded", expected_barcode, None)
    elif expected_barcode is None:
        add_review(
            "barcode",
            "registered_barcode_unavailable",
            None,
            codes[0]["value"],
        )
    else:
        comparable_values = {
            value.casefold()
            for code in codes
            for value in (code["value"], code["identifiers"].get("gtin"))
            if value
        }
        if expected_barcode.casefold() not in comparable_values:
            status = "MISMATCH"
            detected_value = codes[0]["value"]
            findings.append(
                {
                    "field_name": "barcode",
                    "outcome": "MISMATCH",
                    "reason": "registered_barcode_differs",
                    "expected_value": expected_barcode,
                    "detected_value": detected_value,
                }
            )
            error_logs.append(
                {
                    "error_type": "barcode",
                    "expected_value": expected_barcode,
                    "detected_value": detected_value,
                    "severity": "high",
                }
            )

    for code in codes:
        identifiers = code.get("identifiers", {})
        if identifiers.get("malformed_expiry"):
            add_review(
                "barcode",
                "malformed_expiry_identifier",
                None,
                identifiers["malformed_expiry"],
            )
        batch_number = identifiers.get("batch_number")
        if expected_batch is not None and batch_number and batch_number.casefold() != str(expected_batch).casefold():
            status = "MISMATCH"
            findings.append(
                {
                    "field_name": "batch_number",
                    "outcome": "MISMATCH",
                    "reason": "barcode_batch_identifier_differs",
                    "expected_value": str(expected_batch),
                    "detected_value": batch_number,
                }
            )
            error_logs.append(
                {
                    "error_type": "batch_number",
                    "expected_value": str(expected_batch),
                    "detected_value": batch_number,
                    "severity": "high",
                }
            )
        barcode_expiry = identifiers.get("expiry_date")
        if expected_expiry is not None and barcode_expiry and barcode_expiry != str(expected_expiry):
            status = "MISMATCH"
            findings.append(
                {
                    "field_name": "expiry_date",
                    "outcome": "MISMATCH",
                    "reason": "barcode_expiry_identifier_differs",
                    "expected_value": str(expected_expiry),
                    "detected_value": barcode_expiry,
                }
            )
            error_logs.append(
                {
                    "error_type": "expiry_date",
                    "expected_value": str(expected_expiry),
                    "detected_value": barcode_expiry,
                    "severity": "high",
                }
            )

    for duplicate in scan_result.get("duplicate_serials_in_image", []):
        add_review("barcode", "duplicate_serial_within_image", None, duplicate)
    for duplicate in duplicates or []:
        add_review(
            "barcode",
            "serial_seen_on_another_inspection",
            None,
            duplicate["serial_number"],
        )

    return {
        "status": status,
        "findings": findings,
        "error_logs": error_logs,
        "limitations": scan_result.get("limitations"),
    }


def _duplicate_serials(codes):
    seen = set()
    duplicates = []
    for code in codes:
        serial = code["identifiers"].get("serial_number")
        if not serial:
            continue
        if serial in seen and serial not in duplicates:
            duplicates.append(serial)
        seen.add(serial)
    return duplicates
