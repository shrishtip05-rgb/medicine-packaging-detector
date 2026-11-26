import json

from flask import current_app, g, request, session
from mysql.connector import Error as MySQLConnectorError

from database.connection import DatabaseUnavailable, cursor, fetch_all, transaction
from services.api_errors import ApiError


def classify_audit_event():
    method = request.method
    path = request.path.rstrip("/")
    status_code = getattr(g, "response_status_code", None)
    if method == "POST" and path == "/api/auth/login":
        return ("USER_LOGIN" if status_code == 200 else "USER_LOGIN_FAILED", "USER")
    if method == "POST" and path == "/api/auth/logout":
        return "USER_LOGOUT", "USER"
    if path == "/api/users" and method == "POST":
        return ("USER_CREATED" if status_code == 201 else "USER_CREATE_ATTEMPT"), "USER"
    if path.startswith("/api/users/"):
        if method == "PATCH":
            payload = request.get_json(silent=True) or {}
            success = status_code == 200
            if "is_active" in payload and isinstance(payload["is_active"], bool):
                action = (
                    "USER_ACTIVATED" if payload["is_active"] else "USER_DEACTIVATED"
                )
                if not success:
                    action += "_ATTEMPT"
            else:
                action = "USER_UPDATED" if success else "USER_UPDATE_ATTEMPT"
        elif method == "DELETE":
            action = "USER_DELETED" if status_code == 200 else "USER_DELETE_ATTEMPT"
        else:
            action = None
        if action:
            return action, "USER"
    if path.startswith("/api/inspections/") and path.endswith("/report.pdf"):
        action = (
            "INSPECTION_REPORT_GENERATED"
            if status_code == 200
            else "INSPECTION_REPORT_ATTEMPT"
        )
        return action, "INSPECTION"
    if path.startswith("/api/inspections/") and path.endswith("/ocr") and method == "POST":
        return ("OCR_PROCESSED" if status_code == 200 else "OCR_ATTEMPT"), "INSPECTION"
    if path.startswith("/api/inspections/") and path.endswith("/results") and method == "POST":
        return ("INSPECTION_RESULTS_SAVED" if status_code == 200 else "INSPECTION_RESULTS_ATTEMPT"), "INSPECTION"
    if path == "/api/inspections" and method == "POST":
        return ("INSPECTION_UPLOADED" if status_code == 202 else "INSPECTION_UPLOAD_ATTEMPT"), "INSPECTION"
    if path.startswith("/api/medicines/") and "/batches" in path:
        action = _write_action(
            method,
            status_code,
            {
                "POST": ("BATCH_CREATED", 201),
                "PUT": ("BATCH_UPDATED", 200),
                "DELETE": ("BATCH_DELETED", 204),
            },
        )
        if action:
            return action, "BATCH"
    if path.startswith("/api/batches/"):
        action = _write_action(
            method,
            status_code,
            {
                "PUT": ("BATCH_UPDATED", 200),
                "DELETE": ("BATCH_DELETED", 204),
            },
        )
        if action:
            return action, "BATCH"
    if path.startswith("/api/medicines/") or path == "/api/medicines":
        action = _write_action(
            method,
            status_code,
            {
                "POST": ("MEDICINE_CREATED", 201),
                "PUT": ("MEDICINE_UPDATED", 200),
                "DELETE": ("MEDICINE_DELETED", 204),
            },
        )
        if action:
            return action, "MEDICINE"
    if path.startswith("/api/manufacturers/") or path == "/api/manufacturers":
        action = _write_action(
            method,
            status_code,
            {
                "POST": ("MANUFACTURER_CREATED", 201),
                "PUT": ("MANUFACTURER_UPDATED", 200),
                "DELETE": ("MANUFACTURER_DELETED", 204),
            },
        )
        if action:
            return action, "MANUFACTURER"
    return None


def _write_action(method, status_code, actions):
    entry = actions.get(method)
    if entry is None:
        return None
    action, success_status = entry
    return action if status_code == success_status else action + "_ATTEMPT"


def record_request_audit(response):
    g.response_status_code = response.status_code
    event = classify_audit_event()
    if event is None:
        return response
    if current_app.testing and not current_app.config.get("AUDIT_ENABLED_IN_TESTS", False):
        return response

    action, target_type = event
    actor = getattr(g, "current_user", None) or {}
    actor_user_id = actor.get("user_id") or session.get("user_id")
    actor_username = actor.get("username")
    view_args = request.view_args or {}
    target_id = next(iter(view_args.values()), None)
    if request.method == "POST" and response.status_code in {201, 202}:
        body = response.get_json(silent=True) or {}
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            target_key = {
                "USER": "user_id",
                "INSPECTION": "inspection_id",
                "BATCH": "batch_id",
                "MEDICINE": "medicine_id",
                "MANUFACTURER": "manufacturer_id",
            }.get(event[1])
            if target_key and data.get(target_key) is not None:
                target_id = data[target_key]
    if target_id is None and request.path.rstrip("/") in {
        "/api/users",
        "/api/medicines",
        "/api/manufacturers",
        "/api/inspections",
    }:
        target_id = None

    details = {
        "http_status": response.status_code,
        "method": request.method,
        "path": request.path,
    }
    try:
        with transaction() as connection:
            with cursor(connection=connection) as db_cursor:
                db_cursor.execute(
                    """
                    INSERT INTO Audit_Logs
                        (actor_user_id, actor_username, action,
                         target_type, target_id, details)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        actor_user_id,
                        actor_username,
                        action,
                        target_type,
                        str(target_id) if target_id is not None else None,
                        json.dumps(details, separators=(",", ":")),
                    ),
                )
        response.headers["X-Audit-Log-Status"] = "recorded"
    except (DatabaseUnavailable, MySQLConnectorError):
        current_app.logger.exception(
            "Audit log write failed for action=%s target_type=%s.",
            action,
            target_type,
        )
        response.headers["X-Audit-Log-Status"] = "failed"
    return response


def list_audit_logs(limit=50, offset=0, action=None):
    if not 1 <= limit <= 100 or offset < 0:
        raise ApiError("Audit pagination values are invalid.")
    if action:
        return fetch_all(
            """
            SELECT audit_id, actor_user_id, actor_username, action,
                   target_type, target_id, details, created_at
            FROM Audit_Logs
            WHERE action = %s
            ORDER BY audit_id DESC
            LIMIT %s OFFSET %s
            """,
            (action, limit, offset),
        )
    return fetch_all(
        """
        SELECT audit_id, actor_user_id, actor_username, action,
               target_type, target_id, details, created_at
        FROM Audit_Logs
        ORDER BY audit_id DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
