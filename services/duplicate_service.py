def find_duplicate_serials(cursor, current_inspection_id, codes):
    serials = sorted(
        {
            code.get("identifiers", {}).get("serial_number")
            for code in codes
            if code.get("identifiers", {}).get("serial_number")
        }
    )
    if not serials:
        return []

    placeholders = ", ".join(["%s"] * len(serials))
    cursor.execute(
        f"""
        SELECT DISTINCT d.inspection_id, decoded_code.serial_number
        FROM Detected_Details AS d
        INNER JOIN JSON_TABLE(
            CASE
                WHEN JSON_VALID(d.detected_value) THEN d.detected_value
                ELSE '{{}}'
            END,
            '$.codes[*]' COLUMNS (
                serial_number VARCHAR(128)
                    PATH '$.identifiers.serial_number' NULL ON EMPTY
            )
        ) AS decoded_code
        WHERE d.field_name = 'barcode'
          AND decoded_code.serial_number IN ({placeholders})
          AND d.inspection_id <> %s
        ORDER BY d.inspection_id DESC
        """,
        (*serials, current_inspection_id),
    )
    return [
        {
            "serial_number": row["serial_number"],
            "other_inspection_id": row["inspection_id"],
        }
        for row in cursor.fetchall()
    ]
