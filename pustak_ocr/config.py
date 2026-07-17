import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PUSTAK_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "pustak.db"
BOOKS_DIR = DATA_DIR / "books"

# 300 DPI is the accuracy floor for Devanagari; matras and the shirorekha start
# dropping out below it. Raising this past 400 mostly costs disk for no gain.
RENDER_DPI = int(os.environ.get("PUSTAK_DPI", "300"))

TESSERACT_CMD = os.environ.get("PUSTAK_TESSERACT_CMD") or None
TESSERACT_LANG = os.environ.get("PUSTAK_TESSERACT_LANG", "hin")
# psm 6 = "assume a single uniform block of text", the right call for novel pages.
TESSERACT_CONFIG = os.environ.get("PUSTAK_TESSERACT_CONFIG", "--psm 6")

# Pages scoring below this get flagged for priority review.
LOW_CONFIDENCE_THRESHOLD = float(os.environ.get("PUSTAK_LOW_CONF", "80"))

MAX_DESKEW_DEGREES = 10.0


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)


def book_dir(book_id: int) -> Path:
    return BOOKS_DIR / str(book_id)


def page_image_dir(book_id: int) -> Path:
    return book_dir(book_id) / "pages"
