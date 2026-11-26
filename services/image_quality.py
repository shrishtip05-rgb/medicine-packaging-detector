from pathlib import Path

import cv2
import numpy as np


class ImageQualityError(ValueError):
    """Raised when an image cannot be decoded for quality assessment."""


def assess_image_quality(
    image_path,
    blur_variance_min=80.0,
    dark_mean_max=45.0,
    bright_mean_min=220.0,
    min_width=640,
    min_height=480,
    max_tilt_degrees=12.0,
):
    try:
        encoded = np.frombuffer(Path(image_path).read_bytes(), dtype=np.uint8)
    except OSError as exc:
        raise ImageQualityError("The package image could not be read.") from exc

    try:
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3:
            raise ImageQualityError("The package image is unreadable or corrupt.")
        height, width = image.shape[:2]
        grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur_variance = float(cv2.Laplacian(grayscale, cv2.CV_64F).var())
        mean_luminance = float(np.mean(grayscale))
        tilt = _estimate_tilt(grayscale)
    except cv2.error as exc:
        raise ImageQualityError(
            "The package image could not be assessed by OpenCV."
        ) from exc

    issues = []
    recommendations = []
    penalties = 0

    if blur_variance < blur_variance_min:
        issues.append(
            {
                "code": "BLURRY",
                "severity": "block",
                "measurement": round(blur_variance, 2),
            }
        )
        recommendations.append(
            "Retake the photo with the package steady and the printed text in focus."
        )
        penalties += 40
    if mean_luminance < dark_mean_max:
        issues.append(
            {
                "code": "TOO_DARK",
                "severity": "block",
                "measurement": round(mean_luminance, 2),
            }
        )
        recommendations.append("Use brighter, even lighting without casting shadows.")
        penalties += 30
    elif mean_luminance > bright_mean_min:
        issues.append(
            {
                "code": "OVEREXPOSED",
                "severity": "block",
                "measurement": round(mean_luminance, 2),
            }
        )
        recommendations.append("Reduce glare and retake the photo with even lighting.")
        penalties += 30

    if width < min_width or height < min_height:
        issues.append(
            {
                "code": "LOW_RESOLUTION",
                "severity": "block",
                "measurement": {"width": width, "height": height},
            }
        )
        recommendations.append(
            "Use a higher-resolution image and keep all package text large enough to read."
        )
        penalties += 25

    if tilt is not None and abs(tilt) > max_tilt_degrees:
        issues.append(
            {
                "code": "EXCESSIVE_TILT",
                "severity": "warning",
                "measurement": round(tilt, 2),
            }
        )
        recommendations.append(
            "Align the camera more parallel to the package before taking the photo."
        )
        penalties += 10

    recommendations.append(
        "Check that the complete package label and its edges are visible in the frame."
    )
    blocked = any(issue["severity"] == "block" for issue in issues)
    if not blocked:
        recommendations.insert(
            0,
            "Image quality is suitable to attempt OCR; review the extracted values manually.",
        )

    return {
        "quality_score": max(0, min(100, 100 - penalties)),
        "status": "UNSUITABLE" if blocked else ("WARNING" if issues else "SUITABLE"),
        "blocked": blocked,
        "dimensions": {"width": width, "height": height},
        "blur_variance": round(blur_variance, 2),
        "mean_luminance": round(mean_luminance, 2),
        "estimated_tilt_degrees": round(tilt, 2) if tilt is not None else None,
        "issues": issues,
        "recommendations": recommendations,
        "limitations": (
            "These heuristic checks do not guarantee OCR accuracy and cannot reliably "
            "determine whether the complete package is framed."
        ),
    }


def _estimate_tilt(grayscale):
    edges = cv2.Canny(grayscale, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=60,
        minLineLength=max(40, min(grayscale.shape) // 5),
        maxLineGap=12,
    )
    if lines is None:
        return None

    horizontal_angles = []
    for line in lines[:, 0]:
        x1, y1, x2, y2 = (int(value) for value in line)
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if -45 <= angle <= 45:
            horizontal_angles.append(angle)
    if len(horizontal_angles) < 3:
        return None
    return float(np.median(horizontal_angles))
