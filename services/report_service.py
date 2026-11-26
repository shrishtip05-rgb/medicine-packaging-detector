import json
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#163A5F")
TEAL = colors.HexColor("#159A9C")
PALE = colors.HexColor("#F5F8FC")
BORDER = colors.HexColor("#E2E8F0")


def build_inspection_pdf(inspection, expected, detected, errors, image_path, quality):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"Medicine Packaging Inspection #{inspection['inspection_id']}",
        author="Medicine Packaging Detector",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            textColor=NAVY,
            fontSize=20,
            leading=25,
            alignment=TA_CENTER,
            spaceAfter=6 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeading",
            parent=styles["Heading2"],
            textColor=NAVY,
            fontSize=12,
            leading=15,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallText",
            parent=styles["BodyText"],
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#718096"),
        )
    )

    detected_by_field = {row["field_name"]: row for row in detected}
    barcode_result = {}
    barcode_row = detected_by_field.get("barcode")
    if barcode_row and barcode_row.get("detected_value"):
        try:
            barcode_result = json.loads(barcode_row["detected_value"])
        except (TypeError, ValueError):
            barcode_result = {"status": "UNREADABLE", "codes": []}

    story = [
        Paragraph("MEDICINE PACKAGING DETECTOR", styles["ReportTitle"]),
        Paragraph(
            f"Inspection Report #{escape(str(inspection['inspection_id']))}",
            styles["Heading2"],
        ),
        Spacer(1, 2 * mm),
    ]
    metadata = [
        ["Inspection date", _safe_text(inspection.get("inspected_at"))],
        ["Inspector", _safe_text(inspection.get("username"))],
        ["Status", _safe_text(inspection.get("status"))],
        ["Medicine", _safe_text(expected.get("medicine_name"))],
        ["Manufacturer", _safe_text(expected.get("manufacturer"))],
        ["Batch", _safe_text(expected.get("batch_number"))],
        ["Expiry", _safe_text(expected.get("expiry_date"))],
    ]
    story.extend(
        [
            _table(metadata, widths=[42 * mm, 130 * mm]),
            Paragraph("Expected versus detected fields", styles["SectionHeading"]),
        ]
    )

    field_rows = [["Field", "Expected", "Detected", "OCR status / confidence"]]
    for field in (
        "medicine_name",
        "manufacturer",
        "strength",
        "dosage_form",
        "batch_number",
        "manufactured_on",
        "expiry_date",
        "barcode",
    ):
        row = detected_by_field.get(field, {})
        value = row.get("detected_value")
        if field == "barcode" and value:
            value = _barcode_summary(value)
        expected_value = expected.get(field)
        confidence = row.get("ocr_confidence")
        status = row.get("extraction_status", "NOT_FOUND")
        confidence_text = f"{status} / {confidence}%" if confidence is not None else status
        field_rows.append(
            [
                _safe_text(field.replace("_", " ").title()),
                _safe_text(expected_value),
                _safe_text(value),
                _safe_text(confidence_text),
            ]
        )
    story.append(
        _table(
            field_rows,
            widths=[36 * mm, 45 * mm, 55 * mm, 36 * mm],
            repeat_rows=1,
        )
    )

    story.append(Paragraph("Image quality assessment", styles["SectionHeading"]))
    quality_text = (
        f"Status: {quality['status']} · Heuristic score: {quality['quality_score']}/100 · "
        f"Dimensions: {quality['dimensions']['width']}×{quality['dimensions']['height']} · "
        f"Laplacian variance: {quality['blur_variance']} · "
        f"Mean luminance: {quality['mean_luminance']}"
    )
    story.append(Paragraph(_safe_text(quality_text), styles["BodyText"]))
    for recommendation in quality["recommendations"]:
        story.append(Paragraph("• " + _safe_text(recommendation), styles["SmallText"]))

    story.append(Paragraph("QR / barcode scan", styles["SectionHeading"]))
    story.append(
        Paragraph(
            _safe_text(
                f"Scan status: {barcode_result.get('status', 'NOT_FOUND')}. "
                f"Decoded codes: {len(barcode_result.get('codes', []))}. "
                f"Repeated serial observations: "
                f"{len(barcode_result.get('duplicate_serials_across_inspections', []))}. "
                "Decoded content does not authenticate a manufacturer; QR links were "
                "not opened or checked against an external service."
            ),
            styles["BodyText"],
        )
    )
    duplicate_rows = barcode_result.get("duplicate_serials_across_inspections", [])
    for duplicate in duplicate_rows:
        story.append(
            Paragraph(
                _safe_text(
                    "Serial review: "
                    f"{duplicate.get('serial_number')} also appeared in inspection "
                    f"#{duplicate.get('other_inspection_id')}. This may be a repeat "
                    "inspection of the same package."
                ),
                styles["SmallText"],
            )
        )

    story.append(Paragraph("Recorded findings", styles["SectionHeading"]))
    if errors:
        error_rows = [["Finding", "Expected", "Detected", "Severity"]]
        for error in errors:
            error_rows.append(
                [
                    _safe_text(error.get("error_type")),
                    _safe_text(error.get("expected_value")),
                    _safe_text(error.get("detected_value")),
                    _safe_text(error.get("severity")),
                ]
            )
        story.append(
            _table(
                error_rows,
                widths=[36 * mm, 55 * mm, 55 * mm, 26 * mm],
                repeat_rows=1,
            )
        )
    else:
        story.append(Paragraph("No error log rows are recorded.", styles["BodyText"]))

    if image_path:
        story.append(Paragraph("Uploaded package image", styles["SectionHeading"]))
        image = Image(str(image_path), width=95 * mm, height=70 * mm, kind="proportional")
        story.append(image)

    story.extend(
        [
            Spacer(1, 5 * mm),
            Paragraph(
                "Screening prototype only. Text matches and decoded package codes do not "
                "prove medicine authenticity, manufacturer authorization, product quality, "
                "or pharmaceutical safety. Review this report with a qualified professional.",
                styles["SmallText"],
            ),
        ]
    )
    document.build(story, onFirstPage=_draw_page, onLaterPages=_draw_page)
    buffer.seek(0)
    return buffer


def _table(rows, widths, repeat_rows=0):
    cell_style = ParagraphStyle(
        name="ReportCell",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#263445"),
    )
    header_style = ParagraphStyle(
        name="ReportHeader",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    wrapped_rows = [
        [
            Paragraph(str(value), header_style if row_index == 0 else cell_style)
            for value in row
        ]
        for row_index, row in enumerate(rows)
    ]
    table = Table(
        wrapped_rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT"
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _draw_page(canvas, document):
    canvas.saveState()
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(1)
    canvas.line(16 * mm, 14 * mm, A4[0] - 16 * mm, 14 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(NAVY)
    canvas.drawString(16 * mm, 9 * mm, "Medicine Packaging Detector · Screening report")
    canvas.drawRightString(A4[0] - 16 * mm, 9 * mm, f"Page {document.page}")
    canvas.restoreState()


def _barcode_summary(serialized):
    try:
        data = json.loads(serialized)
    except (TypeError, ValueError):
        return "Stored scan data could not be parsed"
    return "; ".join(
        f"{code.get('format', 'CODE')}: {code.get('value', '')}"
        for code in data.get("codes", [])
    ) or data.get("status", "No code decoded")


def _safe_text(value):
    if value is None:
        return "Unavailable"
    value = str(value).encode("latin-1", "replace").decode("latin-1")
    return escape(value).replace("\n", "<br/>")
