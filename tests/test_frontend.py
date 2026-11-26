import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app import create_app
from mysql.connector import Error as MySQLConnectorError
from services.image_service import StoredImageError


class FrontendTests(unittest.TestCase):
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

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def user():
        return {
            "user_id": 7,
            "username": "sample",
            "email": "sample@example.test",
            "role": "pharmacist",
            "is_active": 1,
        }

    def authenticate(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 7

    def test_frontend_pages_and_assets_are_served(self):
        for path in (
            "/login",
            "/dashboard",
            "/medicines",
            "/batches",
            "/batches/3",
            "/inspections/new",
            "/inspections",
            "/inspections/41",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                response.close()

        for path in (
            "/static/css/app.css",
            "/static/js/api.js",
            "/static/js/login.js",
            "/static/js/app.js",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.data)
                response.close()

    def test_login_page_only_checks_api_session_when_one_may_exist(self):
        response = self.client.get("/login")
        self.assertIn(b'data-check-session="false"', response.data)

        self.authenticate()
        response = self.client.get("/login")
        self.assertIn(b'data-check-session="true"', response.data)

    def test_dashboard_returns_database_derived_summary(self):
        self.authenticate()
        statistics = {
            "total_inspections": 3,
            "match_count": 1,
            "mismatch_count": 1,
            "needs_review_count": 0,
            "expired_count": 1,
        }
        recent = [{"inspection_id": 41, "status": "MATCH"}]
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.dashboard_routes.fetch_one", return_value=statistics
        ), patch("routes.dashboard_routes.fetch_all", return_value=recent):
            response = self.client.get("/api/dashboard/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["statistics"], statistics)
        self.assertEqual(payload["recent_inspections"], recent)
        self.assertIn("monthly_trend", payload)
        self.assertIn("common_errors", payload)
        self.assertIn("duplicate_serials", payload)

    def test_history_status_filter_is_passed_to_database_query(self):
        self.authenticate()
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.inspection_routes.fetch_all", return_value=[]
        ) as fetch_history:
            response = self.client.get("/api/inspections?status=MATCH")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "MATCH")
        query, parameters = fetch_history.call_args.args
        self.assertIn("WHERE s.status = %s", query)
        self.assertEqual(parameters, ("MATCH", 25, 0))

    def test_history_search_filters_medicine_manufacturer_or_batch(self):
        self.authenticate()
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.inspection_routes.fetch_all", return_value=[]
        ) as fetch_history:
            response = self.client.get("/api/inspections?q=sample")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["q"], "sample")
        query, parameters = fetch_history.call_args.args
        self.assertIn("search_manufacturer.manufacturer_name LIKE %s", query)
        self.assertEqual(parameters, ("%sample%", "%sample%", "%sample%", 25, 0))

    def test_pdf_download_is_authenticated_and_returns_pdf_attachment(self):
        self.authenticate()
        inspection = {
            "inspection_id": 41,
            "inspected_at": datetime(2026, 1, 1, 10, 0),
            "status": "MATCH",
            "image_path": "package.png",
            "username": "sample",
            "medicine_id": 2,
            "batch_id": 3,
        }
        expected = {"medicine_name": "Example Medicine", "barcode": None}
        pdf_bytes = b"%PDF-1.4 test report"
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.inspection_routes.fetch_one",
            side_effect=[inspection, expected],
        ), patch(
            "routes.inspection_routes.fetch_all", side_effect=[[], []]
        ), patch(
            "routes.inspection_routes.resolve_stored_image_path",
            side_effect=StoredImageError("test image missing"),
        ), patch(
            "services.report_service.build_inspection_pdf",
            return_value=io.BytesIO(pdf_bytes),
        ):
            response = self.client.get("/api/inspections/41/report.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertEqual(response.data, pdf_bytes)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_batch_summary_endpoint_is_login_protected(self):
        with patch("routes.batch_routes.fetch_all", return_value=[]) as fetch_batches:
            response = self.client.get("/api/batches/summary?q=lot&status=expired")
        self.assertEqual(response.status_code, 401)
        fetch_batches.assert_not_called()

    def test_batch_search_and_history_use_database_queries(self):
        self.authenticate()
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.batch_routes.fetch_all", return_value=[]
        ) as fetch_batches:
            response = self.client.get(
                "/api/batches/summary?q=lot&status=expired&limit=10&offset=20"
            )
        self.assertEqual(response.status_code, 200)
        query, parameters = fetch_batches.call_args.args
        self.assertIn("b.expiry_date < CURDATE()", query)
        self.assertEqual(parameters, ("%lot%", "%lot%", "%lot%", 10, 20))

        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.batch_routes.fetch_all", return_value=[]
        ) as fetch_history:
            response = self.client.get("/api/batches/9/inspections?limit=5&offset=10")
        self.assertEqual(response.status_code, 200)
        query, parameters = fetch_history.call_args.args
        self.assertIn("WHERE batch_id = %s", query)
        self.assertEqual(parameters, (9, 5, 10))

    def test_admin_audit_page_is_rendered(self):
        admin = dict(self.user(), role="admin")
        self.authenticate()
        with patch("services.auth.fetch_one", return_value=admin):
            response = self.client.get("/audit")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Audit trail", response.data)

    def test_pdf_download_requires_authentication(self):
        with patch("routes.inspection_routes.fetch_one") as inspection_query:
            response = self.client.get("/api/inspections/41/report.pdf")
        self.assertEqual(response.status_code, 401)
        inspection_query.assert_not_called()

    def test_audit_log_endpoint_is_admin_only(self):
        self.authenticate()
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.audit_routes.list_audit_logs", return_value=[]
        ) as list_logs:
            response = self.client.get("/api/audit")

        self.assertEqual(response.status_code, 403)
        list_logs.assert_not_called()

        admin = dict(self.user(), role="admin")
        with patch("services.auth.fetch_one", return_value=admin), patch(
            "routes.audit_routes.list_audit_logs", return_value=[]
        ) as list_logs:
            response = self.client.get("/api/audit")
        self.assertEqual(response.status_code, 200)
        list_logs.assert_called_once()

        missing_table = MySQLConnectorError(
            msg="Table does not exist.", errno=1146
        )
        with patch("services.auth.fetch_one", return_value=admin), patch(
            "routes.audit_routes.list_audit_logs", side_effect=missing_table
        ):
            response = self.client.get("/api/audit")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Apply the documented audit migration", response.get_json()["error"])

    def test_logout_clears_the_authenticated_session(self):
        self.authenticate()
        with patch("services.auth.fetch_one", return_value=self.user()):
            response = self.client.post("/api/auth/logout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["message"], "Logged out.")
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_inspection_report_includes_expected_fields_and_review_findings(self):
        self.authenticate()
        inspection = {
            "inspection_id": 41,
            "inspected_at": datetime(2026, 1, 1, 10, 0),
            "status": "NEEDS_REVIEW",
            "image_path": "package.png",
            "username": "sample",
            "error_count": 0,
            "review_finding_count": 0,
        }
        expected = {
            "medicine_name": "Example Medicine",
            "manufacturer": "Example Labs",
            "strength": "500 mg",
            "dosage_form": "tablet",
            "batch_number": "B-41",
            "manufactured_on": date(2025, 1, 1),
            "expiry_date": date(2027, 1, 1),
        }
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.inspection_routes.fetch_one",
            side_effect=[inspection, expected],
        ), patch("routes.inspection_routes.fetch_all", side_effect=[[], []]):
            response = self.client.get("/api/inspections/41")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["expected_details"]["medicine_name"], "Example Medicine")
        self.assertEqual(payload["comparison_findings"][0]["outcome"], "NEEDS_REVIEW")

    def test_authenticated_user_can_load_saved_inspection_image(self):
        self.authenticate()
        image_path = Path(self.temp_dir.name) / "package.png"
        image_path.write_bytes(b"synthetic test image")
        with patch("services.auth.fetch_one", return_value=self.user()), patch(
            "routes.inspection_routes.fetch_one",
            return_value={"image_path": "package.png"},
        ):
            response = self.client.get("/api/inspections/41/image")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"synthetic test image")
        self.assertIn("no-store", response.headers["Cache-Control"])
        response.close()


if __name__ == "__main__":
    unittest.main()
