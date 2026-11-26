from flask import Blueprint, current_app, redirect, render_template, session, url_for

from services.auth import login_required, roles_required


web_bp = Blueprint("web", __name__)


@web_bp.get("/")
def root():
    return redirect(url_for("web.dashboard"))


@web_bp.get("/login")
def login_page():
    return render_template(
        "login.html",
        check_session=bool(session.get("user_id")),
    )


@web_bp.get("/dashboard")
@web_bp.get("/medicines")
@web_bp.get("/batches")
@web_bp.get("/inspections/new")
@web_bp.get("/inspections")
@web_bp.get("/inspections/<int:inspection_id>")
@web_bp.get("/batches/<int:batch_id>")
def dashboard(inspection_id=None, batch_id=None):
    return _render_app()


@web_bp.get("/users")
@login_required
@roles_required("admin")
def user_management():
    return _render_app()


@web_bp.get("/audit")
@login_required
@roles_required("admin")
def audit_page():
    return _render_app()


def _render_app():
    return render_template(
        "app.html",
        max_upload_bytes=current_app.config["MAX_CONTENT_LENGTH"],
        ocr_min_confidence=current_app.config["OCR_MIN_CONFIDENCE"],
    )
