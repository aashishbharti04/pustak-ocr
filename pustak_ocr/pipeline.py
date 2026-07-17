"""Background ingest + OCR job.

Single worker thread per book, processing pages in order and committing each one as
it lands. Crash-safe by construction: pages already marked 'ocr_done' are skipped on
resume, so a 282-page run that dies at page 200 costs you 82 pages, not 282.
"""

import threading
import traceback
from pathlib import Path

import cv2

from . import db, ingest, ocr, preprocess
from .config import page_image_dir

_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(book_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(book_id, threading.Lock())


def run_book(book_id: int, pdf_path: Path, *, denoise: bool = True, binarize: bool = False) -> None:
    lock = _lock_for(book_id)
    if not lock.acquire(blocking=False):
        return  # already running
    try:
        _ingest(book_id, pdf_path)
        _ocr_pending(book_id, denoise=denoise, binarize=binarize)
        db.set_book_status(book_id, "ready")
    except ocr.TesseractMissing as exc:
        db.set_book_status(book_id, "failed", str(exc))
    except Exception:
        db.set_book_status(book_id, "failed", traceback.format_exc(limit=5))
    finally:
        lock.release()


def _ingest(book_id: int, pdf_path: Path) -> None:
    existing = {p["page_number"] for p in db.list_pages(book_id)}
    total = ingest.page_count(pdf_path)
    db.set_page_count(book_id, total)
    if len(existing) >= total:
        return

    db.set_book_status(book_id, "ingesting")
    for number, image_path in ingest.render_pages(pdf_path, book_id):
        db.add_page(book_id, number, str(image_path.relative_to(page_image_dir(book_id).parent)))


def _ocr_pending(book_id: int, *, denoise: bool, binarize: bool) -> None:
    ocr.check_available()
    ocr.check_language()

    db.set_book_status(book_id, "ocr")
    for page in db.pending_pages(book_id):
        path = page_image_dir(book_id).parent / page["image_path"]
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            db.save_ocr(book_id, page["page_number"], "", 0.0)
            continue
        prepared = preprocess.prepare(image, do_denoise=denoise, do_binarize=binarize)
        result = ocr.run(prepared["image"])
        db.save_ocr(book_id, page["page_number"], result["text"], result["confidence"])


def start(book_id: int, pdf_path: Path, *, denoise: bool = True, binarize: bool = False) -> None:
    thread = threading.Thread(
        target=run_book,
        args=(book_id, pdf_path),
        kwargs={"denoise": denoise, "binarize": binarize},
        daemon=True,
        name=f"pustak-ocr-{book_id}",
    )
    thread.start()
