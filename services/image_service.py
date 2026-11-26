from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from werkzeug.datastructures import FileStorage

from services.api_errors import ApiError


_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff", {"image/jpeg", "image/jpg"}),
    ".jpeg": (b"\xff\xd8\xff", {"image/jpeg", "image/jpg"}),
    ".png": (b"\x89PNG\r\n\x1a\n", {"image/png"}),
}


class StoredImageError(ValueError):
    """Raised when an inspection image path is missing or unsafe."""


def resolve_stored_image_path(image_path: str, upload_folder: str) -> Path:
    if not isinstance(image_path, str) or not image_path.strip():
        raise StoredImageError("The inspection image path is unavailable.")

    normalized_path = image_path.strip().replace("\\", "/")
    relative_path = PurePosixPath(normalized_path)
    windows_path = PureWindowsPath(image_path)
    if (
        relative_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise StoredImageError("The inspection image path is invalid.")

    upload_root = Path(upload_folder).resolve()
    parts = relative_path.parts
    if len(parts) == 1:
        filename = parts[0]
    elif len(parts) == 2 and parts[0] == upload_root.name:
        filename = parts[1]
    else:
        raise StoredImageError("The inspection image path is invalid.")

    if Path(filename).name != filename or Path(filename).suffix.lower() not in _SIGNATURES:
        raise StoredImageError("The inspection image path is invalid.")

    try:
        image_path = (upload_root / filename).resolve(strict=True)
    except OSError as exc:
        raise StoredImageError("The inspection image is unavailable.") from exc
    if image_path.parent != upload_root or not image_path.is_file():
        raise StoredImageError("The inspection image is unavailable.")
    return image_path


def save_package_image(upload: FileStorage, upload_folder: str, project_root: str):
    if upload is None or not upload.filename:
        raise ApiError("An image file is required.")

    extension = Path(upload.filename).suffix.lower()
    if extension not in _SIGNATURES:
        raise ApiError("Only JPG, JPEG, and PNG images are accepted.")

    signature, allowed_mime_types = _SIGNATURES[extension]
    if upload.mimetype and upload.mimetype not in allowed_mime_types:
        raise ApiError("The uploaded image type does not match its file extension.")

    header = upload.stream.read(len(signature))
    upload.stream.seek(0)
    if not header.startswith(signature):
        raise ApiError("The uploaded file does not have a valid image signature.")

    folder = Path(upload_folder)
    if not folder.is_absolute():
        folder = Path(project_root) / folder
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}{extension}"
    destination = folder / filename
    upload.save(destination)
    try:
        relative_path = destination.relative_to(project_root).as_posix()
    except ValueError:
        relative_path = filename
    return destination, relative_path
