import logging
import math
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify
from mysql.connector import Error as MySQLConnectorError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

load_dotenv()

from config import Config  # noqa: E402
from database.connection import DatabaseUnavailable, close_db  # noqa: E402
from routes.auth_routes import auth_bp, register_cli  # noqa: E402
from routes.audit_routes import audit_bp  # noqa: E402
from routes.batch_routes import batch_bp  # noqa: E402
from routes.dashboard_routes import dashboard_bp  # noqa: E402
from routes.inspection_routes import inspection_bp  # noqa: E402
from routes.medicine_routes import catalog_bp  # noqa: E402
from routes.web_routes import web_bp  # noqa: E402
from services.api_errors import ApiError  # noqa: E402
from services.audit_service import record_request_audit  # noqa: E402
from services.validation import json_safe  # noqa: E402


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    try:
        confidence_threshold = float(app.config["OCR_MIN_CONFIDENCE"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OCR_MIN_CONFIDENCE must be a number from 0 to 100.") from exc
    if not math.isfinite(confidence_threshold) or not 0 <= confidence_threshold <= 100:
        raise RuntimeError("OCR_MIN_CONFIDENCE must be a number from 0 to 100.")
    app.config["OCR_MIN_CONFIDENCE"] = confidence_threshold
    numeric_quality_settings = {
        "IMAGE_BLUR_VARIANCE_MIN": (0, float("inf")),
        "IMAGE_DARK_MEAN_MAX": (0, 255),
        "IMAGE_BRIGHT_MEAN_MIN": (0, 255),
        "IMAGE_MAX_TILT_DEGREES": (0, 90),
        "IMAGE_MIN_WIDTH": (1, float("inf")),
        "IMAGE_MIN_HEIGHT": (1, float("inf")),
    }
    for setting, (minimum, maximum) in numeric_quality_settings.items():
        try:
            value = float(app.config[setting])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"{setting} must be a valid numeric threshold.") from exc
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise RuntimeError(f"{setting} must be a valid numeric threshold.")
        if setting in {"IMAGE_MIN_WIDTH", "IMAGE_MIN_HEIGHT"} and not value.is_integer():
            raise RuntimeError(f"{setting} must be a positive integer.")
        app.config[setting] = int(value) if setting in {"IMAGE_MIN_WIDTH", "IMAGE_MIN_HEIGHT"} else value
    if app.config["IMAGE_DARK_MEAN_MAX"] >= app.config["IMAGE_BRIGHT_MEAN_MIN"]:
        raise RuntimeError("IMAGE_DARK_MEAN_MAX must be lower than IMAGE_BRIGHT_MEAN_MIN.")
    if not app.config.get("TESTING") and not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "FLASK_SECRET_KEY is required. Copy .env.example to .env and configure it."
        )
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    if not upload_folder.is_absolute():
        upload_folder = Path(app.root_path) / upload_folder
    app.config["UPLOAD_FOLDER"] = str(upload_folder)
    upload_folder.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)
    app.register_blueprint(auth_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(batch_bp)
    app.register_blueprint(inspection_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(web_bp)
    register_cli(app)
    app.after_request(record_request_audit)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "ocr": "implemented"})

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        payload = {"error": error.message}
        if error.details is not None:
            payload["data"] = error.details
        return jsonify(payload), error.status_code

    @app.errorhandler(DatabaseUnavailable)
    def handle_database_unavailable(_error):
        return jsonify({"error": "The database is temporarily unavailable."}), 503

    @app.errorhandler(MySQLConnectorError)
    def handle_mysql_error(error):
        app.logger.error("MySQL operation failed (connector errno=%s).", error.errno)
        if error.errno == 1062:
            return jsonify({"error": "A record with the same unique value already exists."}), 409
        if error.errno in {1451, 1452, 3819, 3823}:
            return jsonify(
                {"error": "The operation conflicts with a database relationship or constraint."}
            ), 409
        return jsonify({"error": "A database operation failed."}), 500

    @app.errorhandler(RequestEntityTooLarge)
    def handle_upload_too_large(_error):
        return jsonify({"error": "Uploaded file exceeds the configured size limit."}), 413

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return jsonify({"error": error.description}), error.code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled request error.")
        return jsonify({"error": "An unexpected server error occurred."}), 500

    if not app.testing:
        logging.basicConfig(level=logging.INFO)
    return app


if __name__ == "__main__":
    application = create_app()
    application.run()
