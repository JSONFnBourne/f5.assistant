"""
utils/db.py — SQLite knowledge base helpers.

Creates and manages the knowledge.db schema.
Provides insert/update helpers for both F5 and RFC document records.
"""

import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,           -- 'f5' or 'rfc'
    doc_id        TEXT NOT NULL UNIQUE,    -- URL (F5) | 'rfcNNNN' (RFC)
    title         TEXT,
    url           TEXT,
    section       TEXT,                   -- F5 section | RFC status
    keywords      TEXT,                   -- JSON array (as text)
    content_hash  TEXT,                   -- SHA-256 of raw content
    content       TEXT,                   -- Full text or HTML content
    local_path    TEXT,                   -- Relative path to stored file
    last_fetched  TEXT NOT NULL,          -- ISO-8601 UTC timestamp
    created_at    TEXT NOT NULL           -- ISO-8601 UTC timestamp
);

CREATE INDEX IF NOT EXISTS idx_source       ON documents(source);
CREATE INDEX IF NOT EXISTS idx_last_fetched ON documents(last_fetched);
CREATE INDEX IF NOT EXISTS idx_doc_id       ON documents(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts
    USING fts5(title, keywords, content, content=documents, content_rowid=id);
"""


def init_db(db_path: str | Path) -> None:
    """Initialise the DB schema. Safe to call multiple times (IF NOT EXISTS)."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    logger.info(f"Database initialised at {db_path}")


def sha256(content: str | bytes) -> str:
    """Return SHA-256 hex digest of content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def upsert_document(
    db_path: str | Path,
    *,
    source: str,
    doc_id: str,
    title: str | None = None,
    url: str | None = None,
    section: str | None = None,
    keywords: str | None = None,   # JSON string
    content: str | bytes | None = None,
    local_path: str | None = None,
) -> None:
    """
    Insert or update a document record in knowledge.db.

    On conflict (same doc_id), updates all fields and refreshes last_fetched.
    """
    now = datetime.now(timezone.utc).isoformat()
    content_hash = sha256(content) if content is not None else None

    with sqlite3.connect(Path(db_path)) as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()

        fts_content = content if isinstance(content, str) else None

        if existing:
            row_id = existing[0]
            # FTS5 external-content tables can't be updated in place: the old
            # tokens must be removed via the special 'delete' command, which
            # requires the OLD column values for this rowid.
            old = conn.execute(
                "SELECT title, keywords, content FROM documents WHERE id = ?", (row_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO docs_fts(docs_fts, rowid, title, keywords, content)
                   VALUES ('delete', ?, ?, ?, ?)""",
                (row_id, old[0], old[1], old[2]),
            )
            conn.execute(
                """UPDATE documents
                   SET source=?, title=?, url=?, section=?, keywords=?,
                       content_hash=?, content=?, local_path=?, last_fetched=?
                   WHERE doc_id=?""",
                (source, title, url, section, keywords,
                 content_hash, fts_content,
                 local_path, now, doc_id),
            )
            conn.execute(
                """INSERT INTO docs_fts(rowid, title, keywords, content)
                   VALUES (?, ?, ?, ?)""",
                (row_id, title, keywords, fts_content),
            )
            logger.debug(f"[DB UPDATE] {doc_id}")
        else:
            conn.execute(
                """INSERT INTO documents
                   (source, doc_id, title, url, section, keywords,
                    content_hash, content, local_path, last_fetched, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (source, doc_id, title, url, section, keywords,
                 content_hash, fts_content,
                 local_path, now, now),
            )
            conn.execute(
                """INSERT INTO docs_fts(rowid, title, keywords, content)
                   VALUES (last_insert_rowid(), ?, ?, ?)""",
                (title, keywords, fts_content),
            )
            logger.debug(f"[DB INSERT] {doc_id}")

        conn.commit()
