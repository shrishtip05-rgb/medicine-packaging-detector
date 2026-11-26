import os
import shutil
from pathlib import Path


def _resolve_tesseract_cmd(configured=None):
    if configured is None:
        configured = os.getenv("TESSERACT_CMD")
    if configured and configured.strip():
        configured = configured.strip()
        configured_path = Path(configured)
        if configured_path.is_file():
            return str(configured_path)
        configured_executable = shutil.which(configured)
        if configured_executable:
            return configured_executable

    windows_executable = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if windows_executable.is_file():
        return str(windows_executable)
    return shutil.which("tesseract")


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
    DEBUG = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"}
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(8 * 1024 * 1024)))
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
    TESSERACT_CMD = _resolve_tesseract_cmd()
    OCR_MAX_PIXELS = int(os.getenv("OCR_MAX_PIXELS", "25000000"))
    OCR_MIN_CONFIDENCE = float(os.getenv("OCR_MIN_CONFIDENCE", "80"))
    IMAGE_BLUR_VARIANCE_MIN = float(os.getenv("IMAGE_BLUR_VARIANCE_MIN", "80"))
    IMAGE_DARK_MEAN_MAX = float(os.getenv("IMAGE_DARK_MEAN_MAX", "45"))
    IMAGE_BRIGHT_MEAN_MIN = float(os.getenv("IMAGE_BRIGHT_MEAN_MIN", "220"))
    IMAGE_MIN_WIDTH = int(os.getenv("IMAGE_MIN_WIDTH", "640"))
    IMAGE_MIN_HEIGHT = int(os.getenv("IMAGE_MIN_HEIGHT", "480"))
    IMAGE_MAX_TILT_DEGREES = float(os.getenv("IMAGE_MAX_TILT_DEGREES", "12"))
    AUDIT_ENABLED_IN_TESTS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0").lower() in {
        "1",
        "true",
        "yes",
    }

    MYSQL_CONFIG = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "database": os.getenv("DB_NAME", "medicine_packaging_db"),
        "user": os.getenv("DB_USER", ""),
        "password": os.getenv("DB_PASSWORD", ""),
        "connection_timeout": 5,
        "autocommit": True,
    }
