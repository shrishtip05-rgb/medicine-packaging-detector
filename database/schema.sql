-- AI-Based Visual Medicine Packaging Error Detector
-- Schema target: MySQL 8.0.16+ (CHECK constraints are enforced from 8.0.16).
-- This file is a setup script; review it before running against a database.

CREATE DATABASE IF NOT EXISTS medicine_packaging_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE medicine_packaging_db;

CREATE TABLE IF NOT EXISTS Users (
    user_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(80) NOT NULL,
    email VARCHAR(254) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('admin', 'pharmacist') NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_username (username),
    UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Manufacturers (
    manufacturer_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    manufacturer_name VARCHAR(180) NOT NULL,
    contact_email VARCHAR(254) NULL,
    contact_phone VARCHAR(32) NULL,
    address VARCHAR(500) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (manufacturer_id),
    UNIQUE KEY uq_manufacturers_name (manufacturer_name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Medicines (
    medicine_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    manufacturer_id BIGINT UNSIGNED NOT NULL,
    medicine_name VARCHAR(160) NOT NULL,
    strength VARCHAR(80) NOT NULL,
    dosage_form VARCHAR(80) NOT NULL,
    package_barcode VARCHAR(64) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (medicine_id),
    UNIQUE KEY uq_medicine_identity (
        manufacturer_id, medicine_name, strength, dosage_form
    ),
    UNIQUE KEY uq_medicines_package_barcode (package_barcode),
    KEY idx_medicines_name (medicine_name),
    CONSTRAINT fk_medicines_manufacturer
        FOREIGN KEY (manufacturer_id)
        REFERENCES Manufacturers (manufacturer_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Medicine_Batches (
    batch_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    medicine_id BIGINT UNSIGNED NOT NULL,
    batch_number VARCHAR(80) NOT NULL,
    manufactured_on DATE NOT NULL,
    expiry_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (batch_id),
    UNIQUE KEY uq_batch_number_per_medicine (medicine_id, batch_number),
    -- Required as the referenced key for the inspection's medicine/batch pair.
    UNIQUE KEY uq_batch_medicine_pair (medicine_id, batch_id),
    KEY idx_batches_expiry_date (expiry_date),
    CONSTRAINT chk_batch_date_order
        CHECK (manufactured_on <= expiry_date),
    CONSTRAINT fk_batches_medicine
        FOREIGN KEY (medicine_id)
        REFERENCES Medicines (medicine_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Packaging_Inspections (
    inspection_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    -- The selected expected medicine and batch can be absent when identification
    -- is uncertain. If a batch is supplied, its medicine must match medicine_id.
    medicine_id BIGINT UNSIGNED NULL,
    batch_id BIGINT UNSIGNED NULL,
    image_path VARCHAR(500) NOT NULL,
    status ENUM('MATCH', 'MISMATCH', 'EXPIRED', 'NEEDS_REVIEW') NOT NULL
        DEFAULT 'NEEDS_REVIEW',
    inspected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (inspection_id),
    KEY idx_inspections_status_date (status, inspected_at),
    KEY idx_inspections_user_date (user_id, inspected_at),
    CONSTRAINT chk_inspection_batch_requires_medicine
        CHECK (batch_id IS NULL OR medicine_id IS NOT NULL),
    CONSTRAINT fk_inspections_user
        FOREIGN KEY (user_id)
        REFERENCES Users (user_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT fk_inspections_medicine
        FOREIGN KEY (medicine_id)
        REFERENCES Medicines (medicine_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT fk_inspections_medicine_batch
        FOREIGN KEY (medicine_id, batch_id)
        REFERENCES Medicine_Batches (medicine_id, batch_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Detected_Details (
    detected_detail_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    inspection_id BIGINT UNSIGNED NOT NULL,
    field_name ENUM(
        'medicine_name',
        'manufacturer',
        'strength',
        'dosage_form',
        'batch_number',
        'manufactured_on',
        'expiry_date',
        'barcode'
    ) NOT NULL,
    detected_value TEXT NULL,
    extraction_status ENUM('READ', 'UNREADABLE', 'NOT_FOUND') NOT NULL,
    ocr_confidence DECIMAL(5, 2) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (detected_detail_id),
    UNIQUE KEY uq_detected_detail_per_field (inspection_id, field_name),
    CONSTRAINT chk_ocr_confidence_range
        CHECK (ocr_confidence IS NULL OR ocr_confidence BETWEEN 0 AND 100),
    CONSTRAINT chk_detected_value_matches_status
        CHECK (
            (extraction_status = 'READ' AND detected_value IS NOT NULL)
            OR
            (extraction_status IN ('UNREADABLE', 'NOT_FOUND')
                AND detected_value IS NULL)
        ),
    CONSTRAINT fk_detected_details_inspection
        FOREIGN KEY (inspection_id)
        REFERENCES Packaging_Inspections (inspection_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS Error_Logs (
    error_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    inspection_id BIGINT UNSIGNED NOT NULL,
    error_type ENUM(
        'medicine_name',
        'strength',
        'manufacturer',
        'dosage_form',
        'batch_number',
        'expiry_date',
        'expired_batch',
        'barcode'
    ) NOT NULL,
    -- NULL expected_value means no registered expected value is known.
    -- NULL detected_value means OCR supplied no usable value; its extraction
    -- status is retained in Detected_Details.
    expected_value TEXT NULL,
    detected_value TEXT NULL,
    severity ENUM('low', 'medium', 'high', 'critical') NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (error_id),
    KEY idx_errors_inspection_type (inspection_id, error_type),
    KEY idx_errors_type_severity (error_type, severity),
    CONSTRAINT fk_error_logs_inspection
        FOREIGN KEY (inspection_id)
        REFERENCES Packaging_Inspections (inspection_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
) ENGINE=InnoDB;

-- A report-friendly view: one row per inspection, including zero-error reports.
CREATE OR REPLACE VIEW v_inspection_summary AS
SELECT
    i.inspection_id,
    i.inspected_at,
    i.status,
    i.image_path,
    i.user_id,
    u.username,
    i.medicine_id,
    m.medicine_name,
    i.batch_id,
    b.batch_number,
    COUNT(
        CASE
            WHEN e.expected_value IS NOT NULL AND e.detected_value IS NOT NULL
                THEN e.error_id
        END
    ) AS error_count,
    COUNT(
        CASE
            WHEN e.error_id IS NOT NULL
                AND (e.expected_value IS NULL OR e.detected_value IS NULL)
                THEN e.error_id
        END
    ) AS review_finding_count
FROM Packaging_Inspections AS i
INNER JOIN Users AS u ON u.user_id = i.user_id
LEFT JOIN Medicines AS m ON m.medicine_id = i.medicine_id
LEFT JOIN Medicine_Batches AS b ON b.batch_id = i.batch_id
LEFT JOIN Error_Logs AS e ON e.inspection_id = i.inspection_id
GROUP BY
    i.inspection_id,
    i.inspected_at,
    i.status,
    i.image_path,
    i.user_id,
    u.username,
    i.medicine_id,
    m.medicine_name,
    i.batch_id,
    b.batch_number;

-- Supports the dashboard's most-common-error chart.
CREATE OR REPLACE VIEW v_error_type_summary AS
SELECT
    error_type,
    severity,
    COUNT(*) AS occurrence_count,
    MAX(created_at) AS most_recent_at
FROM Error_Logs
GROUP BY error_type, severity;

-- Status precedence: expired batch, then confirmed mismatch, then needs review.
-- MATCH is assigned by the application only after all required checks pass.
DELIMITER //
DROP TRIGGER IF EXISTS trg_error_marks_inspection_mismatch//
CREATE TRIGGER trg_error_marks_inspection_mismatch
AFTER INSERT ON Error_Logs
FOR EACH ROW
BEGIN
    UPDATE Packaging_Inspections
    SET status = CASE
        WHEN NEW.error_type = 'expired_batch' OR status = 'EXPIRED'
            THEN 'EXPIRED'
        WHEN status = 'MISMATCH'
            THEN 'MISMATCH'
        WHEN NEW.expected_value IS NOT NULL AND NEW.detected_value IS NOT NULL
            THEN 'MISMATCH'
        ELSE 'NEEDS_REVIEW'
    END
    WHERE inspection_id = NEW.inspection_id;
END//

-- Findings are append-only. Correct a result with a new inspection rather than
-- changing or removing the original audit record.
DROP TRIGGER IF EXISTS trg_error_logs_reject_update//
CREATE TRIGGER trg_error_logs_reject_update
BEFORE UPDATE ON Error_Logs
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Error_Logs is append-only; create a corrective inspection';
END//

DROP TRIGGER IF EXISTS trg_error_logs_reject_delete//
CREATE TRIGGER trg_error_logs_reject_delete
BEFORE DELETE ON Error_Logs
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'Error_Logs is append-only; create a corrective inspection';
END//
DELIMITER ;

-- Synthetic reference data only; no real medicines or manufacturers are implied.
-- Seed inserts skip existing natural-key records rather than updating them.
-- A conflicting unique barcode or other unique key remains an explicit error.
INSERT INTO Manufacturers
    (manufacturer_name, contact_email, contact_phone, address)
SELECT
    'Example Pharma Labs (SYNTHETIC)', NULL, NULL, 'Synthetic sample record'
WHERE NOT EXISTS (
    SELECT 1
    FROM Manufacturers
    WHERE manufacturer_name = 'Example Pharma Labs (SYNTHETIC)'
);

INSERT INTO Manufacturers
    (manufacturer_name, contact_email, contact_phone, address)
SELECT
    'Demo Health Products (SYNTHETIC)', NULL, NULL, 'Synthetic sample record'
WHERE NOT EXISTS (
    SELECT 1
    FROM Manufacturers
    WHERE manufacturer_name = 'Demo Health Products (SYNTHETIC)'
);

INSERT INTO Medicines
    (manufacturer_id, medicine_name, strength, dosage_form, package_barcode)
SELECT mf.manufacturer_id, 'Samplecet', '500 mg', 'tablet', 'SYNTHETIC-0001'
FROM Manufacturers AS mf
WHERE mf.manufacturer_name = 'Example Pharma Labs (SYNTHETIC)'
  AND NOT EXISTS (
      SELECT 1
      FROM Medicines AS m
      WHERE m.manufacturer_id = mf.manufacturer_id
        AND m.medicine_name = 'Samplecet'
        AND m.strength = '500 mg'
        AND m.dosage_form = 'tablet'
  );

INSERT INTO Medicines
    (manufacturer_id, medicine_name, strength, dosage_form, package_barcode)
SELECT mf.manufacturer_id, 'Democillin', '250 mg', 'capsule', NULL
FROM Manufacturers AS mf
WHERE mf.manufacturer_name = 'Demo Health Products (SYNTHETIC)'
  AND NOT EXISTS (
      SELECT 1
      FROM Medicines AS m
      WHERE m.manufacturer_id = mf.manufacturer_id
        AND m.medicine_name = 'Democillin'
        AND m.strength = '250 mg'
        AND m.dosage_form = 'capsule'
  );

INSERT INTO Medicine_Batches
    (medicine_id, batch_number, manufactured_on, expiry_date)
SELECT m.medicine_id, 'SYN-SC-2601', '2026-01-01', '2028-01-01'
FROM Medicines AS m
INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
WHERE mf.manufacturer_name = 'Example Pharma Labs (SYNTHETIC)'
  AND m.medicine_name = 'Samplecet'
  AND m.strength = '500 mg'
  AND m.dosage_form = 'tablet'
  AND NOT EXISTS (
      SELECT 1
      FROM Medicine_Batches AS b
      WHERE b.medicine_id = m.medicine_id
        AND b.batch_number = 'SYN-SC-2601'
  );

INSERT INTO Medicine_Batches
    (medicine_id, batch_number, manufactured_on, expiry_date)
SELECT m.medicine_id, 'SYN-DC-2602', '2026-02-01', '2028-02-01'
FROM Medicines AS m
INNER JOIN Manufacturers AS mf ON mf.manufacturer_id = m.manufacturer_id
WHERE mf.manufacturer_name = 'Demo Health Products (SYNTHETIC)'
  AND m.medicine_name = 'Democillin'
  AND m.strength = '250 mg'
  AND m.dosage_form = 'capsule'
  AND NOT EXISTS (
      SELECT 1
      FROM Medicine_Batches AS b
      WHERE b.medicine_id = m.medicine_id
        AND b.batch_number = 'SYN-DC-2602'
  );
