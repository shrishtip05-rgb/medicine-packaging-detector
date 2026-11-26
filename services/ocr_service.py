from pathlib import Path

import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from pytesseract.pytesseract import TesseractError, TesseractNotFoundError

from services.field_extraction import extract_medicine_fields


class OcrEngineUnavailable(Exception):
    """Raised when the Tesseract executable cannot be found or started."""


class OcrImageError(Exception):
    """Raised when an uploaded image cannot be decoded for OCR."""


class OcrProcessingError(Exception):
    """Raised when the OCR engine fails while processing a valid image."""


def _preprocess_image(image_path, max_pixels):
    try:
        encoded = np.frombuffer(Path(image_path).read_bytes(), dtype=np.uint8)
    except OSError as exc:
        raise OcrImageError("The inspection image could not be read.") from exc

    try:
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise OcrImageError("The inspection image is unreadable or corrupt.") from exc
    if image is None or image.ndim != 3:
        raise OcrImageError("The inspection image is unreadable or corrupt.")

    height, width = image.shape[:2]
    if height < 3 or width < 3:
        raise OcrImageError("The inspection image is too small to process.")
    if height * width > max_pixels:
        raise OcrImageError("The inspection image exceeds the OCR pixel limit.")

    try:
        grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.medianBlur(grayscale, 3)
        smallest_dimension = min(height, width)
        if smallest_dimension >= 3:
            block_size = min(31, smallest_dimension)
            if block_size % 2 == 0:
                block_size -= 1
        else:
            block_size = 1

        if block_size >= 3:
            return cv2.adaptiveThreshold(
                denoised,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                block_size,
                11,
            )
        return cv2.threshold(
            denoised, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
        )[1]
    except cv2.error as exc:
        raise OcrImageError("The inspection image could not be preprocessed.") from exc


def _line_confidences(data):
    if not isinstance(data, dict) or not all(
        key in data for key in ("text", "conf", "block_num", "par_num", "line_num")
    ):
        return {}

    groups = {}
    for index, text in enumerate(data["text"]):
        text = str(text).strip()
        if not text:
            continue
        key = (
            data["block_num"][index],
            data["par_num"][index],
            data["line_num"][index],
        )
        group = groups.setdefault(key, {"words": [], "confidences": []})
        group["words"].append(text)
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            group["confidences"].append(confidence)

    confidences = {}
    for group in groups.values():
        if group["confidences"]:
            line = " ".join(group["words"]).casefold()
            confidences[line] = sum(group["confidences"]) / len(group["confidences"])
    return confidences


def run_ocr(image_path, tesseract_cmd=None, max_pixels=25_000_000):
    """Preprocess one safely-resolved image and return OCR text and field results."""
    if not isinstance(max_pixels, int) or max_pixels <= 0:
        raise ValueError("max_pixels must be a positive integer.")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    processed_image = _preprocess_image(image_path, max_pixels)
    try:
        pytesseract.get_tesseract_version()
    except TesseractNotFoundError as exc:
        raise OcrEngineUnavailable from exc
    except TesseractError as exc:
        raise OcrProcessingError from exc

    try:
        raw_text = pytesseract.image_to_string(processed_image, config="--psm 6")
        data = pytesseract.image_to_data(
            processed_image, output_type=Output.DICT, config="--psm 6"
        )
    except TesseractNotFoundError as exc:
        raise OcrEngineUnavailable from exc
    except TesseractError as exc:
        raise OcrProcessingError from exc

    if not isinstance(raw_text, str):
        raise OcrProcessingError("Tesseract returned an invalid text result.")

    return {
        "raw_text": raw_text,
        "fields": extract_medicine_fields(raw_text, _line_confidences(data)),
        "unsupported_fields": ["barcode"],
    }
