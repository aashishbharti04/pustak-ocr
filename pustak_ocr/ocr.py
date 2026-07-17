"""Tesseract wrapper with per-page confidence."""

import shutil

import cv2
import numpy as np
import pytesseract

from .config import TESSERACT_CMD, TESSERACT_CONFIG, TESSERACT_LANG


class TesseractMissing(RuntimeError):
    pass


def _configure() -> None:
    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def check_available() -> str:
    """Return the tesseract version, or raise with an actionable message."""
    _configure()
    if not TESSERACT_CMD and shutil.which("tesseract") is None:
        raise TesseractMissing(
            "tesseract not found on PATH. Install it, then either add it to PATH or set "
            "PUSTAK_TESSERACT_CMD to the full path of tesseract.exe."
        )
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception as exc:
        raise TesseractMissing(f"tesseract found but not runnable: {exc}") from exc


def check_language(lang: str = TESSERACT_LANG) -> None:
    _configure()
    available = set(pytesseract.get_languages(config=""))
    missing = [code for code in lang.split("+") if code not in available]
    if missing:
        raise TesseractMissing(
            f"tesseract language data missing: {', '.join(missing)}. "
            f"Installed: {', '.join(sorted(available))}. "
            "Add the .traineddata file to your tessdata directory."
        )


def run(image: np.ndarray, lang: str = TESSERACT_LANG, config: str = TESSERACT_CONFIG) -> dict:
    """OCR a page. Returns text plus a 0-100 mean word confidence."""
    _configure()
    if image.ndim == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    data = pytesseract.image_to_data(
        rgb, lang=lang, config=config, output_type=pytesseract.Output.DICT
    )

    confidences = [
        float(c)
        for c, word in zip(data["conf"], data["text"])
        if word.strip() and float(c) >= 0
    ]
    mean_conf = float(np.mean(confidences)) if confidences else 0.0
    text = pytesseract.image_to_string(rgb, lang=lang, config=config)
    return {"text": text.strip(), "confidence": round(mean_conf, 2), "word_count": len(confidences)}
