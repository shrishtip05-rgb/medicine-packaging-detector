from flask import Blueprint, jsonify, request

from database.connection import execute_write, fetch_all, fetch_one
from services.api_errors import ApiError
from services.auth import login_required, roles_required
from services.validation import (
    json_object,
    json_safe,
    optional_email,
    optional_text,
    positive_int,
    required_text,
)


catalog_bp = Blueprint("catalog", __name__, url_prefix="/api")


@catalog_bp.get("/manufacturers")
@login_required
def list_manufacturers():
    search = request.args.get("q", "").strip()
    manufacturers = fetch_all(
        """
        SELECT manufacturer_id, manufacturer_name, contact_email,
               contact_phone, address, created_at, updated_at
        FROM Manufacturers
        WHERE manufacturer_name LIKE %s
        ORDER BY manufacturer_name
        """,
        (f"%{search}%",),
    )
    return jsonify({"data": json_safe(manufacturers)})


@catalog_bp.post("/manufacturers")
@login_required
@roles_required("admin")
def create_manufacturer():
    payload = json_object()
    name = required_text(payload, "manufacturer_name", 180)
    email = optional_email(payload.get("contact_email"))
    phone = optional_text(payload, "contact_phone", 32)
    address = optional_text(payload, "address", 500)
    result = execute_write(
        """
        INSERT INTO Manufacturers
            (manufacturer_name, contact_email, contact_phone, address)
        VALUES (%s, %s, %s, %s)
        """,
        (name, email, phone, address),
    )
    return jsonify({"data": {"manufacturer_id": result["lastrowid"]}}), 201


@catalog_bp.put("/manufacturers/<int:manufacturer_id>")
@login_required
@roles_required("admin")
def update_manufacturer(manufacturer_id):
    payload = json_object()
    name = required_text(payload, "manufacturer_name", 180)
    email = optional_email(payload.get("contact_email"))
    phone = optional_text(payload, "contact_phone", 32)
    address = optional_text(payload, "address", 500)
    result = execute_write(
        """
        UPDATE Manufacturers
        SET manufacturer_name = %s, contact_email = %s,
            contact_phone = %s, address = %s
        WHERE manufacturer_id = %s
        """,
        (name, email, phone, address, manufacturer_id),
    )
    if result["rowcount"] == 0:
        exists = fetch_one(
            "SELECT manufacturer_id FROM Manufacturers WHERE manufacturer_id = %s",
            (manufacturer_id,),
        )
        if exists is None:
            raise ApiError("Manufacturer not found.", 404)
    return jsonify({"message": "Manufacturer updated."})


@catalog_bp.delete("/manufacturers/<int:manufacturer_id>")
@login_required
@roles_required("admin")
def delete_manufacturer(manufacturer_id):
    result = execute_write(
        "DELETE FROM Manufacturers WHERE manufacturer_id = %s",
        (manufacturer_id,),
    )
    if result["rowcount"] == 0:
        raise ApiError("Manufacturer not found.", 404)
    return "", 204


@catalog_bp.get("/medicines")
@login_required
def list_medicines():
    search = request.args.get("q", "").strip()
    medicines = fetch_all(
        """
        SELECT m.medicine_id, m.manufacturer_id, mf.manufacturer_name,
               m.medicine_name, m.strength, m.dosage_form,
               m.package_barcode, m.created_at, m.updated_at
        FROM Medicines AS m
        INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        WHERE m.medicine_name LIKE %s
        ORDER BY m.medicine_name, m.strength
        """,
        (f"%{search}%",),
    )
    return jsonify({"data": json_safe(medicines)})


@catalog_bp.get("/medicines/<int:medicine_id>")
@login_required
def get_medicine(medicine_id):
    medicine = fetch_one(
        """
        SELECT m.medicine_id, m.manufacturer_id, mf.manufacturer_name,
               m.medicine_name, m.strength, m.dosage_form,
               m.package_barcode, m.created_at, m.updated_at
        FROM Medicines AS m
        INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
        WHERE m.medicine_id = %s
        """,
        (medicine_id,),
    )
    if medicine is None:
        raise ApiError("Medicine not found.", 404)
    return jsonify({"data": json_safe(medicine)})


def _medicine_values(payload):
    return (
        positive_int(payload.get("manufacturer_id"), "manufacturer_id"),
        required_text(payload, "medicine_name", 160),
        required_text(payload, "strength", 80),
        required_text(payload, "dosage_form", 80),
        optional_text(payload, "package_barcode", 64),
    )


@catalog_bp.post("/medicines")
@login_required
@roles_required("admin")
def create_medicine():
    values = _medicine_values(json_object())
    result = execute_write(
        """
        INSERT INTO Medicines
            (manufacturer_id, medicine_name, strength, dosage_form, package_barcode)
        VALUES (%s, %s, %s, %s, %s)
        """,
        values,
    )
    return jsonify({"data": {"medicine_id": result["lastrowid"]}}), 201


@catalog_bp.put("/medicines/<int:medicine_id>")
@login_required
@roles_required("admin")
def update_medicine(medicine_id):
    values = _medicine_values(json_object())
    result = execute_write(
        """
        UPDATE Medicines
        SET manufacturer_id = %s, medicine_name = %s, strength = %s,
            dosage_form = %s, package_barcode = %s
        WHERE medicine_id = %s
        """,
        (*values, medicine_id),
    )
    if result["rowcount"] == 0:
        exists = fetch_one(
            "SELECT medicine_id FROM Medicines WHERE medicine_id = %s",
            (medicine_id,),
        )
        if exists is None:
            raise ApiError("Medicine not found.", 404)
    return jsonify({"message": "Medicine updated."})


@catalog_bp.delete("/medicines/<int:medicine_id>")
@login_required
@roles_required("admin")
def delete_medicine(medicine_id):
    result = execute_write(
        "DELETE FROM Medicines WHERE medicine_id = %s",
        (medicine_id,),
    )
    if result["rowcount"] == 0:
        raise ApiError("Medicine not found.", 404)
    return "", 204
