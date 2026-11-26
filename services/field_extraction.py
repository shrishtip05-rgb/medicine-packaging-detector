import re


_FIELD_NAMES = (
    "medicine_name",
    "manufacturer",
    "strength",
    "dosage_form",
    "batch_number",
    "manufactured_on",
    "expiry_date",
)

_LABEL_PATTERNS = {
    "medicine_name": re.compile(
        r"\b(?:medicine|product|brand)(?:\s+name)?\s*[:\-]\s*(.+)", re.IGNORECASE
    ),
    "manufacturer": re.compile(
        r"\b(?:manufacturer|manufactured\s+by|manufacturing\s+company|"
        r"mfr\.?|marketed\s+by)\s*[:\-]?\s*(.+)",
        re.IGNORECASE,
    ),
    "batch_number": re.compile(
        r"\b(?:batch(?:\s*(?:no|number))?|lot(?:\s*(?:no|number))?|"
        r"b\.?\s*no\.?)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9/_\-]*)\b",
        re.IGNORECASE,
    ),
    "manufactured_on": re.compile(
        r"\b(?:mfg\.?\s*date|mfd\.?\s*date|mfg\.?|mfd\.?|"
        r"manufactured\s+on|manufacturing\s+date)\s*[:#\-]?\s*"
        r"(\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
        r"\d{1,2}[/-]\d{4}|\d{1,2}\s+[A-Z]{3,9}\s+\d{2,4})",
        re.IGNORECASE,
    ),
    "expiry_date": re.compile(
        r"\b(?:expiry(?:\s*date)?|exp\.?(?:\s*date)?|expires|use\s+before|"
        r"use\s+by)\s*[:#\-]?\s*"
        r"(\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
        r"\d{1,2}[/-]\d{4}|\d{1,2}\s+[A-Z]{3,9}\s+\d{2,4})",
        re.IGNORECASE,
    ),
}

_STRENGTH_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|[µμ]g|g|ml|iu|%)(?:\s*/\s*"
    r"\d+(?:\.\d+)?\s*(?:mg|mcg|[µμ]g|g|ml|iu))?\b",
    re.IGNORECASE,
)
_DOSAGE_FORM_PATTERN = re.compile(
    r"\b(?:oral\s+suspension|extended\s+release|tablet|tablets|tab|tabs|"
    r"capsule|capsules|caplet|caplets|syrup|suspension|injection|solution|"
    r"cream|ointment|drops|powder|gel|suppository|inhaler)\b",
    re.IGNORECASE,
)


def _confidence_for_line(line, line_confidences):
    if not line_confidences:
        return None
    return line_confidences.get(" ".join(line.split()).casefold())


def _find_label_value(field_name, lines):
    pattern = _LABEL_PATTERNS[field_name]
    for line in lines:
        match = pattern.search(line)
        if match:
            value = match.group(1).strip(" \t:;,.")
            if value:
                return value, line
    return None, None


def _find_pattern_value(pattern, lines):
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(0).strip(), line
    return None, None


def extract_medicine_fields(raw_text, line_confidences=None):
    """Extract only values supported by explicit text labels or known patterns."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    unreadable = not lines
    result = {}

    for field_name in _FIELD_NAMES:
        if field_name in _LABEL_PATTERNS:
            value, source_line = _find_label_value(field_name, lines)
        elif field_name == "strength":
            value, source_line = _find_pattern_value(_STRENGTH_PATTERN, lines)
        else:
            value, source_line = _find_pattern_value(_DOSAGE_FORM_PATTERN, lines)

        result[field_name] = {
            "detected_value": value,
            "extraction_status": (
                "READ" if value is not None else "UNREADABLE" if unreadable else "NOT_FOUND"
            ),
            "ocr_confidence": (
                _confidence_for_line(source_line, line_confidences)
                if value is not None
                else None
            ),
        }

    return result
