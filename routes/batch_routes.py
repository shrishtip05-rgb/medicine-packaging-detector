from flask import Blueprint, jsonify, request

from database.connection import execute_write, fetch_all, fetch_one
from services.api_errors import ApiError
from services.auth import login_required, roles_required
from services.validation import (
    iso_date,
    json_object,
    json_safe,
    positive_int,
    required_text,
)


batch_bp = Blueprint("batches", __name__, url_prefix="/api")


def _batch_values(payload):
    manufactured_on = iso_date(payload.get("manufactured_on"), "manufactured_on")
    expiry_date = iso_date(payload.get("expiry_date"), "expiry_date")
    if manufactured_on > expiry_date:
        raise ApiError("manufactured_on cannot be after expiry_date.")
    return (
        required_text(payload, "batch_number", 80),
        manufactured_on,
        expiry_date,
    )


@batch_bp.get("/medicines/<int:medicine_id>/batches")
@login_required
def list_batches(medicine_id):
    batches = fetch_all(
        """
        SELECT batch_id, medicine_id, batch_number, manufactured_on,
               expiry_date, created_at, updated_at
        FROM Medicine_Batches
        WHERE medicine_id = %s
        ORDER BY expiry_date, batch_number
        """,
        (medicine_id,),
    )
    return jsonify({"data": json_safe(batches)})


@batch_bp.post("/medicines/<int:medicine_id>/batches")
@login_required
@roles_required("admin")
def create_batch(medicine_id):
    payload = json_object()
    batch_number, manufactured_on, expiry_date = _batch_values(payload)
    result = execute_write(
        """
        INSERT INTO Medicine_Batches
            (medicine_id, batch_number, manufactured_on, expiry_date)
        VALUES (%s, %s, %s, %s)
        """,
        (medicine_id, batch_number, manufactured_on, expiry_date),
    )
    return jsonify({"data": {"batch_id": result["lastrowid"]}}), 201


@batch_bp.put("/batches/<int:batch_id>")
@login_required
@roles_required("admin")
def update_batch(batch_id):
    payload = json_object()
    medicine_id = positive_int(payload.get("medicine_id"), "medicine_id")
    batch_number, manufactured_on, expiry_date = _batch_values(payload)
    result = execute_write(
        """
        UPDATE Medicine_Batches
        SET medicine_id = %s, batch_number = %s, manufactured_on = %s,
            expiry_date = %s
        WHERE batch_id = %s
        """,
        (medicine_id, batch_number, manufactured_on, expiry_date, batch_id),
    )
    if result["rowcount"] == 0:
        exists = fetch_one(
            "SELECT batch_id FROM Medicine_Batches WHERE batch_id = %s",
            (batch_id,),
        )
        if exists is None:
            raise ApiError("Batch not found.", 404)
    return jsonify({"message": "Batch updated."})


@batch_bp.delete("/batches/<int:batch_id>")
@login_required
@roles_required("admin")
def delete_batch(batch_id):
    result = execute_write(
        "DELETE FROM Medicine_Batches WHERE batch_id = %s",
        (batch_id,),
    )
    if result["rowcount"] == 0:
        raise ApiError("Batch not found.", 404)
    return "", 204


@batch_bp.get("/batches/summary")
@login_required
def batch_summary():
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip().lower()
    if status not in {"", "expired", "expiring", "active"}:
        raise ApiError("status must be expired, expiring, or active.")
    limit = positive_int(request.args.get("limit", 25), "limit")
    if limit > 100:
        raise ApiError("limit cannot exceed 100.")
    try:
        offset = int(request.args.get("offset", "0"))
    except (TypeError, ValueError):
        raise ApiError("offset must be a non-negative integer.") from None
    if offset < 0:
        raise ApiError("offset must be a non-negative integer.")

    filters = []
    params = []
    if search:
        pattern = f"%{search}%"
        filters.append(
            "(m.medicine_name LIKE %s OR mf.manufacturer_name LIKE %s "
            "OR b.batch_number LIKE %s)"
        )
        params.extend((pattern, pattern, pattern))
    if status == "expired":
        filters.append("b.expiry_date < CURDATE()")
    elif status == "expiring":
        filters.append(
            "b.expiry_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)"
        )
    elif status == "active":
        filters.append("b.expiry_date > DATE_ADD(CURDATE(), INTERVAL 30 DAY)")
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    params.extend((limit, offset))
    batches = fetch_all(
        f"""
        SELECT b.batch_id, b.medicine_id, b.batch_number, b.manufactured_on,
               b.expiry_date, m.medicine_name, m.strength,
               mf.manufacturer_name,
               COUNT(i.inspection_id) AS inspection_count,
               COUNT(CASE WHEN i.status = 'MATCH' THEN 1 END) AS match_count,
               COUNT(CASE WHEN i.status = 'MISMATCH' THEN 1 END) AS mismatch_count,
               COUNT(CASE WHEN i.status = 'NEEDS_REVIEW' THEN 1 END) AS review_count,
               COUNT(CASE WHEN i.status = 'EXPIRED' THEN 1 END) AS expired_count,
               DATEDIFF(b.expiry_date, CURDATE()) AS days_until_expiry
        FROM Medicine_Batches AS b
        INNER JOIN Medicines AS m ON m.medicine_id = b.medicine_id
        INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        LEFT JOIN Packaging_Inspections AS i
            ON i.batch_id = b.batch_id AND i.medicine_id = b.medicine_id
        {where_clause}
        GROUP BY b.batch_id, b.medicine_id, b.batch_number, b.manufactured_on,
                 b.expiry_date, m.medicine_name, m.strength, mf.manufacturer_name
        ORDER BY b.expiry_date, m.medicine_name, b.batch_number
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return jsonify(
        {
            "data": json_safe(batches),
            "limit": limit,
            "offset": offset,
            "status": status or None,
            "q": search or None,
        }
    )


@batch_bp.get("/batches/<int:batch_id>/inspections")
@login_required
def batch_inspections(batch_id):
    limit = positive_int(request.args.get("limit", 25), "limit")
    if limit > 100:
        raise ApiError("limit cannot exceed 100.")
    try:
        offset = int(request.args.get("offset", "0"))
    except (TypeError, ValueError):
        raise ApiError("offset must be a non-negative integer.") from None
    if offset < 0:
        raise ApiError("offset must be a non-negative integer.")
    rows = fetch_all(
        """
        SELECT inspection_id, inspected_at, status, image_path, user_id,
               username, medicine_id, medicine_name, batch_id, batch_number,
               error_count, review_finding_count
        FROM v_inspection_summary
        WHERE batch_id = %s
        ORDER BY inspected_at DESC, inspection_id DESC
        LIMIT %s OFFSET %s
        """,
        (batch_id, limit, offset),
    )
    return jsonify({"data": json_safe(rows), "limit": limit, "offset": offset})
