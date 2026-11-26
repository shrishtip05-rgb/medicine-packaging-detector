import unittest
from datetime import date, datetime

from services.comparison_service import compare_inspection_fields


EXPECTED = {
    "medicine_name": "Samplecet",
    "manufacturer": "Example Pharma Labs",
    "strength": "500 mg",
    "dosage_form": "tablet",
    "batch_number": "AB12345",
    "manufactured_on": date(2025, 1, 1),
    "expiry_date": date(2027, 1, 1),
}
INSPECTED_AT = datetime(2026, 6, 1, 12, 0)


def detected_fields():
    return {
        "medicine_name": _field("Samplecet"),
        "manufacturer": _field("Example Pharma Labs"),
        "strength": _field("500 mg"),
        "dosage_form": _field("tablet"),
        "batch_number": _field("AB12345"),
        "manufactured_on": _field("01/2025"),
        "expiry_date": _field("01/2027"),
    }


def _field(value, status="READ", confidence=95):
    return {
        "detected_value": value,
        "extraction_status": status,
        "ocr_confidence": confidence,
    }


def compare(expected=None, detected=None):
    return compare_inspection_fields(
        EXPECTED if expected is None else expected,
        detected_fields() if detected is None else detected,
        confidence_threshold=80,
        inspected_at=INSPECTED_AT,
    )


class ComparisonTests(unittest.TestCase):
    def test_all_fields_match_and_normalizes_case_spacing_strength_and_dates(self):
        detected = detected_fields()
        detected["medicine_name"]["detected_value"] = "  SAMPLECET "
        detected["manufacturer"]["detected_value"] = "Example   Pharma Labs"
        detected["strength"]["detected_value"] = "500MG"
        detected["dosage_form"]["detected_value"] = "TABLET"
        detected["manufactured_on"]["detected_value"] = "2025-01"
        detected["expiry_date"]["detected_value"] = "Jan 2027"

        result = compare(detected=detected)

        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["error_logs"], [])
        self.assertTrue(all(finding["outcome"] == "MATCH" for finding in result["findings"]))

    def test_medicine_name_mismatch_is_logged(self):
        detected = detected_fields()
        detected["medicine_name"]["detected_value"] = "Othercet"
        result = compare(detected=detected)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertIn("medicine_name", [row["error_type"] for row in result["error_logs"]])

    def test_strength_mismatch_is_logged_without_unit_conversion(self):
        detected = detected_fields()
        detected["strength"]["detected_value"] = "500 mcg"
        result = compare(detected=detected)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertIn("strength", [row["error_type"] for row in result["error_logs"]])

    def test_manufacturer_and_dosage_form_mismatches_are_logged(self):
        detected = detected_fields()
        detected["manufacturer"]["detected_value"] = "Different Pharma"
        detected["dosage_form"]["detected_value"] = "capsule"
        result = compare(detected=detected)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertEqual(
            {row["error_type"] for row in result["error_logs"]},
            {"manufacturer", "dosage_form"},
        )

    def test_batch_number_mismatch_is_logged(self):
        detected = detected_fields()
        detected["batch_number"]["detected_value"] = "AB12346"
        result = compare(detected=detected)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertIn("batch_number", [row["error_type"] for row in result["error_logs"]])

    def test_expired_batch_takes_precedence_over_matching_fields(self):
        expected = dict(EXPECTED, expiry_date=date(2026, 5, 31))
        detected = detected_fields()
        detected["expiry_date"]["detected_value"] = "31/05/2026"
        result = compare(expected=expected, detected=detected)
        self.assertEqual(result["status"], "EXPIRED")
        self.assertIn("expired_batch", [row["error_type"] for row in result["error_logs"]])

    def test_expiry_mismatch_is_logged(self):
        detected = detected_fields()
        detected["expiry_date"]["detected_value"] = "02/2027"
        result = compare(detected=detected)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertIn("expiry_date", [row["error_type"] for row in result["error_logs"]])

    def test_missing_field_requires_review(self):
        detected = detected_fields()
        detected["manufacturer"] = _field(None, status="NOT_FOUND", confidence=None)
        result = compare(detected=detected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        manufacturer_log = next(
            row for row in result["error_logs"] if row["error_type"] == "manufacturer"
        )
        self.assertIsNone(manufacturer_log["detected_value"])

    def test_unreadable_field_requires_review(self):
        detected = detected_fields()
        detected["batch_number"] = _field(None, status="UNREADABLE", confidence=None)
        result = compare(detected=detected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertIn("batch_number", [row["error_type"] for row in result["error_logs"]])

    def test_low_confidence_does_not_create_a_confirmed_mismatch(self):
        detected = detected_fields()
        detected["strength"] = _field("250 mg", confidence=79.99)
        result = compare(detected=detected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        strength_finding = next(
            row for row in result["findings"] if row["field_name"] == "strength"
        )
        self.assertEqual(strength_finding["reason"], "ocr_confidence_below_threshold")
        strength_log = next(
            row for row in result["error_logs"] if row["error_type"] == "strength"
        )
        self.assertIsNone(strength_log["detected_value"])

    def test_missing_expected_medicine_requires_review(self):
        expected = dict(EXPECTED)
        for field in ("medicine_name", "manufacturer", "strength", "dosage_form"):
            expected[field] = None
        result = compare(expected=expected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(
            any(
                row["error_type"] == "medicine_name"
                and row["expected_value"] is None
                for row in result["error_logs"]
            )
        )

    def test_missing_expected_batch_requires_review(self):
        expected = dict(EXPECTED)
        for field in ("batch_number", "manufactured_on", "expiry_date"):
            expected[field] = None
        result = compare(expected=expected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(
            any(
                row["error_type"] == "batch_number"
                and row["expected_value"] is None
                for row in result["error_logs"]
            )
        )

    def test_manufacturing_date_difference_is_review_only_without_schema_change(self):
        detected = detected_fields()
        detected["manufactured_on"]["detected_value"] = "02/2025"
        result = compare(detected=detected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        finding = next(
            row for row in result["findings"] if row["field_name"] == "manufactured_on"
        )
        self.assertEqual(
            finding["reason"],
            "manufacturing_date_difference_requires_manual_review",
        )
        self.assertNotIn("manufactured_on", [row["error_type"] for row in result["error_logs"]])

    def test_ambiguous_date_requires_review_instead_of_guessing(self):
        detected = detected_fields()
        detected["manufactured_on"]["detected_value"] = "01/02/2025"
        result = compare(detected=detected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        finding = next(
            row for row in result["findings"] if row["field_name"] == "manufactured_on"
        )
        self.assertEqual(finding["reason"], "date_format_unrecognized_or_ambiguous")

    def test_unavailable_confidence_never_allows_match(self):
        detected = detected_fields()
        detected["strength"]["ocr_confidence"] = None
        result = compare(detected=detected)
        self.assertEqual(result["status"], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
