from flask import Blueprint, jsonify, request
from mysql.connector import Error as MySQLConnectorError

from services.audit_service import list_audit_logs
from services.api_errors import ApiError
from services.auth import login_required, roles_required
from services.validation import json_safe, positive_int


audit_bp = Blueprint("audit", __name__, url_prefix="/api/audit")


@audit_bp.get("")
@login_required
@roles_required("admin")
def list_logs():
    limit = positive_int(request.args.get("limit", 50), "limit")
    offset_value = request.args.get("offset", "0")
    try:
        offset = int(offset_value)
    except (TypeError, ValueError):
        offset = -1
    action = request.args.get("action", "").strip() or None
    try:
        rows = list_audit_logs(limit, offset, action)
    except MySQLConnectorError as exc:
        if exc.errno == 1146:
            raise ApiError(
                "Audit logs are unavailable. Apply the documented audit migration.",
                503,
            ) from exc
        raise
    return jsonify({"data": json_safe(rows)})
