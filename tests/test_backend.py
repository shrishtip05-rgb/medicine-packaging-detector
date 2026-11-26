import binascii
import io
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch

from app import create_app
from mysql.connector import Error as MySQLConnectorError
from services.api_errors import ApiError
from services.validation import iso_date, ocr_confidence, positive_int
from werkzeug.security import generate_password_hash


def make_test_png():
    """Create a valid 1x1 RGBA PNG without a fixture file or imaging dependency."""
    def chunk(name, data):
        body = name + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", binascii.crc32(body) & 0xFFFFFFFF
        )

    header = struct.pack(">2I5B", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x42\x82\xc0\xff")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.lastrowid = 41
        self.rowcount = 1
        self._result = None

    def execute(self, sql, params=()):
        self.connection.statements.append((sql, params))
        self._result = None
        if "INSERT INTO Packaging_Inspections" in sql:
            if self.connection.fail_inspection_insert:
                raise MySQLConnectorError("Synthetic database insert failure.")
            self.lastrowid = 41
        elif "SELECT medicine_id FROM Medicines" in sql:
            self._result = {"medicine_id": 9}

    def fetchone(self):
        return self._result

    def close(self):
        pass


class FakeConnection:
    def __init__(self, fail_inspection_insert=False):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_inspection_insert = fail_inspection_insert
        self.statements = []

    def cursor(self, dictionary=True):
        return FakeCursor(self)

    def start_transaction(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def is_connected(self):
        return not self.closed

    def close(self):
        self.closed = True


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key-not-for-production",
                "UPLOAD_FOLDER": self.temp_dir.name,
                "MAX_CONTENT_LENGTH": 64 * 1024,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_health_reports_ocr_service_implemented(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ocr"], "implemented")

    def test_invalid_ocr_confidence_threshold_is_rejected(self):
        with self.assertRaises(RuntimeError):
            create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-key-not-for-production",
                    "OCR_MIN_CONFIDENCE": 101,
                }
            )

    def test_upload_requires_authentication(self):
        response = self.client.post(
            "/api/inspections",
            data={"image": (io.BytesIO(make_test_png()), "package.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(list(Path(self.temp_dir.name).iterdir()), [])

    def test_successful_login_stores_session_without_returning_hash(self):
        user = {
            "user_id": 12,
            "username": "pharmacist",
            "email": "pharmacist@example.test",
            "password_hash": generate_password_hash("sample-password"),
            "role": "pharmacist",
            "is_active": 1,
        }
        with patch("routes.auth_routes.fetch_one", return_value=user):
            response = self.client.post(
                "/api/auth/login",
                json={"username": "pharmacist", "password": "sample-password"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password_hash", response.get_json()["data"])

    def test_invalid_login_is_rejected(self):
        with patch("routes.auth_routes.fetch_one", return_value=None):
            response = self.client.post(
                "/api/auth/login",
                json={"username": "unknown", "password": "wrong-password"},
            )
        self.assertEqual(response.status_code, 401)

    def _authenticate_pharmacist(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    @staticmethod
    def _pharmacist():
        return {
            "user_id": 7,
            "username": "sample",
            "email": "sample@example.test",
            "role": "pharmacist",
            "is_active": 1,
        }

    def test_missing_image_is_rejected(self):
        self._authenticate_pharmacist()
        with patch("services.auth.fetch_one", return_value=self._pharmacist()):
            response = self.client.post("/api/inspections", data={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("image file is required", response.get_json()["error"])
        self.assertEqual(list(Path(self.temp_dir.name).iterdir()), [])

    def test_invalid_file_type_and_signature_are_rejected(self):
        self._authenticate_pharmacist()
        with patch("services.auth.fetch_one", return_value=self._pharmacist()):
            bad_extension = self.client.post(
                "/api/inspections",
                data={"image": (io.BytesIO(b"not an image"), "package.gif")},
                content_type="multipart/form-data",
            )
            bad_signature = self.client.post(
                "/api/inspections",
                data={"image": (io.BytesIO(b"not a PNG"), "package.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(bad_extension.status_code, 400)
        self.assertEqual(bad_signature.status_code, 400)
        self.assertEqual(list(Path(self.temp_dir.name).iterdir()), [])

    def test_upload_size_limit_is_enforced(self):
        self._authenticate_pharmacist()
        with patch("services.auth.fetch_one", return_value=self._pharmacist()):
            response = self.client.post(
                "/api/inspections",
                data={"image": (io.BytesIO(b"x" * (65 * 1024)), "package.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 413)

    def test_valid_image_creates_review_inspection_without_claiming_ocr(self):
        connection = FakeConnection()
        self._authenticate_pharmacist()
        with patch("services.auth.fetch_one", return_value=self._pharmacist()), patch(
            "database.connection.get_db", return_value=connection
        ):
            response = self.client.post(
                "/api/inspections",
                data={"image": (io.BytesIO(make_test_png()), "package.png")},
                content_type="multipart/form-data",
            )

        payload = response.get_json()
        stored_path = Path(self.temp_dir.name) / payload["data"]["image_path"]
        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["data"]["inspection_id"], 41)
        self.assertEqual(payload["data"]["status"], "NEEDS_REVIEW")
        self.assertEqual(payload["data"]["ocr_status"], "not_run")
        self.assertTrue(stored_path.is_file())
        self.assertIn(
            "'NEEDS_REVIEW'",
            next(
                sql
                for sql, _params in connection.statements
                if "INSERT INTO Packaging_Inspections" in sql
            ),
        )
        self.assertEqual(connection.commits, 1)

    def test_failed_database_insert_rolls_back_and_removes_uploaded_file(self):
        connection = FakeConnection(fail_inspection_insert=True)
        self._authenticate_pharmacist()
        with patch("services.auth.fetch_one", return_value=self._pharmacist()), patch(
            "database.connection.get_db", return_value=connection
        ):
            response = self.client.post(
                "/api/inspections",
                data={"image": (io.BytesIO(make_test_png()), "package.png")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(list(Path(self.temp_dir.name).iterdir()), [])

    def test_validation_rejects_bad_values(self):
        with self.assertRaises(ApiError):
            positive_int(0, "id")
        with self.assertRaises(ApiError):
            iso_date("2026/01/01", "date")
        with self.assertRaises(ApiError):
            ocr_confidence(101)


if __name__ == "__main__":
    unittest.main()
