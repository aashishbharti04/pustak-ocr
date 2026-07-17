"""Image cleanup ahead of OCR.

Deliberately conservative. Tesseract binarizes internally (Otsu), and handing it a
pre-binarized page usually makes Devanagari *worse*: adaptive thresholding eats the
shirorekha and thins matras until they vanish. So the default path is
grayscale -> light denoise -> deskew, and binarization stays opt-in for genuinely
bad scans (heavy show-through, uneven lighting).
"""

import cv2
import numpy as np

from .config import MAX_DESKEW_DEGREES


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def estimate_skew(gray: np.ndarray) -> float:
    """Skew angle in degrees via projection profile.

    Devanagari is a gift here: the shirorekha makes every text line a strong
    horizontal bar, so row-variance peaks hard at the correct rotation.
    """
    work = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    binary = cv2.threshold(work, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-MAX_DESKEW_DEGREES, MAX_DESKEW_DEGREES + 0.25, 0.25):
        rotated = _rotate(binary, float(angle), border=0)
        profile = rotated.sum(axis=1, dtype=np.float64)
        score = float(((profile[1:] - profile[:-1]) ** 2).sum())
        if score > best_score:
            best_score, best_angle = score, float(angle)
    return best_angle


def _rotate(image: np.ndarray, angle: float, border: int = 255) -> np.ndarray:
    if abs(angle) < 0.01:
        return image
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border,
    )


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    angle = estimate_skew(gray)
    return _rotate(gray, angle), angle


def denoise(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7, searchWindowSize=21)


def binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]


def prepare(image: np.ndarray, *, do_denoise: bool = True, do_binarize: bool = False) -> dict:
    gray = _to_gray(image)
    if do_denoise:
        gray = denoise(gray)
    gray, angle = deskew(gray)
    if do_binarize:
        gray = binarize(gray)
    return {"image": gray, "skew_angle": angle}
