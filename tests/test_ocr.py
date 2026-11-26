import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from mysql.connector import Error as MySQLConnectorError
from pytesseract.pytesseract import TesseractNotFoundError

from app import create_app
from services.field_extraction import extract_medicine_fields
from services.image_service import StoredImageError, resolve_stored_image_path
from services.ocr_service import OcrEngineUnavailable, OcrImageError, run_ocr


def create_synthetic_image(path):
    image = np.full((120, 320, 3), 255, dtype=np.uint8)
    encoded, buffer = cv2.imencode(".png", image)
    if not encoded:
        raise RuntimeError("Could not encode the synthetic OCR test image.")
    path.write_bytes(buffer.tobytes())
    return path


def make_line_data(lines):
    data = {
        "text": [],
        "conf": [],
        "block_num": [],
        "par_num": [],
        "line_num": [],
    }
    for line_number, line in enumerate(lines, start=1):
        for word in line.split():
            data["text"].append(word)
            data["conf"].append("88")
            data["block_num"].append(1)
            data["par_num"].append(1)
            data["line_num"].append(line_number)
    return data


class FakeOcrCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.connection.statements.append((sql, params))
        self.result = None
        if "FOR UPDATE" in sql:
            self.result = {
                "status": self.connection.status,
                "medicine_id": self.connection.medicine_id,
                "batch_id": self.connection.batch_id,
                "inspected_at": self.connection.inspected_at,
            }
        elif "SELECT COUNT(*)" in sql:
            self.result = {
                "detail_count": self.connection.detail_count,
                "error_count": self.connection.error_count,
            }
        elif "FROM Medicines AS m" in sql:
            self.result = self.connection.medicine
        elif "FROM Medicine_Batches" in sql:
            self.result = self.connection.batch
        elif "INSERT INTO Detected_Details" in sql:
            self.connection.insert_count += 1
            if self.connection.fail_on_insert == self.connection.insert_count:
                raise MySQLConnectorError("Synthetic detail insert failure.")
            self.connection.pending_details.append(params)
        elif "INSERT INTO Error_Logs" in sql:
            if self.connection.fail_on_error_insert:
                raise MySQLConnectorError("Synthetic error-log insert failure.")
            self.connection.pending_errors.append(params)
            error_type, expected_value, detected_value = params[1:4]
            if error_type == "expired_batch" or self.connection.status == "EXPIRED":
                self.connection.status = "EXPIRED"
            elif self.connection.status == "MISMATCH":
                self.connection.status = "MISMATCH"
            elif expected_value is not None and detected_value is not None:
                self.connection.status = "MISMATCH"
            else:
                self.connection.status = "NEEDS_REVIEW"
        elif "UPDATE Packaging_Inspections" in sql:
            self.connection.status = "MATCH"
        elif "SELECT status FROM Packaging_Inspections" in sql:
            self.result = {"status": self.connection.status}

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeOcrConnection:
    def __init__(
        self,
        status="NEEDS_REVIEW",
        fail_on_insert=None,
        fail_on_error_insert=False,
        batch_expiry=date(2027, 1, 1),
        medicine_id=9,
        batch_id=13,
    ):
        self.status = status
        self.detail_count = 0
        self.error_count = 0
        self.fail_on_insert = fail_on_insert
        self.fail_on_error_insert = fail_on_error_insert
        self.insert_count = 0
        self.medicine_id = medicine_id
        self.batch_id = batch_id
        self.inspected_at = datetime(2026, 6, 1, 12, 0)
        self.medicine = {
            "medicine_name": "Samplecet",
            "manufacturer_name": "Example Pharma",
            "strength": "500 mg",
            "dosage_form": "tablet",
            "package_barcode": "SYNTHETIC-001",
        }
        self.batch = {
            "batch_id": batch_id,
            "medicine_id": medicine_id,
            "batch_number": "AB123",
            "manufactured_on": date(2025, 1, 1),
            "expiry_date": batch_expiry,
        }
        self.pending_details = []
        self.persisted_details = []
        self.pending_errors = []
        self.persisted_errors = []
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, dictionary=True):
        return FakeOcrCursor(self)

    def start_transaction(self):
        self.transaction_status = self.status

    def commit(self):
        self.commits += 1
        self.persisted_details.extend(self.pending_details)
        self.pending_details.clear()
        self.persisted_errors.extend(self.pending_errors)
        self.pending_errors.clear()

    def rollback(self):
        self.rollbacks += 1
        self.status = self.transaction_status
        self.pending_details.clear()
        self.pending_errors.clear()

    def is_connected(self):
        return True


class OcrServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = create_synthetic_image(Path(self.temp_dir.name) / "sample.png")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preprocessing_and_extraction_preserve_text_and_confidence(self):
        lines = [
            "Medicine Name: Samplecet",
            "Manufacturer: Example Pharma",
            "Strength: 500 mg",
            "Dosage Form: tablet",
            "Batch No: AB123",
            "MFG Date: 01/2025",
            "EXP Date: 01/2027",
        ]
        raw_text = "\n".join(lines) + "\n"
        with patch(
            "services.ocr_service.pytesseract.get_tesseract_version",
            return_value="5.5",
        ), patch(
            "services.ocr_service.pytesseract.image_to_string",
            return_value=raw_text,
        ), patch(
            "services.ocr_service.pytesseract.image_to_data",
            return_value=make_line_data(lines),
        ):
            result = run_ocr(self.image_path)

        self.assertEqual(result["raw_text"], raw_text)
        self.assertEqual(result["fields"]["medicine_name"]["detected_value"], "Samplecet")
        self.assertEqual(result["fields"]["manufacturer"]["detected_value"], "Example Pharma")
        self.assertEqual(result["fields"]["strength"]["detected_value"], "500 mg")
        self.assertEqual(result["fields"]["dosage_form"]["detected_value"], "tablet")
        self.assertEqual(result["fields"]["batch_number"]["detected_value"], "AB123")
        self.assertEqual(result["fields"]["manufactured_on"]["detected_value"], "01/2025")
        self.assertEqual(result["fields"]["expiry_date"]["detected_value"], "01/2027")
        self.assertEqual(result["fields"]["medicine_name"]["ocr_confidence"], 88.0)
        self.assertEqual(result["unsupported_fields"], ["barcode"])

    def test_missing_tesseract_is_reported_without_running_ocr(self):
        with patch(
            "services.ocr_service.pytesseract.get_tesseract_version",
            side_effect=TesseractNotFoundError(),
        ), patch("services.ocr_service.pytesseract.image_to_string") as image_to_string:
            with self.assertRaises(OcrEngineUnavailable):
                run_ocr(self.image_path)
        image_to_string.assert_not_called()

    def test_corrupt_image_is_rejected(self):
        corrupt_path = Path(self.temp_dir.name) / "corrupt.png"
        corrupt_path.write_bytes(b"not an image")
        with self.assertRaises(OcrImageError):
            run_ocr(corrupt_path)

    def test_missing_fields_are_not_fabricated_and_blank_text_is_unreadable(self):
        missing = extract_medicine_fields("Plain package text without labeled values.")
        self.assertTrue(
            all(item["extraction_status"] == "NOT_FOUND" for item in missing.values())
        )
        unreadable = extract_medicine_fields(" \n ")
        self.assertTrue(
            all(item["extraction_status"] == "UNREADABLE" for item in unreadable.values())
        )
        self.assertTrue(all(item["detected_value"] is None for item in missing.values()))

    def test_resolver_rejects_path_traversal(self):
        with self.assertRaises(StoredImageError):
            resolve_stored_image_path(
                "uploads/../outside.png", self.temp_dir.name
            )


class OcrApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key-not-for-production",
                "UPLOAD_FOLDER": self.temp_dir.name,
            }
        )
        self.client = self.app.test_client()
        self.stored_path = Path(self.temp_dir.name) / "sample.png"
        self.stored_path.write_bytes(b"synthetic stored image")
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _user():
        return {
            "user_id": 7,
            "username": "sample",
            "email": "sample@example.test",
            "role": "pharmacist",
            "is_active": 1,
        }

    @staticmethod
    def _ocr_result():
        fields = {
            name: {
                "detected_value": value,
                "extraction_status": "READ",
                "ocr_confidence": 90.0,
            }
            for name, value in (
                ("medicine_name", "Samplecet"),
                ("manufacturer", "Example Pharma"),
                ("strength", "500 mg"),
                ("dosage_form", "tablet"),
                ("batch_number", "AB123"),
                ("manufactured_on", "01/2025"),
                ("expiry_date", "01/2027"),
            )
        }
        return {
            "raw_text": "Medicine Name: Samplecet\n",
            "fields": fields,
            "unsupported_fields": ["barcode"],
        }

    def _post_ocr(self, connection, status="NEEDS_REVIEW", ocr_result=None):
        scan = {
            "status": "READ",
            "codes": [
                {
                    "format": "TEST",
                    "value": "SYNTHETIC-001",
                    "identifiers": {},
                    "is_url": False,
                }
            ],
            "duplicate_serials_in_image": [],
            "warnings": [],
            "limitations": "Test fixture only.",
        }
        with patch("services.auth.fetch_one", return_value=self._user()), patch(
            "routes.inspection_routes.fetch_one",
            return_value={
                "image_path": "sample.png",
                "status": status,
                "detail_count": connection.detail_count,
                "error_count": connection.error_count,
            },
        ), patch(
            "routes.inspection_routes.run_ocr",
            return_value=ocr_result or self._ocr_result(),
        ) as ocr_mock, patch(
            "routes.inspection_routes.assess_image_quality",
            return_value={
                "status": "SUITABLE",
                "blocked": False,
                "quality_score": 100,
                "recommendations": [],
            },
        ), patch(
            "routes.inspection_routes.scan_codes", return_value=scan
        ), patch(
            "database.connection.get_db", return_value=connection
        ):
            response = self.client.post("/api/inspections/5/ocr")
        return response, ocr_mock

    def test_ocr_api_compares_matching_fields_and_marks_match(self):
        connection = FakeOcrConnection()
        response, ocr_mock = self._post_ocr(connection)
        payload = response.get_json()["data"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "MATCH")
        self.assertEqual(payload["ocr_status"], "completed")
        self.assertEqual(payload["detected_detail_count"], 8)
        self.assertEqual(payload["error_log_count"], 0)
        self.assertEqual(payload["comparison"]["status"], "MATCH")
        self.assertEqual(payload["raw_text"], "Medicine Name: Samplecet\n")
        self.assertEqual(payload["unsupported_fields"], ["barcode"])
        self.assertEqual(len(connection.persisted_details), 8)
        self.assertEqual(connection.persisted_errors, [])
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        ocr_mock.assert_called_once()
        self.assertTrue(
            any("UPDATE Packaging_Inspections" in sql for sql, _params in connection.statements)
        )
        barcode_rows = [
            params
            for sql, params in connection.statements
            if "INSERT INTO Detected_Details" in sql and "'barcode'" in sql
        ]
        self.assertEqual(len(barcode_rows), 1)
        stored_scan = json.loads(barcode_rows[0][1])
        self.assertEqual(stored_scan["codes"][0]["value"], "SYNTHETIC-001")
        self.assertEqual(stored_scan["image_quality"]["quality_score"], 100)

    def test_ocr_api_mismatch_is_logged_and_trigger_sets_mismatch(self):
        connection = FakeOcrConnection()
        result = self._ocr_result()
        result["fields"]["strength"]["detected_value"] = "250 mg"
        response, _ocr_mock = self._post_ocr(connection, ocr_result=result)

        payload = response.get_json()["data"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "MISMATCH")
        self.assertEqual(payload["error_log_count"], 1)
        self.assertEqual(connection.persisted_errors[0][1], "strength")
        self.assertEqual(connection.persisted_errors[0][2], "500 mg")
        self.assertEqual(connection.persisted_errors[0][3], "250 mg")

    def test_expired_batch_status_takes_precedence(self):
        connection = FakeOcrConnection(batch_expiry=date(2026, 5, 31))
        result = self._ocr_result()
        result["fields"]["expiry_date"]["detected_value"] = "31/05/2026"
        response, _ocr_mock = self._post_ocr(connection, ocr_result=result)
        payload = response.get_json()["data"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "EXPIRED")
        self.assertIn(
            "expired_batch",
            [row[1] for row in connection.persisted_errors],
        )

    def test_expiration_takes_precedence_over_a_strength_mismatch(self):
        connection = FakeOcrConnection(batch_expiry=date(2026, 5, 31))
        result = self._ocr_result()
        result["fields"]["strength"]["detected_value"] = "250 mg"
        result["fields"]["expiry_date"]["detected_value"] = "31/05/2026"
        response, _ocr_mock = self._post_ocr(connection, ocr_result=result)
        payload = response.get_json()["data"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "EXPIRED")
        error_types = [row[1] for row in connection.persisted_errors]
        self.assertIn("strength", error_types)
        self.assertIn("expired_batch", error_types)

    def test_unauthenticated_ocr_request_is_rejected_before_processing(self):
        with self.client.session_transaction() as session:
            session.clear()
        with patch("routes.inspection_routes.fetch_one") as inspection_query, patch(
            "routes.inspection_routes.run_ocr"
        ) as ocr_mock:
            response = self.client.post("/api/inspections/5/ocr")
        self.assertEqual(response.status_code, 401)
        inspection_query.assert_not_called()
        ocr_mock.assert_not_called()

    def test_unsuitable_image_is_blocked_before_ocr_and_returns_guidance(self):
        quality = {
            "status": "UNSUITABLE",
            "blocked": True,
            "quality_score": 20,
            "issues": [{"code": "BLURRY", "severity": "block"}],
            "recommendations": ["Retake the photo with text in focus."],
        }
        with patch("services.auth.fetch_one", return_value=self._user()), patch(
            "routes.inspection_routes.fetch_one",
            return_value={
                "image_path": "sample.png",
                "status": "NEEDS_REVIEW",
                "detail_count": 0,
                "error_count": 0,
            },
        ), patch(
            "routes.inspection_routes.assess_image_quality", return_value=quality
        ), patch("routes.inspection_routes.run_ocr") as ocr_mock, patch(
            "database.connection.get_db"
        ) as get_db:
            response = self.client.post("/api/inspections/5/ocr")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["data"]["image_quality"], quality)
        ocr_mock.assert_not_called()
        get_db.assert_not_called()

    def test_unavailable_tesseract_returns_clear_error_without_database_transaction(self):
        with patch("services.auth.fetch_one", return_value=self._user()), patch(
            "routes.inspection_routes.fetch_one",
            return_value={
                "image_path": "sample.png",
                "status": "NEEDS_REVIEW",
                "detail_count": 0,
                "error_count": 0,
            },
        ), patch(
            "routes.inspection_routes.assess_image_quality",
            return_value={"status": "SUITABLE", "blocked": False, "recommendations": []},
        ), patch(
            "routes.inspection_routes.run_ocr",
            side_effect=OcrEngineUnavailable(),
        ), patch("database.connection.get_db") as get_db:
            response = self.client.post("/api/inspections/5/ocr")

        self.assertEqual(response.status_code, 503)
        self.assertIn("Tesseract is unavailable", response.get_json()["error"])
        self.assertNotIn(self.temp_dir.name, response.get_data(as_text=True))
        get_db.assert_not_called()

    def test_unreadable_stored_image_returns_client_error_without_database_write(self):
        with patch("services.auth.fetch_one", return_value=self._user()), patch(
            "routes.inspection_routes.fetch_one",
            return_value={
                "image_path": "sample.png",
                "status": "NEEDS_REVIEW",
                "detail_count": 0,
                "error_count": 0,
            },
        ), patch(
            "routes.inspection_routes.assess_image_quality",
            return_value={"status": "SUITABLE", "blocked": False, "recommendations": []},
        ), patch(
            "routes.inspection_routes.run_ocr",
            side_effect=OcrImageError(),
        ), patch("database.connection.get_db") as get_db:
            response = self.client.post("/api/inspections/5/ocr")

        self.assertEqual(response.status_code, 422)
        self.assertIn("unreadable or corrupt", response.get_json()["error"])
        get_db.assert_not_called()

    def test_confirmed_status_is_not_overwritten_or_processed(self):
        connection = FakeOcrConnection(status="MATCH")
        response, ocr_mock = self._post_ocr(connection, status="MATCH")
        self.assertEqual(response.status_code, 409)
        ocr_mock.assert_not_called()
        self.assertEqual(connection.persisted_details, [])

    def test_batch_from_another_medicine_is_rejected(self):
        connection = FakeOcrConnection()
        connection.batch["medicine_id"] = 10
        response, _ocr_mock = self._post_ocr(connection)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(connection.persisted_details, [])
        self.assertEqual(connection.persisted_errors, [])
        self.assertEqual(connection.rollbacks, 1)

    def test_existing_results_prevent_duplicate_error_logs(self):
        connection = FakeOcrConnection()
        connection.detail_count = 1
        response, ocr_mock = self._post_ocr(connection)
        self.assertEqual(response.status_code, 409)
        ocr_mock.assert_not_called()
        self.assertEqual(connection.persisted_errors, [])

    def test_failed_detail_insert_rolls_back_all_ocr_rows(self):
        connection = FakeOcrConnection(fail_on_insert=3)
        response, _ocr_mock = self._post_ocr(connection)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(connection.persisted_details, [])
        self.assertEqual(connection.pending_details, [])
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_failed_error_log_insert_rolls_back_details_and_status(self):
        connection = FakeOcrConnection(fail_on_error_insert=True)
        result = self._ocr_result()
        result["fields"]["strength"]["detected_value"] = "250 mg"
        response, _ocr_mock = self._post_ocr(connection, ocr_result=result)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(connection.persisted_details, [])
        self.assertEqual(connection.persisted_errors, [])
        self.assertEqual(connection.status, "NEEDS_REVIEW")
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
