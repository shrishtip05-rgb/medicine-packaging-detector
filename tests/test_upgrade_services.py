import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from flask import g, make_response

from app import create_app
from database.connection import DatabaseUnavailable
from services.audit_service import classify_audit_event, record_request_audit
from services.code_scanner import compare_scanned_codes, parse_gs1_identifiers, scan_codes
from services.duplicate_service import find_duplicate_serials
from services.image_quality import assess_image_quality
from services.report_service import build_inspection_pdf
from ml.train_baseline import classification_metrics, grouped_split


class ImageQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = Path(self.temp_dir.name) / "quality.png"

    def tearDown(self):
        self.temp_dir.cleanup()

    def assess(self, image):
        self.assertTrue(cv2.imwrite(str(self.image_path), image))
        return assess_image_quality(self.image_path)

    def test_blurry_and_small_image_is_blocked_with_recommendations(self):
        image = np.full((240, 320, 3), 130, dtype=np.uint8)
        quality = self.assess(image)
        issue_codes = {issue["code"] for issue in quality["issues"]}

        self.assertTrue(quality["blocked"])
        self.assertEqual(quality["status"], "UNSUITABLE")
        self.assertIn("BLURRY", issue_codes)
        self.assertIn("LOW_RESOLUTION", issue_codes)
        self.assertTrue(quality["recommendations"])

    def test_sharp_evenly_lit_sufficient_image_can_proceed(self):
        image = np.full((600, 800, 3), 150, dtype=np.uint8)
        cv2.putText(image, "PACKAGE LABEL", (90, 300), cv2.FONT_HERSHEY_SIMPLEX, 2, (10, 10, 10), 4)
        quality = self.assess(image)

        self.assertFalse(quality["blocked"])
        self.assertIn(quality["status"], {"SUITABLE", "WARNING"})
        self.assertIn("do not guarantee OCR accuracy", quality["limitations"])

    def test_overexposure_is_detected(self):
        quality = self.assess(np.full((600, 800, 3), 255, dtype=np.uint8))
        self.assertTrue(quality["blocked"])
        self.assertIn("OVEREXPOSED", {issue["code"] for issue in quality["issues"]})


class CodeScannerTests(unittest.TestCase):
    def test_gs1_identifiers_are_extracted_without_guessing(self):
        identifiers = parse_gs1_identifiers(
            "(01)09506000134352(17)271231(10)LOT-123(21)SERIAL-9"
        )
        self.assertEqual(identifiers["gtin"], "09506000134352")
        self.assertEqual(identifiers["expiry_date"], "2027-12-31")
        self.assertEqual(identifiers["batch_number"], "LOT-123")
        self.assertEqual(identifiers["serial_number"], "SERIAL-9")

    def test_url_content_is_not_parsed_as_gs1_data(self):
        identifiers = parse_gs1_identifiers(
            "https://example.invalid/(01)09506000134352(21)SERIAL-9"
        )
        self.assertEqual(identifiers, {})

    def test_code_comparison_flags_duplicate_serials_for_review(self):
        scan = {
            "status": "READ",
            "codes": [
                {
                    "format": "QR_CODE",
                    "value": "https://untrusted.invalid/product",
                    "identifiers": {"serial_number": "SERIAL-9"},
                    "is_url": True,
                }
            ],
            "duplicate_serials_in_image": [],
            "limitations": "URLs are inert text.",
        }
        result = compare_scanned_codes(
            scan,
            expected_barcode=None,
            expected_batch="LOT-1",
            expected_expiry=None,
            duplicates=[{"serial_number": "SERIAL-9", "other_inspection_id": 12}],
        )
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertTrue(any("serial_seen_on_another_inspection" == row["reason"] for row in result["findings"]))
        self.assertNotIn("open", str(result).lower())

    def test_missing_code_requires_review_instead_of_authenticity_claim(self):
        result = compare_scanned_codes(
            {"status": "NOT_FOUND", "codes": [], "limitations": "No code decoded."},
            expected_barcode="REGISTERED",
            expected_batch=None,
            expected_expiry=None,
        )
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["findings"][0]["reason"], "barcode_not_decoded")

    def test_qr_url_is_decoded_as_inert_text(self):
        image = cv2.QRCodeEncoder_create().encode("https://example.invalid/item")
        image = cv2.copyMakeBorder(
            image, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255
        )
        image = cv2.resize(
            image, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inert-url-qr.png"
            self.assertTrue(cv2.imwrite(str(path), image))
            scan = scan_codes(path)

        self.assertEqual(scan["status"], "READ")
        self.assertTrue(scan["codes"][0]["is_url"])
        self.assertEqual(scan["codes"][0]["value"], "https://example.invalid/item")
        self.assertEqual(scan["codes"][0]["identifiers"], {})
        self.assertIn("never opened", scan["limitations"])

    def test_duplicate_lookup_filters_for_observed_serial_identifiers(self):
        class FakeCursor:
            statement = None

            def execute(self, statement, params):
                self.statement = (statement, params)

            def fetchall(self):
                return [{"inspection_id": 12, "serial_number": "SERIAL-9"}]

        cursor = FakeCursor()
        matches = find_duplicate_serials(
            cursor,
            15,
            [{"identifiers": {"serial_number": "SERIAL-9"}}],
        )
        self.assertIn("JSON_TABLE", cursor.statement[0])
        self.assertEqual(cursor.statement[1], ("SERIAL-9", 15))
        self.assertEqual(matches[0]["other_inspection_id"], 12)


class PdfAndModelScaffoldTests(unittest.TestCase):
    def test_pdf_report_is_generated_with_screening_limitations(self):
        pdf = build_inspection_pdf(
            {
                "inspection_id": 41,
                "inspected_at": "2026-01-01 10:00:00",
                "status": "NEEDS_REVIEW",
                "username": "reviewer",
            },
            {"medicine_name": "Example Medicine", "batch_number": "B-41"},
            [],
            [],
            None,
            {
                "status": "SUITABLE",
                "quality_score": 100,
                "dimensions": {"width": 800, "height": 600},
                "blur_variance": 90,
                "mean_luminance": 120,
                "recommendations": [],
            },
        )
        content = pdf.getvalue()
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertIn(b"/Title (Medicine Packaging Inspection #41)", content)
        self.assertGreater(len(content), 1000)

    def test_model_metrics_include_confusion_matrix_and_anomaly_scores(self):
        metrics = classification_metrics([0, 0, 1, 1], [0, 1, 1, 1])
        self.assertEqual(metrics["confusion_matrix"], [[1, 1], [0, 2]])
        self.assertEqual(metrics["anomalous_precision"], 2 / 3)
        self.assertEqual(metrics["anomalous_recall"], 1.0)
        self.assertEqual(metrics["anomalous_f1"], 0.8)

    def test_model_holdout_split_keeps_package_groups_separate(self):
        examples = [
            (Path("genuine/package-a_front.jpg"), 0, "package-a"),
            (Path("genuine/package-a_back.jpg"), 0, "package-a"),
            (Path("genuine/package-b_front.jpg"), 0, "package-b"),
            (Path("genuine/package-b_back.jpg"), 0, "package-b"),
            (Path("anomalous/package-c_front.jpg"), 1, "package-c"),
            (Path("anomalous/package-c_back.jpg"), 1, "package-c"),
            (Path("anomalous/package-d_front.jpg"), 1, "package-d"),
            (Path("anomalous/package-d_back.jpg"), 1, "package-d"),
        ]
        training, holdout = grouped_split(examples, seed=10)
        self.assertFalse(
            {example[2] for example in training}
            & {example[2] for example in holdout}
        )

    def test_training_cli_reports_missing_and_invalid_datasets_without_traceback(self):
        script = Path(__file__).resolve().parents[1] / "ml" / "train_baseline.py"
        repository_root = script.parents[1]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            missing = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--dataset-root",
                    str(temp_root / "missing"),
                    "--output-dir",
                    str(temp_root / "missing-output"),
                ],
                cwd=str(repository_root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("Missing class directory", missing.stderr)
            self.assertNotIn("Traceback", missing.stderr)

            dataset = temp_root / "invalid"
            for class_name in ("genuine", "anomalous"):
                class_dir = dataset / class_name
                class_dir.mkdir(parents=True)
                for package in ("package-a", "package-b"):
                    (class_dir / (package + "_front.jpg")).write_bytes(b"not an image")

            invalid = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--dataset-root",
                    str(dataset),
                    "--output-dir",
                    str(temp_root / "invalid-output"),
                ],
                cwd=str(repository_root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("Could not decode image", invalid.stderr)
            self.assertNotIn("Traceback", invalid.stderr)


class AuditTests(unittest.TestCase):
    def test_user_activation_events_are_distinguished_without_logging_passwords(self):
        class FakeCursor:
            statement = None

            def execute(self, statement, params):
                self.statement = (statement, params)

        fake_cursor = FakeCursor()
        with tempfile.TemporaryDirectory() as upload_dir:
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-key-not-for-production",
                    "UPLOAD_FOLDER": upload_dir,
                    "AUDIT_ENABLED_IN_TESTS": True,
                }
            )
            with app.test_request_context(
                "/api/users/5",
                method="PATCH",
                json={"is_active": False, "password": "do-not-log-this"},
            ):
                g.current_user = {"user_id": 1, "username": "admin"}
                g.response_status_code = 200
                self.assertEqual(classify_audit_event(), ("USER_DEACTIVATED", "USER"))
                response = make_response("", 200)
                with patch(
                    "services.audit_service.transaction",
                    return_value=nullcontext(object()),
                ), patch(
                    "services.audit_service.cursor",
                    return_value=nullcontext(fake_cursor),
                ):
                    record_request_audit(response)

        self.assertEqual(response.headers["X-Audit-Log-Status"], "recorded")
        self.assertEqual(fake_cursor.statement[1][2], "USER_DEACTIVATED")
        self.assertNotIn("do-not-log-this", str(fake_cursor.statement[1]))

    def test_audit_failure_is_reported_without_replacing_the_request_response(self):
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key-not-for-production",
                "AUDIT_ENABLED_IN_TESTS": True,
            }
        )
        with app.test_request_context(
            "/api/auth/login",
            method="POST",
            json={"identifier": "sample@example.test", "password": "do-not-log-this"},
        ):
            g.response_status_code = 401
            response = make_response({"error": "Invalid credentials."}, 401)
            with patch(
                "services.audit_service.transaction",
                side_effect=DatabaseUnavailable("database unavailable"),
            ):
                result = record_request_audit(response)

        self.assertIs(result, response)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["X-Audit-Log-Status"], "failed")
        self.assertNotIn("do-not-log-this", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
