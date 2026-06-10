"""
utils/cache.py — 90-day document cache staleness logic.

Shared by curate_f5.py and curate_rfc.py.
Checks the knowledge.db 'documents' table to determine if a document
needs to be re-fetched based on its last_fetched timestamp.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger


def is_stale(db_path: str | Path, doc_id: str, ttl_days: int = 90) -> bool:
    """
    Return True if doc_id is missing from the DB or older than ttl_days.

    Args:
        db_path:  Path to knowledge.db
        doc_id:   Unique document identifier (URL for F5, 'rfcNNNN' for RFC)
        ttl_days: Cache TTL in days (default: 90)

    Returns:
        True  → document needs to be fetched / refreshed
        False → document is fresh, skip
    """
    db_path = Path(db_path)
    if not db_path.exists():
        logger.debug(f"DB not found at {db_path} — treating {doc_id} as stale")
        return True

    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_fetched FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()

    if row is None:
        logger.debug(f"[CACHE MISS] {doc_id} — not in DB")
        return True

    last_fetched = datetime.fromisoformat(row[0])
    # Ensure timezone-aware comparison
    if last_fetched.tzinfo is None:
        last_fetched = last_fetched.replace(tzinfo=timezone.utc)

    if last_fetched < cutoff:
        age_days = (datetime.now(timezone.utc) - last_fetched).days
        logger.debug(f"[CACHE STALE] {doc_id} — {age_days}d old (TTL={ttl_days}d)")
        return True

    age_days = (datetime.now(timezone.utc) - last_fetched).days
    logger.debug(f"[CACHE HIT]  {doc_id} — {age_days}d old, skipping")
    return False


def update_last_fetched(db_path: str | Path, doc_id: str) -> None:
    """Update last_fetched to now for an existing document record."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(Path(db_path)) as conn:
        conn.execute(
            "UPDATE documents SET last_fetched = ? WHERE doc_id = ?",
            (now, doc_id),
        )
        conn.commit()
