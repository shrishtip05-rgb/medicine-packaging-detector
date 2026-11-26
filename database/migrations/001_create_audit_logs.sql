-- Additive, backward-compatible audit log migration.
-- Does not alter existing tables, triggers, views, or records.
USE medicine_packaging_db;

CREATE TABLE IF NOT EXISTS Audit_Logs (
    audit_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    actor_user_id BIGINT UNSIGNED NULL,
    actor_username VARCHAR(80) NULL,
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(64) NOT NULL,
    target_id VARCHAR(128) NULL,
    details JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (audit_id),
    KEY idx_audit_created_at (created_at),
    KEY idx_audit_actor_time (actor_user_id, created_at),
    KEY idx_audit_target (target_type, target_id),
    CONSTRAINT fk_audit_actor
        FOREIGN KEY (actor_user_id)
        REFERENCES Users (user_id)
        ON UPDATE RESTRICT
        ON DELETE SET NULL
) ENGINE=InnoDB;
