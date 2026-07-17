import json
import shutil
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import ai, db, export, ocr, pipeline
from .config import BASE_DIR, LOW_CONFIDENCE_THRESHOLD, book_dir, ensure_dirs

app = FastAPI(title="Pustak-OCR")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
def _startup() -> None:
    ensure_dirs()
    db.init_db()


def content_disposition(title: str, ext: str) -> str:
    """RFC 5987 Content-Disposition.

    HTTP headers are latin-1, and every title this tool sees is Devanagari, so the
    filename has to go out percent-encoded with an ASCII fallback for old clients.
    """
    ascii_name = "".join(c for c in title if c.isascii() and (c.isalnum() or c in " -_")).strip()
    ascii_name = ascii_name or "book"
    utf8_name = quote(f"{title}.{ext}", safe="")
    return f"attachment; filename=\"{ascii_name}.{ext}\"; filename*=UTF-8''{utf8_name}"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    try:
        tesseract_version = ocr.check_available()
        ocr.check_language()
        tesseract_error = None
    except ocr.TesseractMissing as exc:
        tesseract_version, tesseract_error = None, str(exc)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "books": db.list_books(),
            "tesseract_version": tesseract_version,
            "tesseract_error": tesseract_error,
        },
    )


@app.post("/books")
async def upload_book(
    title: str = Form(...),
    author: str = Form(""),
    binarize: bool = Form(False),
    pdf: UploadFile = Form(...),
):
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF.")

    book_id = db.create_book(title.strip(), author.strip() or None, pdf.filename)
    target_dir = book_dir(book_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = target_dir / "source.pdf"

    with pdf_path.open("wb") as fh:
        shutil.copyfileobj(pdf.file, fh)

    pipeline.start(book_id, pdf_path, binarize=binarize)
    return RedirectResponse(f"/books/{book_id}", status_code=303)


@app.get("/books/{book_id}", response_class=HTMLResponse)
def book_detail(request: Request, book_id: int):
    book = db.get_book(book_id)
    if book is None:
        raise HTTPException(404, "No such book.")
    pages = db.list_pages(book_id)
    return templates.TemplateResponse(
        request,
        "book.html",
        {
            "book": book,
            "pages": pages,
            "progress": db.progress(book_id),
            "low_conf": LOW_CONFIDENCE_THRESHOLD,
        },
    )


@app.get("/books/{book_id}/progress", response_class=HTMLResponse)
def book_progress(request: Request, book_id: int):
    book = db.get_book(book_id)
    if book is None:
        raise HTTPException(404, "No such book.")
    return templates.TemplateResponse(
        request,
        "_progress.html",
        {"book": book, "progress": db.progress(book_id)},
    )


@app.get("/books/{book_id}/pages/{page_number}", response_class=HTMLResponse)
def review_page(request: Request, book_id: int, page_number: int):
    book = db.get_book(book_id)
    page = db.get_page(book_id, page_number)
    if book is None or page is None:
        raise HTTPException(404, "No such page.")

    chapters = {c["start_page"]: c["title"] for c in db.list_chapters(book_id)}
    stored = json.loads(page["suggestions"]) if page["suggestions"] else None
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "book": book,
            "page": page,
            "text": page["corrected_text"] or page["raw_ocr_text"] or "",
            "chapter_title": chapters.get(page_number, ""),
            "stored_suggestions": stored,
            "progress": db.progress(book_id),
            "has_prev": page_number > 1,
            "has_next": page_number < book["page_count"],
            "low_conf": LOW_CONFIDENCE_THRESHOLD,
        },
    )


@app.get("/books/{book_id}/pages/{page_number}/image")
def page_image(book_id: int, page_number: int):
    page = db.get_page(book_id, page_number)
    if page is None:
        raise HTTPException(404, "No such page.")
    path = (book_dir(book_id) / page["image_path"]).resolve()
    if not path.is_file() or book_dir(book_id).resolve() not in path.parents:
        raise HTTPException(404, "Image missing.")
    return StreamingResponse(path.open("rb"), media_type="image/png")


@app.post("/pages/{page_id}/save")
def save_page(page_id: int, text: str = Form(""), reviewed: bool = Form(False)):
    db.save_correction(page_id, text, mark_reviewed=reviewed)
    return PlainTextResponse("saved")


@app.post("/books/{book_id}/pages/{page_number}/chapter")
def mark_chapter(book_id: int, page_number: int, title: str = Form("")):
    db.set_chapter(book_id, page_number, title)
    return PlainTextResponse("ok")


@app.post("/pages/{page_id}/suggest", response_class=HTMLResponse)
def suggest_page(request: Request, page_id: int):
    page = db.get_page_by_id(page_id)
    if page is None:
        raise HTTPException(404, "No such page.")

    text = page["corrected_text"] or page["raw_ocr_text"] or ""
    image = book_dir(page["book_id"]) / page["image_path"]
    try:
        result = ai.suggest(str(image), text)
    except ai.AIUnavailable as exc:
        return templates.TemplateResponse(
            request, "_suggestions.html", {"error": str(exc), "result": None}, status_code=200
        )

    db.save_suggestions(page_id, json.dumps(result, ensure_ascii=False))
    return templates.TemplateResponse(request, "_suggestions.html", {"result": result, "error": None})


@app.get("/books/{book_id}/export.docx")
def export_docx(book_id: int):
    book = db.get_book(book_id)
    if book is None:
        raise HTTPException(404, "No such book.")
    return StreamingResponse(
        export.build_docx(book_id),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(book["title"], "docx")},
    )


@app.get("/books/{book_id}/export.epub")
def export_epub(book_id: int):
    book = db.get_book(book_id)
    if book is None:
        raise HTTPException(404, "No such book.")
    return StreamingResponse(
        export.build_epub(book_id),
        media_type="application/epub+zip",
        headers={"Content-Disposition": content_disposition(book["title"], "epub")},
    )


@app.get("/books/{book_id}/export.txt", response_class=PlainTextResponse)
def export_txt(book_id: int):
    if db.get_book(book_id) is None:
        raise HTTPException(404, "No such book.")
    return export.build_text(book_id)


@app.post("/books/{book_id}/retry")
def retry(book_id: int):
    book = db.get_book(book_id)
    if book is None:
        raise HTTPException(404, "No such book.")
    pdf_path = book_dir(book_id) / "source.pdf"
    if not pdf_path.is_file():
        raise HTTPException(400, "Source PDF is gone.")
    pipeline.start(book_id, pdf_path)
    return RedirectResponse(f"/books/{book_id}", status_code=303)
