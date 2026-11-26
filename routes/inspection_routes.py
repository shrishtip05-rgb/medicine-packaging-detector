import json

from flask import Blueprint, current_app, g, jsonify, request, send_file

from database.connection import cursor, fetch_all, fetch_one, transaction
from services.api_errors import ApiError
from services.auth import login_required
from services.comparison_service import compare_inspection_fields
from services.code_scanner import CodeScanError, compare_scanned_codes, scan_codes
from services.duplicate_service import find_duplicate_serials
from services.image_service import (
    StoredImageError,
    resolve_stored_image_path,
    save_package_image,
)
from services.image_quality import ImageQualityError, assess_image_quality
from services.ocr_service import (
    OcrEngineUnavailable,
    OcrImageError,
    OcrProcessingError,
    run_ocr,
)
from services.validation import (
    json_object,
    json_safe,
    ocr_confidence,
    optional_positive_int,
    positive_int,
)


inspection_bp = Blueprint("inspections", __name__, url_prefix="/api")

_FIELDS = {
    "medicine_name",
    "manufacturer",
    "strength",
    "dosage_form",
    "batch_number",
    "manufactured_on",
    "expiry_date",
    "barcode",
}
_EXTRACTION_STATUSES = {"READ", "UNREADABLE", "NOT_FOUND"}
_ERROR_TYPES = {
    "medicine_name",
    "strength",
    "manufacturer",
    "dosage_form",
    "batch_number",
    "expiry_date",
    "expired_batch",
    "barcode",
}
_SEVERITIES = {"low", "medium", "high", "critical"}
_INSPECTION_STATUSES = {"MATCH", "MISMATCH", "NEEDS_REVIEW", "EXPIRED"}


def _assess_stored_image(path):
    return assess_image_quality(
        path,
        blur_variance_min=current_app.config["IMAGE_BLUR_VARIANCE_MIN"],
        dark_mean_max=current_app.config["IMAGE_DARK_MEAN_MAX"],
        bright_mean_min=current_app.config["IMAGE_BRIGHT_MEAN_MIN"],
        min_width=current_app.config["IMAGE_MIN_WIDTH"],
        min_height=current_app.config["IMAGE_MIN_HEIGHT"],
        max_tilt_degrees=current_app.config["IMAGE_MAX_TILT_DEGREES"],
    )


@inspection_bp.get("/health/database")
def database_health():
    row = fetch_one("SELECT VERSION() AS server_version")
    return jsonify({"database": "connected", "server_version": row["server_version"]})


@inspection_bp.post("/inspections")
@login_required
def create_inspection():
    upload = request.files.get("image")
    medicine_id = optional_positive_int(
        request.form.get("medicine_id"), "medicine_id"
    )
    batch_id = optional_positive_int(request.form.get("batch_id"), "batch_id")
    if batch_id is not None and medicine_id is None:
        raise ApiError("medicine_id is required when batch_id is provided.")

    destination, relative_path = save_package_image(
        upload, current_app.config["UPLOAD_FOLDER"], current_app.root_path
    )
    try:
        with transaction() as connection:
            with cursor(connection=connection) as cur:
                if medicine_id is not None:
                    cur.execute(
                        "SELECT medicine_id FROM Medicines WHERE medicine_id = %s",
                        (medicine_id,),
                    )
                    if cur.fetchone() is None:
                        raise ApiError("Medicine not found.", 404)
                if batch_id is not None:
                    cur.execute(
                        """
                        SELECT batch_id
                        FROM Medicine_Batches
                        WHERE batch_id = %s AND medicine_id = %s
                        """,
                        (batch_id, medicine_id),
                    )
                    if cur.fetchone() is None:
                        raise ApiError("Batch does not belong to the selected medicine.", 400)
                cur.execute(
                    """
                    INSERT INTO Packaging_Inspections
                        (user_id, medicine_id, batch_id, image_path, status)
                    VALUES (%s, %s, %s, %s, 'NEEDS_REVIEW')
                    """,
                    (
                        g.current_user["user_id"],
                        medicine_id,
                        batch_id,
                        relative_path,
                    ),
                )
                inspection_id = cur.lastrowid
    except Exception:
        if destination.exists():
            try:
                destination.unlink()
            except OSError:
                current_app.logger.exception(
                    "Could not remove upload after failed inspection creation."
                )
        raise

    return (
        jsonify(
            {
                "data": {
                    "inspection_id": inspection_id,
                    "status": "NEEDS_REVIEW",
                    "image_path": relative_path,
                    "ocr_status": "not_run",
                },
                "message": "Image stored for inspection; OCR has not been run yet.",
            }
        ),
        202,
    )


@inspection_bp.post("/inspections/<int:inspection_id>/ocr")
@login_required
def process_inspection_ocr(inspection_id):
    inspection_id = positive_int(inspection_id, "inspection_id")
    inspection = fetch_one(
        """
        SELECT i.image_path, i.status,
               (SELECT COUNT(*) FROM Detected_Details AS d
                WHERE d.inspection_id = i.inspection_id) AS detail_count,
               (SELECT COUNT(*) FROM Error_Logs AS e
                WHERE e.inspection_id = i.inspection_id) AS error_count
        FROM Packaging_Inspections AS i
        WHERE i.inspection_id = %s
        """,
        (inspection_id,),
    )
    if inspection is None:
        raise ApiError("Inspection not found.", 404)
    if inspection["status"] != "NEEDS_REVIEW":
        raise ApiError("OCR can only run for a NEEDS_REVIEW inspection.", 409)
    if inspection["detail_count"] or inspection["error_count"]:
        raise ApiError("Results already exist for this inspection.", 409)

    try:
        image_path = resolve_stored_image_path(
            inspection["image_path"], current_app.config["UPLOAD_FOLDER"]
        )
    except StoredImageError as exc:
        raise ApiError("The stored inspection image is unavailable or invalid.", 422) from exc

    try:
        image_quality = _assess_stored_image(image_path)
    except ImageQualityError as exc:
        raise ApiError("The inspection image could not be assessed.", 422) from exc
    if image_quality["blocked"]:
        return (
            jsonify(
                {
                    "error": (
                        "Image quality is unsuitable for OCR. Upload a clearer, "
                        "well-lit, higher-resolution package image."
                    ),
                    "data": {"image_quality": image_quality},
                }
            ),
            422,
        )

    try:
        result = run_ocr(
            image_path,
            tesseract_cmd=current_app.config.get("TESSERACT_CMD"),
            max_pixels=current_app.config.get("OCR_MAX_PIXELS", 25_000_000),
        )
    except OcrEngineUnavailable as exc:
        raise ApiError(
            "Tesseract is unavailable. Install it or set TESSERACT_CMD to the "
            "executable path.", 503
        ) from exc
    except OcrImageError as exc:
        raise ApiError("The stored inspection image is unreadable or corrupt.", 422) from exc
    except OcrProcessingError as exc:
        current_app.logger.error(
            "OCR processing failed (error type: %s).", type(exc).__name__
        )
        raise ApiError("OCR processing failed; no results were saved.", 503) from exc

    try:
        code_scan = scan_codes(image_path)
    except CodeScanError:
        code_scan = {
            "status": "NOT_FOUND",
            "codes": [],
            "duplicate_serials_in_image": [],
            "warnings": ["QR/barcode scan could not be completed."],
            "limitations": "Code decoding does not authenticate a manufacturer.",
        }
    code_scan["image_quality"] = image_quality

    with transaction() as connection:
        with cursor(connection=connection) as cur:
            cur.execute(
                """
                SELECT status, medicine_id, batch_id, inspected_at
                FROM Packaging_Inspections
                WHERE inspection_id = %s
                FOR UPDATE
                """,
                (inspection_id,),
            )
            locked_inspection = cur.fetchone()
            if locked_inspection is None:
                raise ApiError("Inspection not found.", 404)
            if locked_inspection["status"] != "NEEDS_REVIEW":
                raise ApiError("Inspection status changed; OCR results were not saved.", 409)

            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM Detected_Details
                     WHERE inspection_id = %s) AS detail_count,
                    (SELECT COUNT(*) FROM Error_Logs
                     WHERE inspection_id = %s) AS error_count
                """,
                (inspection_id, inspection_id),
            )
            existing_results = cur.fetchone()
            if existing_results["detail_count"] or existing_results["error_count"]:
                raise ApiError("Results already exist for this inspection.", 409)

            expected = {
                "medicine_name": None,
                "manufacturer": None,
                "strength": None,
                "dosage_form": None,
                "batch_number": None,
                "manufactured_on": None,
                "expiry_date": None,
                "package_barcode": None,
            }
            if locked_inspection["medicine_id"] is not None:
                cur.execute(
                    """
                    SELECT m.medicine_name, m.strength, m.dosage_form,
                           m.package_barcode, mf.manufacturer_name
                    FROM Medicines AS m
                    INNER JOIN Manufacturers AS mf
                        ON mf.manufacturer_id = m.manufacturer_id
                    WHERE m.medicine_id = %s
                    """,
                    (locked_inspection["medicine_id"],),
                )
                medicine = cur.fetchone()
                if medicine is None:
                    raise ApiError(
                        "The inspection's expected medicine is unavailable.", 409
                    )
                expected.update(
                    {
                        "medicine_name": medicine["medicine_name"],
                        "manufacturer": medicine["manufacturer_name"],
                        "strength": medicine["strength"],
                        "dosage_form": medicine["dosage_form"],
                        "package_barcode": medicine["package_barcode"],
                    }
                )

            if locked_inspection["batch_id"] is not None:
                if locked_inspection["medicine_id"] is None:
                    raise ApiError(
                        "A selected batch requires an expected medicine.", 409
                    )
                cur.execute(
                    """
                    SELECT batch_id, medicine_id, batch_number,
                           manufactured_on, expiry_date
                    FROM Medicine_Batches
                    WHERE batch_id = %s
                    """,
                    (locked_inspection["batch_id"],),
                )
                batch = cur.fetchone()
                if (
                    batch is None
                    or batch["medicine_id"] != locked_inspection["medicine_id"]
                ):
                    raise ApiError(
                        "The selected batch does not belong to the expected medicine.",
                        409,
                    )
                expected.update(
                    {
                        "batch_number": batch["batch_number"],
                        "manufactured_on": batch["manufactured_on"],
                        "expiry_date": batch["expiry_date"],
                    }
                )

            duplicates = find_duplicate_serials(
                cur, inspection_id, code_scan.get("codes", [])
            )
            code_scan["duplicate_serials_across_inspections"] = duplicates
            if duplicates:
                code_scan["warnings"].append(
                    "A serial identifier was found in another inspection; this may "
                    "also represent another inspection of the same package."
                )
            code_comparison = compare_scanned_codes(
                code_scan,
                expected.get("package_barcode"),
                expected.get("batch_number"),
                expected.get("expiry_date"),
                duplicates,
            )
            comparison = compare_inspection_fields(
                expected,
                result["fields"],
                current_app.config["OCR_MIN_CONFIDENCE"],
                locked_inspection["inspected_at"],
            )
            comparison["findings"].extend(code_comparison["findings"])
            comparison["error_logs"].extend(code_comparison["error_logs"])
            if "EXPIRED" in {comparison["status"], code_comparison["status"]}:
                comparison["status"] = "EXPIRED"
            elif "MISMATCH" in {comparison["status"], code_comparison["status"]}:
                comparison["status"] = "MISMATCH"
            elif "NEEDS_REVIEW" in {comparison["status"], code_comparison["status"]}:
                comparison["status"] = "NEEDS_REVIEW"

            for field_name, field_result in result["fields"].items():
                cur.execute(
                    """
                    INSERT INTO Detected_Details
                        (inspection_id, field_name, detected_value,
                         extraction_status, ocr_confidence)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        inspection_id,
                        field_name,
                        field_result["detected_value"],
                        field_result["extraction_status"],
                        field_result["ocr_confidence"],
                    ),
                )

            barcode_value = (
                json.dumps(code_scan, ensure_ascii=False, separators=(",", ":"))
                if code_scan["status"] == "READ"
                else None
            )
            cur.execute(
                """
                INSERT INTO Detected_Details
                    (inspection_id, field_name, detected_value,
                     extraction_status, ocr_confidence)
                VALUES (%s, 'barcode', %s, %s, NULL)
                """,
                (
                    inspection_id,
                    barcode_value,
                    "READ" if barcode_value is not None else "NOT_FOUND",
                ),
            )

            for error in comparison["error_logs"]:
                cur.execute(
                    """
                    INSERT INTO Error_Logs
                        (inspection_id, error_type, expected_value,
                         detected_value, severity)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        inspection_id,
                        error["error_type"],
                        error["expected_value"],
                        error["detected_value"],
                        error["severity"],
                    ),
                )

            if comparison["status"] == "MATCH":
                cur.execute(
                    """
                    UPDATE Packaging_Inspections
                    SET status = 'MATCH'
                    WHERE inspection_id = %s AND status = 'NEEDS_REVIEW'
                    """,
                    (inspection_id,),
                )
                if cur.rowcount != 1:
                    raise ApiError(
                        "Inspection status changed; comparison was not finalized.", 409
                    )

            cur.execute(
                "SELECT status FROM Packaging_Inspections WHERE inspection_id = %s",
                (inspection_id,),
            )
            final_status = cur.fetchone()["status"]
            if final_status != comparison["status"]:
                raise ApiError(
                    "Database status triggers did not agree with the comparison result.",
                    500,
                )

    return jsonify(
        {
            "data": {
                "inspection_id": inspection_id,
                "status": final_status,
                "ocr_status": "completed",
                "raw_text": result["raw_text"],
                "fields": result["fields"],
                "unsupported_fields": result["unsupported_fields"],
                "detected_detail_count": len(result["fields"]) + 1,
                "error_log_count": len(comparison["error_logs"]),
                "comparison": comparison,
                "image_quality": image_quality,
                "code_scan": code_scan,
                "duplicate_serials": duplicates,
            },
            "message": (
                "OCR fields were compared with the registered catalog and stored. "
                "This is not a medicine identity, packaging quality, or authenticity "
                "verification."
            ),
        }
    )


@inspection_bp.get("/inspections")
@login_required
def list_inspections():
    limit = positive_int(request.args.get("limit", 25), "limit")
    if limit > 100:
        raise ApiError("limit cannot exceed 100.")
    status_filter = request.args.get("status", "").strip().upper()
    if status_filter and status_filter not in _INSPECTION_STATUSES:
        raise ApiError("status must be MATCH, MISMATCH, NEEDS_REVIEW, or EXPIRED.")
    offset = request.args.get("offset", "0")
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        raise ApiError("offset must be a non-negative integer.") from None
    if offset < 0:
        raise ApiError("offset must be a non-negative integer.")

    search = request.args.get("q", "").strip()
    filters = []
    query_params = []
    if status_filter:
        filters.append("s.status = %s")
        query_params.append(status_filter)
    if search:
        filters.append(
            """
            (
                s.medicine_name LIKE %s OR s.batch_number LIKE %s
                OR EXISTS (
                    SELECT 1
                    FROM Medicines AS search_medicine
                    INNER JOIN Manufacturers AS search_manufacturer
                        ON search_manufacturer.manufacturer_id =
                           search_medicine.manufacturer_id
                    WHERE search_medicine.medicine_id = s.medicine_id
                      AND search_manufacturer.manufacturer_name LIKE %s
                )
            )
            """
        )
        search_pattern = f"%{search}%"
        query_params.extend((search_pattern, search_pattern, search_pattern))
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    query_params.extend((limit, offset))
    rows = fetch_all(
        f"""
        SELECT s.inspection_id, s.inspected_at, s.status, s.image_path, s.user_id,
               s.username, s.medicine_id, s.medicine_name, s.batch_id, s.batch_number,
               s.error_count, s.review_finding_count
        FROM v_inspection_summary AS s
        {where_clause}
        ORDER BY s.inspected_at DESC, s.inspection_id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(query_params),
    )
    return jsonify(
        {
            "data": json_safe(rows),
            "limit": limit,
            "offset": offset,
            "status": status_filter or None,
            "q": search or None,
        }
    )


@inspection_bp.get("/inspections/<int:inspection_id>/report.pdf")
@login_required
def download_inspection_report(inspection_id):
    inspection = fetch_one(
        """
        SELECT inspection_id, inspected_at, status, image_path,
               username, medicine_id, batch_id
        FROM v_inspection_summary
        WHERE inspection_id = %s
        """,
        (inspection_id,),
    )
    if inspection is None:
        raise ApiError("Inspection not found.", 404)

    expected = fetch_one(
        """
        SELECT m.medicine_name, mf.manufacturer_name AS manufacturer,
               m.strength, m.dosage_form, m.package_barcode AS barcode,
               b.batch_number, b.manufactured_on, b.expiry_date
        FROM Packaging_Inspections AS i
        LEFT JOIN Medicines AS m ON m.medicine_id = i.medicine_id
        LEFT JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        LEFT JOIN Medicine_Batches AS b
            ON b.batch_id = i.batch_id AND b.medicine_id = i.medicine_id
        WHERE i.inspection_id = %s
        """,
        (inspection_id,),
    )
    detected = fetch_all(
        """
        SELECT field_name, detected_value, extraction_status,
               ocr_confidence, created_at
        FROM Detected_Details
        WHERE inspection_id = %s
        ORDER BY field_name
        """,
        (inspection_id,),
    )
    errors = fetch_all(
        """
        SELECT error_type, expected_value, detected_value, severity, created_at
        FROM Error_Logs
        WHERE inspection_id = %s
        ORDER BY error_id
        """,
        (inspection_id,),
    )
    try:
        image_path = resolve_stored_image_path(
            inspection["image_path"], current_app.config["UPLOAD_FOLDER"]
        )
        image_quality = _assess_stored_image(image_path)
    except (StoredImageError, ImageQualityError):
        image_path = None
        image_quality = {
            "status": "UNAVAILABLE",
            "quality_score": 0,
            "dimensions": {"width": 0, "height": 0},
            "blur_variance": 0,
            "mean_luminance": 0,
            "recommendations": ["Stored image was unavailable for this report."],
        }

    from services.report_service import build_inspection_pdf

    pdf = build_inspection_pdf(
        inspection, expected or {}, detected, errors, image_path, image_quality
    )
    response = send_file(
        pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"inspection-{inspection_id}-report.pdf",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@inspection_bp.get("/inspections/<int:inspection_id>")
@login_required
def get_inspection(inspection_id):
    inspection = fetch_one(
        """
        SELECT inspection_id, inspected_at, status, image_path, user_id,
               username, medicine_id, medicine_name, batch_id, batch_number,
               error_count, review_finding_count
        FROM v_inspection_summary
        WHERE inspection_id = %s
        """,
        (inspection_id,),
    )
    if inspection is None:
        raise ApiError("Inspection not found.", 404)
    inspection["detected_details"] = fetch_all(
        """
        SELECT field_name, detected_value, extraction_status,
               ocr_confidence, created_at
        FROM Detected_Details
        WHERE inspection_id = %s
        ORDER BY field_name
        """,
        (inspection_id,),
    )
    inspection["error_logs"] = fetch_all(
        """
        SELECT error_id, error_type, expected_value, detected_value,
               severity, created_at
        FROM Error_Logs
        WHERE inspection_id = %s
        ORDER BY error_id
        """,
        (inspection_id,),
    )
    expected = fetch_one(
        """
        SELECT m.medicine_name, mf.manufacturer_name AS manufacturer,
               m.strength, m.dosage_form, b.batch_number,
               b.manufactured_on, b.expiry_date
        FROM Packaging_Inspections AS i
        LEFT JOIN Medicines AS m ON m.medicine_id = i.medicine_id
        LEFT JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        LEFT JOIN Medicine_Batches AS b
            ON b.batch_id = i.batch_id AND b.medicine_id = i.medicine_id
        WHERE i.inspection_id = %s
        """,
        (inspection_id,),
    )
    inspection["expected_details"] = expected
    try:
        image_path = resolve_stored_image_path(
            inspection["image_path"], current_app.config["UPLOAD_FOLDER"]
        )
        inspection["image_quality"] = _assess_stored_image(image_path)
    except (StoredImageError, ImageQualityError):
        inspection["image_quality"] = {
            "status": "UNAVAILABLE",
            "quality_score": None,
            "issues": [],
            "recommendations": [
                "The stored image is unavailable for quality assessment."
            ],
        }
    detected = {
        row["field_name"]: {
            "detected_value": row["detected_value"],
            "extraction_status": row["extraction_status"],
            "ocr_confidence": row["ocr_confidence"],
        }
        for row in inspection["detected_details"]
    }
    comparison = compare_inspection_fields(
        expected or {},
        detected,
        current_app.config["OCR_MIN_CONFIDENCE"],
        inspection["inspected_at"],
    )
    inspection["comparison_findings"] = comparison["findings"]
    inspection["confidence_threshold"] = comparison["confidence_threshold"]
    return jsonify({"data": json_safe(inspection)})


@inspection_bp.get("/inspections/<int:inspection_id>/image")
@login_required
def get_inspection_image(inspection_id):
    inspection = fetch_one(
        "SELECT image_path FROM Packaging_Inspections WHERE inspection_id = %s",
        (inspection_id,),
    )
    if inspection is None:
        raise ApiError("Inspection not found.", 404)
    try:
        image_path = resolve_stored_image_path(
            inspection["image_path"], current_app.config["UPLOAD_FOLDER"]
        )
    except StoredImageError as exc:
        raise ApiError("The inspection image is unavailable.", 404) from exc

    response = send_file(image_path, conditional=True, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@inspection_bp.post("/inspections/<int:inspection_id>/results")
@login_required
def store_external_results(inspection_id):
    """Persist results supplied by a caller; this endpoint does not run OCR."""
    payload = json_object()
    details = payload.get("detected_details")
    errors = payload.get("error_logs", [])
    if not isinstance(details, list) or not details:
        raise ApiError("detected_details must be a non-empty array.")
    if not isinstance(errors, list):
        raise ApiError("error_logs must be an array.")

    normalized_details = []
    seen_fields = set()
    for item in details:
        if not isinstance(item, dict):
            raise ApiError("Each detected detail must be a JSON object.")
        field_name = item.get("field_name")
        status = item.get("extraction_status")
        if field_name not in _FIELDS:
            raise ApiError("A detected detail has an unsupported field_name.")
        if field_name in seen_fields:
            raise ApiError("Each OCR field may be submitted only once.")
        if status not in _EXTRACTION_STATUSES:
            raise ApiError("A detected detail has an invalid extraction_status.")
        value = item.get("detected_value")
        if status == "READ":
            if not isinstance(value, str) or not value.strip():
                raise ApiError("READ details require a non-empty detected_value.")
            value = value.strip()
        elif value is not None:
            raise ApiError("UNREADABLE and NOT_FOUND values must be null.")
        confidence = ocr_confidence(item.get("ocr_confidence"))
        normalized_details.append((field_name, value, status, confidence))
        seen_fields.add(field_name)

    normalized_errors = []
    for item in errors:
        if not isinstance(item, dict):
            raise ApiError("Each error log must be a JSON object.")
        error_type = item.get("error_type")
        severity = item.get("severity")
        if error_type not in _ERROR_TYPES:
            raise ApiError("An error log has an unsupported error_type.")
        if severity not in _SEVERITIES:
            raise ApiError("An error log has an invalid severity.")
        expected = item.get("expected_value")
        detected = item.get("detected_value")
        for field, value in (("expected_value", expected), ("detected_value", detected)):
            if value is not None and (
                not isinstance(value, str) or len(value) > 4000
            ):
                raise ApiError(f"{field} must be text up to 4000 characters or null.")
        normalized_errors.append((error_type, expected, detected, severity))

    with transaction() as connection:
        with cursor(connection=connection) as cur:
            cur.execute(
                """
                SELECT status
                FROM Packaging_Inspections
                WHERE inspection_id = %s
                FOR UPDATE
                """,
                (inspection_id,),
            )
            inspection = cur.fetchone()
            if inspection is None:
                raise ApiError("Inspection not found.", 404)
            if inspection["status"] != "NEEDS_REVIEW":
                raise ApiError("Results can only be added to a NEEDS_REVIEW inspection.", 409)

            cur.execute(
                """
                SELECT COUNT(*) AS detail_count
                FROM Detected_Details
                WHERE inspection_id = %s
                """,
                (inspection_id,),
            )
            if cur.fetchone()["detail_count"]:
                raise ApiError("Results have already been submitted for this inspection.", 409)

            for detail in normalized_details:
                cur.execute(
                    """
                    INSERT INTO Detected_Details
                        (inspection_id, field_name, detected_value,
                         extraction_status, ocr_confidence)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (inspection_id, *detail),
                )
            for error in normalized_errors:
                cur.execute(
                    """
                    INSERT INTO Error_Logs
                        (inspection_id, error_type, expected_value,
                         detected_value, severity)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (inspection_id, *error),
                )
            cur.execute(
                "SELECT status FROM Packaging_Inspections WHERE inspection_id = %s",
                (inspection_id,),
            )
            status = cur.fetchone()["status"]

    return jsonify(
        {
            "data": {
                "inspection_id": inspection_id,
                "status": status,
                "detected_detail_count": len(normalized_details),
                "error_log_count": len(normalized_errors),
                "ocr_status": "not_performed_by_this_endpoint",
            },
            "message": "Supplied results stored; this API did not perform OCR or validation.",
        }
    )


