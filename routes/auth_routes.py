from flask import Blueprint, g, jsonify, session
from mysql.connector import Error as MySQLConnectorError
from werkzeug.security import generate_password_hash

from database.connection import cursor, execute_write, fetch_all, fetch_one, transaction
from services.api_errors import ApiError
from services.auth import login_required, public_user, roles_required, verify_password
from services.validation import json_object, json_safe, optional_email, required_text


auth_bp = Blueprint("auth", __name__, url_prefix="/api")
_ROLES = {"admin", "pharmacist"}


@auth_bp.post("/auth/login")
def login():
    session.clear()
    payload = json_object()
    if "email" in payload:
        email = optional_email(payload.get("email"))
        if email is None:
            raise ApiError("email is required.")
        lookup_column = "email"
        login_value = email
    elif "identifier" in payload:
        identifier = required_text(payload, "identifier", 254)
        if "@" in identifier:
            email = optional_email(identifier)
            if email is None:
                raise ApiError("email must be valid.")
            lookup_column = "email"
            login_value = email
        else:
            login_value = required_text({"username": identifier}, "username", 80)
            lookup_column = "username"
    else:
        login_value = required_text(payload, "username", 80)
        lookup_column = "username"

    password = payload.get("password")
    if not isinstance(password, str) or not password:
        raise ApiError("password is required.")

    user = fetch_one(
        f"""
        SELECT user_id, username, email, password_hash, role, is_active
        FROM Users
        WHERE {lookup_column} = %s
        """,
        (login_value,),
    )
    if user is None or not user["is_active"] or not verify_password(
        user["password_hash"], password
    ):
        raise ApiError("Invalid username or password.", 401)

    session.permanent = True
    session["user_id"] = user["user_id"]
    g.current_user = user
    return jsonify({"data": public_user(user)})


@auth_bp.post("/auth/logout")
@login_required
def logout():
    session.clear()
    return jsonify({"message": "Logged out."})


@auth_bp.get("/auth/me")
@login_required
def me():
    from flask import g

    return jsonify({"data": public_user(g.current_user)})


@auth_bp.get("/users")
@login_required
@roles_required("admin")
def list_users():
    users = fetch_all(
        """
        SELECT u.user_id, u.username, u.email, u.role, u.is_active,
               u.created_at, u.updated_at,
               EXISTS (
                   SELECT 1
                   FROM Packaging_Inspections AS i
                   WHERE i.user_id = u.user_id
               ) AS has_inspection_history
        FROM Users AS u
        ORDER BY u.user_id
        """
    )
    return jsonify({"data": json_safe(users)})


@auth_bp.post("/users")
@login_required
@roles_required("admin")
def create_user():
    payload = json_object()
    username = required_text(payload, "username", 80)
    email = optional_email(payload.get("email"))
    if email is None:
        raise ApiError("email is required.")
    password = payload.get("password")
    if not isinstance(password, str) or len(password) < 8:
        raise ApiError("password must be at least 8 characters.")
    role = payload.get("role")
    if role not in _ROLES:
        raise ApiError("role must be admin or pharmacist.")

    result = execute_write(
        """
        INSERT INTO Users (username, email, password_hash, role)
        VALUES (%s, %s, %s, %s)
        """,
        (username, email, generate_password_hash(password), role),
    )
    return jsonify({"data": {"user_id": result["lastrowid"]}}), 201


@auth_bp.patch("/users/<int:user_id>")
@login_required
@roles_required("admin")
def update_user(user_id):
    payload = json_object()
    if not payload:
        raise ApiError("At least one user field is required.")

    existing = fetch_one(
        "SELECT user_id, username, email, role, is_active FROM Users WHERE user_id = %s",
        (user_id,),
    )
    if existing is None:
        raise ApiError("User not found.", 404)

    values = {}
    if "username" in payload:
        values["username"] = required_text(payload, "username", 80)
    if "email" in payload:
        email = optional_email(payload["email"])
        if email is None:
            raise ApiError("email cannot be empty.")
        values["email"] = email
    if "role" in payload:
        role = payload["role"]
        if role not in _ROLES:
            raise ApiError("role must be admin or pharmacist.")
        values["role"] = role
    if "is_active" in payload:
        if not isinstance(payload["is_active"], bool):
            raise ApiError("is_active must be true or false.")
        values["is_active"] = payload["is_active"]
    if "password" in payload:
        password = payload["password"]
        if not isinstance(password, str) or len(password) < 8:
            raise ApiError("password must be at least 8 characters.")
        values["password_hash"] = generate_password_hash(password)
    if not values:
        raise ApiError("No supported user fields were provided.")

    target_role = values.get("role", existing["role"])
    target_active = values.get("is_active", bool(existing["is_active"]))
    if user_id == g.current_user["user_id"] and (
        target_role != "admin" or not target_active
    ):
        raise ApiError("You cannot demote or deactivate your own admin account.")
    if existing["role"] == "admin" and existing["is_active"] and (
        target_role != "admin" or not target_active
    ):
        remaining_admins = fetch_one(
            """
            SELECT COUNT(*) AS admin_count
            FROM Users
            WHERE role = 'admin' AND is_active = TRUE AND user_id <> %s
            """,
            (user_id,),
        )
        if remaining_admins["admin_count"] == 0:
            raise ApiError("At least one active admin account must remain.")

    assignments = ", ".join(f"{column} = %s" for column in values)
    execute_write(
        f"UPDATE Users SET {assignments} WHERE user_id = %s",
        (*values.values(), user_id),
    )
    return jsonify({"message": "User updated."})


@auth_bp.delete("/users/<int:user_id>")
@login_required
@roles_required("admin")
def delete_user(user_id):
    if user_id == g.current_user["user_id"]:
        raise ApiError("You cannot permanently delete your own account.", 409)

    try:
        with transaction() as connection:
            with cursor(connection=connection) as cur:
                cur.execute(
                    """
                    SELECT user_id, role, is_active
                    FROM Users
                    WHERE user_id = %s
                    FOR UPDATE
                    """,
                    (user_id,),
                )
                user = cur.fetchone()
                if user is None:
                    raise ApiError("User not found.", 404)

                if user["role"] == "admin" and user["is_active"]:
                    cur.execute(
                        """
                        SELECT user_id
                        FROM Users
                        WHERE role = 'admin' AND is_active = TRUE
                        FOR UPDATE
                        """
                    )
                    if len(cur.fetchall()) <= 1:
                        raise ApiError(
                            "At least one active admin account must remain.", 409
                        )

                # All user-owned history is rooted at inspections; detected
                # details and error logs reference those inspection rows.
                cur.execute(
                    """
                    SELECT inspection_id
                    FROM Packaging_Inspections
                    WHERE user_id = %s
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (user_id,),
                )
                if cur.fetchone() is not None:
                    raise ApiError(
                        "This account has inspection history and cannot be "
                        "permanently deleted. Deactivate it instead.",
                        409,
                    )

                cur.execute("DELETE FROM Users WHERE user_id = %s", (user_id,))
                if cur.rowcount != 1:
                    raise ApiError("User not found.", 404)
    except MySQLConnectorError as exc:
        if exc.errno in {1451, 1452}:
            raise ApiError(
                "This account has inspection history and cannot be permanently "
                "deleted. Deactivate it instead.",
                409,
            ) from exc
        raise

    return jsonify({"message": "User permanently deleted."})


def register_cli(app):
    import click

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--email", prompt=True)
    def create_admin(username, email):
        """Create an initial admin using a hidden password prompt."""
        from getpass import getpass

        from flask import current_app

        from database.connection import get_db

        password = getpass("Admin password (minimum 8 characters): ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation or len(password) < 8:
            raise click.ClickException(
                "Passwords must match and contain at least 8 characters."
            )
        try:
            checked_email = optional_email(email)
        except ApiError as exc:
            raise click.ClickException(exc.message) from exc
        if not checked_email:
            raise click.ClickException("A valid email is required.")
        checked_username = username.strip()
        if not checked_username or len(checked_username) > 80:
            raise click.ClickException("Username must contain 1-80 characters.")

        connection = get_db()
        try:
            with cursor(connection=connection) as cur:
                cur.execute(
                    """
                    INSERT INTO Users (username, email, password_hash, role)
                    VALUES (%s, %s, %s, 'admin')
                    """,
                    (
                        checked_username,
                        checked_email,
                        generate_password_hash(password),
                    ),
                )
                user_id = cur.lastrowid
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        current_app.logger.info("Created admin user id=%s.", user_id)
        click.echo(f"Admin user created (user_id={user_id}).")
