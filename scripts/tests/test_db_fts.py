"""Tests for scripts/utils/db.py — FTS5 external-content sync in upsert_document.

Regression test for the document-refresh corruption bug: the UPDATE branch used
``INSERT OR REPLACE INTO docs_fts(rowid, ...) VALUES (last_insert_rowid(), ...)``,
but no INSERT had happened on the connection, so last_insert_rowid() was 0 —
new content was indexed under orphan rowid 0 and stale tokens were never deleted.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db import init_db, upsert_document  # noqa: E402


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "knowledge.db"
    init_db(path)
    return path


def _fts_rowids(db_path: Path, term: str) -> list[int]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?", (term,)
        ).fetchall()
    return [r[0] for r in rows]


def test_insert_then_update_keeps_fts_in_sync(db_path: Path) -> None:
    upsert_document(
        db_path,
        source="f5",
        doc_id="K12345",
        title="Original snatpool article",
        keywords='["oldkeyword"]',
        content="oldtoken appears only in the first revision",
    )
    with sqlite3.connect(db_path) as conn:
        doc_rowid = conn.execute(
            "SELECT id FROM documents WHERE doc_id = 'K12345'"
        ).fetchone()[0]

    assert _fts_rowids(db_path, "oldtoken") == [doc_rowid]

    # Refresh the same doc_id with new content (separate connection/call,
    # exactly how curate scripts re-fetch a document).
    upsert_document(
        db_path,
        source="f5",
        doc_id="K12345",
        title="Updated snatpool article",
        keywords='["newkeyword"]',
        content="newtoken appears only in the second revision",
    )

    # Still exactly one document row, same rowid
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM documents WHERE doc_id = 'K12345'"
        ).fetchall()
    assert [r[0] for r in rows] == [doc_rowid]

    # NEW text is findable under the real rowid…
    assert _fts_rowids(db_path, "newtoken") == [doc_rowid]
    assert _fts_rowids(db_path, "newkeyword") == [doc_rowid]
    # …and the OLD text is gone (no stale tokens, no orphan rowid 0)
    assert _fts_rowids(db_path, "oldtoken") == []
    assert _fts_rowids(db_path, "oldkeyword") == []


def test_update_passes_fts_integrity_check(db_path: Path) -> None:
    upsert_document(db_path, source="f5", doc_id="K1", title="t1", content="alpha body")
    upsert_document(db_path, source="f5", doc_id="K1", title="t2", content="beta body")
    # external-content integrity check raises SQLITE_CORRUPT_VTAB on drift
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO docs_fts(docs_fts) VALUES ('integrity-check')")
