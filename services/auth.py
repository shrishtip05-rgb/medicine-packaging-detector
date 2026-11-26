from functools import wraps

from flask import g, session
from werkzeug.security import check_password_hash

from database.connection import fetch_one
from services.api_errors import ApiError


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            raise ApiError("Authentication is required.", 401)

        user = fetch_one(
            """
            SELECT user_id, username, email, role, is_active
            FROM Users
            WHERE user_id = %s
            """,
            (user_id,),
        )
        if user is None or not user["is_active"]:
            session.clear()
            raise ApiError("The session is no longer valid.", 401)
        g.current_user = user
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if user is None:
                raise ApiError("Authentication is required.", 401)
            if user["role"] not in roles:
                raise ApiError("You do not have permission to perform this action.", 403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


def verify_password(stored_hash, password):
    try:
        return check_password_hash(stored_hash, password)
    except (TypeError, ValueError):
        return False


def public_user(user):
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "is_active": bool(user["is_active"]),
    }
