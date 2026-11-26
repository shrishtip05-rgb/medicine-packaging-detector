import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from werkzeug.security import generate_password_hash, check_password_hash


class UserManagementTests(unittest.TestCase):
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
        self.admin = {
            "user_id": 1,
            "username": "admin",
            "email": "admin@example.test",
            "password_hash": generate_password_hash("admin-password"),
            "role": "admin",
            "is_active": 1,
        }
        self.inspector = {
            "user_id": 2,
            "username": "inspector",
            "email": "inspector@example.test",
            "password_hash": generate_password_hash("inspector-password"),
            "role": "pharmacist",
            "is_active": 1,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_admin_inspector_account_lifecycle_and_permissions(self):
        with patch("routes.auth_routes.fetch_one", return_value=self.admin):
            admin_login = self.client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin-password"},
            )
        self.assertEqual(admin_login.status_code, 200)
        self.assertEqual(admin_login.get_json()["data"]["role"], "admin")

        with patch("services.auth.fetch_one", return_value=self.admin), patch(
            "routes.auth_routes.execute_write",
            return_value={"lastrowid": self.inspector["user_id"], "rowcount": 1},
        ) as insert_user:
            create_response = self.client.post(
                "/api/users",
                json={
                    "username": self.inspector["username"],
                    "email": self.inspector["email"],
                    "password": "inspector-password",
                    "role": "pharmacist",
                },
            )
        self.assertEqual(create_response.status_code, 201)
        self.assertNotIn("password_hash", create_response.get_data(as_text=True))
        insert_sql, insert_values = insert_user.call_args.args
        self.assertIn("INSERT INTO Users", insert_sql)
        self.assertTrue(check_password_hash(insert_values[2], "inspector-password"))
        self.assertEqual(insert_values[3], "pharmacist")

        with patch("routes.auth_routes.fetch_one", return_value=self.inspector):
            inspector_login = self.client.post(
                "/api/auth/login",
                json={
                    "identifier": "inspector@example.test",
                    "password": "inspector-password",
                },
            )
        self.assertEqual(inspector_login.status_code, 200)
        self.assertEqual(inspector_login.get_json()["data"]["user_id"], 2)

        with patch("services.auth.fetch_one", return_value=self.inspector), patch(
            "routes.inspection_routes.fetch_all", return_value=[]
        ):
            history = self.client.get("/api/inspections")
        self.assertEqual(history.status_code, 200)

        with patch("services.auth.fetch_one", return_value=self.inspector):
            user_list_denied = self.client.get("/api/users")
            medicine_write_denied = self.client.post("/api/medicines", json={})
            admin_page_denied = self.client.get("/users")
        self.assertEqual(user_list_denied.status_code, 403)
        self.assertEqual(medicine_write_denied.status_code, 403)
        self.assertEqual(admin_page_denied.status_code, 403)

        with patch("routes.auth_routes.fetch_one", return_value=self.admin):
            admin_email_login = self.client.post(
                "/api/auth/login",
                json={"email": "admin@example.test", "password": "admin-password"},
            )
        self.assertEqual(admin_email_login.status_code, 200)

        registered_users = [
            {
                "user_id": 1,
                "username": "admin",
                "email": "admin@example.test",
                "role": "admin",
                "is_active": 1,
            },
            {
                "user_id": 2,
                "username": "inspector",
                "email": "inspector@example.test",
                "role": "pharmacist",
                "is_active": 1,
            },
        ]
        with patch("services.auth.fetch_one", return_value=self.admin), patch(
            "routes.auth_routes.fetch_all", return_value=registered_users
        ) as list_users_query:
            admin_page = self.client.get("/users")
            user_list = self.client.get("/api/users")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"User management", admin_page.data)
        self.assertEqual(user_list.status_code, 200)
        self.assertEqual(len(user_list.get_json()["data"]), 2)
        self.assertNotIn("password_hash", user_list.get_data(as_text=True))
        self.assertIn("has_inspection_history", list_users_query.call_args.args[0])
        self.assertIn("Packaging_Inspections", list_users_query.call_args.args[0])

        with patch("services.auth.fetch_one", return_value=self.admin), patch(
            "routes.auth_routes.fetch_one", return_value=self.inspector
        ), patch("routes.auth_routes.execute_write") as update_user:
            deactivate = self.client.patch("/api/users/2", json={"is_active": False})
        self.assertEqual(deactivate.status_code, 200)
        self.assertIn("is_active", update_user.call_args.args[0])
        self.assertEqual(update_user.call_args.args[1], (False, 2))

        inactive_inspector = dict(self.inspector, is_active=0)
        with patch("routes.auth_routes.fetch_one", return_value=inactive_inspector):
            inactive_login = self.client.post(
                "/api/auth/login",
                json={
                    "identifier": "inspector@example.test",
                    "password": "inspector-password",
                },
            )
        self.assertEqual(inactive_login.status_code, 401)

    def test_invalid_email_identifier_is_rejected(self):
        response = self.client.post(
            "/api/auth/login",
            json={"identifier": "not-an-email@", "password": "some-password"},
        )
        self.assertEqual(response.status_code, 400)

    def test_admin_cannot_deactivate_or_demote_themselves(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin["user_id"]
        with patch("services.auth.fetch_one", return_value=self.admin), patch(
            "routes.auth_routes.fetch_one", return_value=self.admin
        ), patch("routes.auth_routes.execute_write") as update_user:
            response = self.client.patch("/api/users/1", json={"is_active": False})

        self.assertEqual(response.status_code, 400)
        update_user.assert_not_called()

    def test_admin_cannot_deactivate_the_last_other_admin(self):
        another_admin = dict(self.admin, user_id=3, username="second-admin")
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin["user_id"]
        with patch("services.auth.fetch_one", return_value=self.admin), patch(
            "routes.auth_routes.fetch_one",
            side_effect=[another_admin, {"admin_count": 0}],
        ), patch("routes.auth_routes.execute_write") as update_user:
            response = self.client.patch("/api/users/3", json={"is_active": False})

        self.assertEqual(response.status_code, 400)
        update_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
