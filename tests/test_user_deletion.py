import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app import create_app
from mysql.connector import Error as MySQLConnectorError


class DeleteCursor:
    def __init__(
        self,
        user=None,
        active_admins=None,
        inspection=None,
        fail_delete=False,
    ):
        self.user = user
        self.active_admins = active_admins or []
        self.inspection = inspection
        self.fail_delete = fail_delete
        self.statements = []
        self.result = None
        self.rows = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        self.result = None
        self.rows = []
        self.rowcount = 0
        if "SELECT user_id, role, is_active" in sql:
            self.result = self.user
        elif "SELECT user_id" in sql and "role = 'admin'" in sql:
            self.rows = self.active_admins
        elif "SELECT inspection_id" in sql:
            self.result = self.inspection
        elif sql.startswith("DELETE FROM Users"):
            if self.fail_delete:
                raise MySQLConnectorError("Synthetic foreign key restriction.", errno=1451)
            self.rowcount = 1

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class DeleteConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.started = 0

    def cursor(self, dictionary=True):
        return self.fake_cursor

    def start_transaction(self):
        self.started += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class UserDeletionTests(unittest.TestCase):
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
            "role": "admin",
            "is_active": 1,
        }
        self.inspector = {
            "user_id": 2,
            "username": "inspector",
            "email": "inspector@example.test",
            "role": "pharmacist",
            "is_active": 1,
        }
        with self.client.session_transaction() as session:
            session["user_id"] = self.admin["user_id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def delete_as_admin(self, user, active_admins=None, inspection=None, fail_delete=False):
        fake_cursor = DeleteCursor(
            user=user,
            active_admins=active_admins,
            inspection=inspection,
            fail_delete=fail_delete,
        )
        connection = DeleteConnection(fake_cursor)

        @contextmanager
        def fake_transaction():
            connection.start_transaction()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        with patch("services.auth.fetch_one", return_value=self.admin), patch(
            "routes.auth_routes.transaction", side_effect=fake_transaction
        ):
            response = self.client.delete("/api/users/2")
        return response, connection, fake_cursor

    def test_admin_can_delete_unused_account_in_transaction(self):
        response, connection, fake_cursor = self.delete_as_admin(self.inspector)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["message"], "User permanently deleted.")
        self.assertEqual(connection.started, 1)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        delete_statements = [
            sql for sql, _params in fake_cursor.statements if sql.startswith("DELETE")
        ]
        self.assertEqual(delete_statements, ["DELETE FROM Users WHERE user_id = %s"])
        self.assertNotIn("Detected_Details", " ".join(delete_statements))
        self.assertNotIn("Error_Logs", " ".join(delete_statements))
        self.assertNotIn("Packaging_Inspections", " ".join(delete_statements))

    def test_account_with_inspection_history_cannot_be_deleted(self):
        response, connection, fake_cursor = self.delete_as_admin(
            self.inspector,
            inspection={"inspection_id": 50},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"],
            "This account has inspection history and cannot be permanently deleted. "
            "Deactivate it instead.",
        )
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any(sql.startswith("DELETE") for sql, _ in fake_cursor.statements))
        self.assertFalse(
            any(
                sql.startswith("DELETE")
                and any(table in sql for table in ("Packaging_Inspections", "Detected_Details", "Error_Logs"))
                for sql, _ in fake_cursor.statements
            )
        )

    def test_admin_cannot_delete_own_account(self):
        with patch("services.auth.fetch_one", return_value=self.admin), patch(
            "routes.auth_routes.transaction"
        ) as transaction:
            response = self.client.delete("/api/users/1")

        self.assertEqual(response.status_code, 409)
        self.assertIn("own account", response.get_json()["error"])
        transaction.assert_not_called()

    def test_last_active_admin_cannot_be_deleted(self):
        response, connection, fake_cursor = self.delete_as_admin(
            dict(self.admin, user_id=2),
            active_admins=[{"user_id": 2}],
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("At least one active admin", response.get_json()["error"])
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any(sql.startswith("DELETE") for sql, _ in fake_cursor.statements))

    def test_inspector_cannot_delete_users(self):
        with patch("services.auth.fetch_one", return_value=self.inspector), patch(
            "routes.auth_routes.transaction"
        ) as transaction:
            response = self.client.delete("/api/users/3")

        self.assertEqual(response.status_code, 403)
        transaction.assert_not_called()

    def test_restrictive_foreign_key_error_rolls_back_without_cascading(self):
        response, connection, fake_cursor = self.delete_as_admin(
            self.inspector,
            fail_delete=True,
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("inspection history", response.get_json()["error"])
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(
            [sql for sql, _params in fake_cursor.statements if sql.startswith("DELETE")],
            ["DELETE FROM Users WHERE user_id = %s"],
        )


if __name__ == "__main__":
    unittest.main()
