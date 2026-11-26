from flask import Blueprint, jsonify

from database.connection import fetch_all, fetch_one
from services.auth import login_required
from services.validation import json_safe


dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@dashboard_bp.get("/summary")
@login_required
def summary():
    statistics = fetch_one(
        """
        SELECT
            COUNT(*) AS total_inspections,
            COUNT(CASE WHEN status = 'MATCH' THEN 1 END) AS match_count,
            COUNT(CASE WHEN status = 'MISMATCH' THEN 1 END) AS mismatch_count,
            COUNT(CASE WHEN status = 'NEEDS_REVIEW' THEN 1 END) AS needs_review_count,
            COUNT(CASE WHEN status = 'EXPIRED' THEN 1 END) AS expired_count
        FROM Packaging_Inspections
        """
    )
    recent_inspections = fetch_all(
        """
        SELECT inspection_id, inspected_at, status, medicine_name,
               batch_number, error_count, review_finding_count
        FROM v_inspection_summary
        ORDER BY inspected_at DESC, inspection_id DESC
        LIMIT 8
        """
    )
    monthly_trend = fetch_all(
        """
        SELECT DATE_FORMAT(inspected_at, '%Y-%m') AS month,
               COUNT(*) AS inspection_count,
               COUNT(CASE WHEN status = 'MATCH' THEN 1 END) AS match_count,
               COUNT(CASE WHEN status = 'MISMATCH' THEN 1 END) AS mismatch_count,
               COUNT(CASE WHEN status = 'NEEDS_REVIEW' THEN 1 END) AS review_count,
               COUNT(CASE WHEN status = 'EXPIRED' THEN 1 END) AS expired_count
        FROM Packaging_Inspections
        WHERE inspected_at >= DATE_FORMAT(
            DATE_SUB(CURDATE(), INTERVAL 11 MONTH), '%Y-%m-01'
        )
        GROUP BY DATE_FORMAT(inspected_at, '%Y-%m')
        ORDER BY month
        """
    )
    common_errors = fetch_all(
        """
        SELECT error_type, severity, occurrence_count, most_recent_at
        FROM v_error_type_summary
        ORDER BY occurrence_count DESC, most_recent_at DESC
        LIMIT 8
        """
    )
    batch_summaries = fetch_all(
        """
        SELECT b.batch_id, b.batch_number, b.expiry_date,
               m.medicine_name, mf.manufacturer_name,
               COUNT(i.inspection_id) AS inspection_count,
               COUNT(CASE WHEN i.status = 'MATCH' THEN 1 END) AS match_count,
               COUNT(CASE WHEN i.status = 'MISMATCH' THEN 1 END) AS mismatch_count,
               COUNT(CASE WHEN i.status = 'NEEDS_REVIEW' THEN 1 END) AS review_count,
               COUNT(CASE WHEN i.status = 'EXPIRED' THEN 1 END) AS expired_count
        FROM Medicine_Batches AS b
        INNER JOIN Medicines AS m ON m.medicine_id = b.medicine_id
        INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        LEFT JOIN Packaging_Inspections AS i
            ON i.batch_id = b.batch_id AND i.medicine_id = b.medicine_id
        GROUP BY b.batch_id, b.batch_number, b.expiry_date,
                 m.medicine_name, mf.manufacturer_name
        ORDER BY inspection_count DESC, b.expiry_date
        LIMIT 8
        """
    )
    expired_batch_alerts = fetch_all(
        """
        SELECT b.batch_id, b.batch_number, b.expiry_date,
               m.medicine_name, mf.manufacturer_name,
               DATEDIFF(b.expiry_date, CURDATE()) AS days_until_expiry
        FROM Medicine_Batches AS b
        INNER JOIN Medicines AS m ON m.medicine_id = b.medicine_id
        INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        WHERE b.expiry_date < CURDATE()
        ORDER BY b.expiry_date DESC, m.medicine_name
        LIMIT 5
        """
    )
    expiring_batch_alerts = fetch_all(
        """
        SELECT b.batch_id, b.batch_number, b.expiry_date,
               m.medicine_name, mf.manufacturer_name,
               DATEDIFF(b.expiry_date, CURDATE()) AS days_until_expiry
        FROM Medicine_Batches AS b
        INNER JOIN Medicines AS m ON m.medicine_id = b.medicine_id
        INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        WHERE b.expiry_date BETWEEN CURDATE()
            AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)
        ORDER BY b.expiry_date, m.medicine_name
        LIMIT 5
        """
    )
    duplicate_serials = fetch_all(
        """
        SELECT scans.serial_number,
               COUNT(DISTINCT scans.inspection_id) AS inspection_count,
               GROUP_CONCAT(
                   DISTINCT scans.inspection_id
                   ORDER BY scans.inspection_id DESC SEPARATOR ', '
               ) AS inspection_ids
        FROM (
            SELECT d.inspection_id, decoded_code.serial_number
            FROM Detected_Details AS d
            INNER JOIN JSON_TABLE(
                CASE
                    WHEN JSON_VALID(d.detected_value) THEN d.detected_value
                    ELSE '{}'
                END,
                '$.codes[*]' COLUMNS (
                    serial_number VARCHAR(128)
                        PATH '$.identifiers.serial_number' NULL ON EMPTY
                )
            ) AS decoded_code
            WHERE d.field_name = 'barcode'
        ) AS scans
        WHERE scans.serial_number IS NOT NULL AND scans.serial_number <> ''
        GROUP BY scans.serial_number
        HAVING COUNT(DISTINCT scans.inspection_id) > 1
        ORDER BY inspection_count DESC
        LIMIT 10
        """
    )
    return jsonify(
        {
            "data": {
                "statistics": json_safe(statistics),
                "recent_inspections": json_safe(recent_inspections),
                "monthly_trend": json_safe(monthly_trend),
                "common_errors": json_safe(common_errors),
                "batch_summaries": json_safe(batch_summaries),
                "expiry_alerts": json_safe(
                    expired_batch_alerts + expiring_batch_alerts
                ),
                "duplicate_serials": json_safe(duplicate_serials),
            }
        }
    )
