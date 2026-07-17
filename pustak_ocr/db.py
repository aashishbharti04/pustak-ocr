import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    author      TEXT,
    source_file TEXT NOT NULL,
    page_count  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'ingesting',
    error       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id             INTEGER PRIMARY KEY,
    book_id        INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_number    INTEGER NOT NULL,
    image_path     TEXT NOT NULL,
    raw_ocr_text   TEXT,
    corrected_text TEXT,
    suggestions    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    ocr_confidence REAL,
    updated_at     TEXT,
    UNIQUE (book_id, page_number)
);

CREATE TABLE IF NOT EXISTS chapters (
    id         INTEGER PRIMARY KEY,
    book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    start_page INTEGER NOT NULL,
    UNIQUE (book_id, start_page)
);

CREATE INDEX IF NOT EXISTS idx_pages_book ON pages (book_id, page_number);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
        if "suggestions" not in columns:  # DBs created before the AI layer existed
            conn.execute("ALTER TABLE pages ADD COLUMN suggestions TEXT")


def save_suggestions(page_id: int, payload: str | None) -> None:
    with session() as conn:
        conn.execute("UPDATE pages SET suggestions = ? WHERE id = ?", (payload, page_id))


def get_page_by_id(page_id: int) -> sqlite3.Row | None:
    with session() as conn:
        return conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()


def create_book(title: str, author: str | None, source_file: str) -> int:
    with session() as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, source_file, created_at) VALUES (?, ?, ?, ?)",
            (title, author, source_file, now()),
        )
        return int(cur.lastrowid)


def set_book_status(book_id: int, status: str, error: str | None = None) -> None:
    with session() as conn:
        conn.execute(
            "UPDATE books SET status = ?, error = ? WHERE id = ?", (status, error, book_id)
        )


def set_page_count(book_id: int, count: int) -> None:
    with session() as conn:
        conn.execute("UPDATE books SET page_count = ? WHERE id = ?", (count, book_id))


def add_page(book_id: int, page_number: int, image_path: str) -> None:
    with session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pages (book_id, page_number, image_path) VALUES (?, ?, ?)",
            (book_id, page_number, image_path),
        )


def save_ocr(book_id: int, page_number: int, text: str, confidence: float) -> None:
    with session() as conn:
        conn.execute(
            """UPDATE pages SET raw_ocr_text = ?, ocr_confidence = ?, status = 'ocr_done',
                                updated_at = ?
               WHERE book_id = ? AND page_number = ?""",
            (text, confidence, now(), book_id, page_number),
        )


def save_correction(page_id: int, text: str, mark_reviewed: bool) -> None:
    status = "reviewed" if mark_reviewed else "ocr_done"
    with session() as conn:
        conn.execute(
            "UPDATE pages SET corrected_text = ?, status = ?, updated_at = ? WHERE id = ?",
            (text, status, now(), page_id),
        )


def get_book(book_id: int) -> sqlite3.Row | None:
    with session() as conn:
        return conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()


def list_books() -> list[sqlite3.Row]:
    with session() as conn:
        return conn.execute(
            """SELECT b.*,
                      (SELECT COUNT(*) FROM pages p
                        WHERE p.book_id = b.id AND p.status = 'reviewed') AS reviewed_count
                 FROM books b ORDER BY b.created_at DESC"""
        ).fetchall()


def get_page(book_id: int, page_number: int) -> sqlite3.Row | None:
    with session() as conn:
        return conn.execute(
            "SELECT * FROM pages WHERE book_id = ? AND page_number = ?", (book_id, page_number)
        ).fetchone()


def list_pages(book_id: int) -> list[sqlite3.Row]:
    with session() as conn:
        return conn.execute(
            "SELECT * FROM pages WHERE book_id = ? ORDER BY page_number", (book_id,)
        ).fetchall()


def pending_pages(book_id: int) -> list[sqlite3.Row]:
    with session() as conn:
        return conn.execute(
            "SELECT * FROM pages WHERE book_id = ? AND status = 'pending' ORDER BY page_number",
            (book_id,),
        ).fetchall()


def progress(book_id: int) -> dict:
    with session() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(status = 'reviewed') AS reviewed,
                      SUM(status != 'pending') AS ocred
                 FROM pages WHERE book_id = ?""",
            (book_id,),
        ).fetchone()
    return {
        "total": row["total"] or 0,
        "reviewed": row["reviewed"] or 0,
        "ocred": row["ocred"] or 0,
    }


def list_chapters(book_id: int) -> list[sqlite3.Row]:
    with session() as conn:
        return conn.execute(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY start_page", (book_id,)
        ).fetchall()


def set_chapter(book_id: int, start_page: int, title: str) -> None:
    with session() as conn:
        if title.strip():
            conn.execute(
                "INSERT OR REPLACE INTO chapters (book_id, start_page, title) VALUES (?, ?, ?)",
                (book_id, start_page, title.strip()),
            )
        else:
            conn.execute(
                "DELETE FROM chapters WHERE book_id = ? AND start_page = ?", (book_id, start_page)
            )
