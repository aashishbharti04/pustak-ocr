"""PDF -> page images."""

from pathlib import Path

import fitz

from .config import RENDER_DPI, page_image_dir


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def render_pages(pdf_path: Path, book_id: int, dpi: int = RENDER_DPI):
    """Render each PDF page to PNG. Yields (page_number, path) as it goes.

    Streams rather than returning a list so ingest progress is visible on a
    300-page book instead of looking hung for ten minutes.
    """
    out_dir = page_image_dir(book_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
            target = out_dir / f"{index + 1:04d}.png"
            pixmap.save(target)
            yield index + 1, target
