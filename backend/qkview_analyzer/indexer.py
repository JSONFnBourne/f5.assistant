"""SQLite FTS5 indexer for fast log searching and filtering."""

import sqlite3
from datetime import datetime
from typing import Optional

from .parser import LogEntry, SEVERITY_LEVELS


class LogIndexer:
    """Index parsed log entries in SQLite with FTS5 for fast searching."""

    def __init__(self, db_path: str = ":memory:"):
        """Initialize the indexer.

        Args:
            db_path: Path to SQLite database, or ":memory:" for in-memory.
        """
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=MEMORY;
            PRAGMA cache_size=-200000;
        """)
        self._create_tables()
        self._entry_count = 0

    def _create_tables(self):
        """Create the logs table and FTS5 index."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                timestamp_epoch REAL NOT NULL,
                hostname TEXT,
                severity TEXT NOT NULL,
                severity_num INTEGER NOT NULL,
                process TEXT,
                pid INTEGER,
                msg_code TEXT,
                f5_severity INTEGER,
                message TEXT NOT NULL,
                source_file TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                raw_line TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS logs_fts USING fts5(
                message,
                content='logs',
                content_rowid='id'
            );

            CREATE INDEX IF NOT EXISTS idx_timestamp ON logs(timestamp_epoch);
            CREATE INDEX IF NOT EXISTS idx_severity ON logs(severity_num);
            CREATE INDEX IF NOT EXISTS idx_process ON logs(process);
            CREATE INDEX IF NOT EXISTS idx_msg_code ON logs(msg_code);
            CREATE INDEX IF NOT EXISTS idx_source ON logs(source_file);
        """)

    def bulk_insert(self, entries: list[LogEntry], progress_callback=None):
        """Insert a batch of log entries.

        Args:
            entries: List of LogEntry objects
            progress_callback: Optional callable(status_message)
        """
        total = len(entries)
        batch_size = 5000
        cursor = self.conn.cursor()

        for i in range(0, total, batch_size):
            batch = entries[i:i + batch_size]

            if progress_callback and i % batch_size == 0:
                progress_callback(f"Indexing entries {i}/{total}...")

            # Insert into main table
            cursor.executemany(
                """INSERT INTO logs
                   (timestamp, timestamp_epoch, hostname, severity, severity_num,
                    process, pid, msg_code, f5_severity, message,
                    source_file, line_number, raw_line)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        e.timestamp.isoformat(),
                        e.timestamp.timestamp(),
                        e.hostname,
                        e.severity,
                        e.severity_num,
                        e.process,
                        e.pid,
                        e.msg_code,
                        e.f5_severity,
                        e.message,
                        e.source_file,
                        e.line_number,
                        e.raw_line,
                    )
                    for e in batch
                ],
            )

        # Rebuild FTS index
        self.conn.execute("INSERT INTO logs_fts(logs_fts) VALUES ('rebuild')")
        self.conn.commit()
        self._entry_count = total

        if progress_callback:
            progress_callback(f"Indexed {total} entries")

    @property
    def entry_count(self) -> int:
        """Total number of indexed entries."""
        return self._entry_count

    def query(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        min_severity: Optional[str] = None,
        process: Optional[str] = None,
        msg_code: Optional[str] = None,
        search: Optional[str] = None,
        source_file: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict]:
        """Query log entries with composable filters.

        Args:
            start: Start datetime (inclusive)
            end: End datetime (inclusive)
            min_severity: Minimum syslog severity name (e.g., "warning")
            process: Process name filter (exact match)
            msg_code: F5 message code filter (prefix match)
            search: Full-text search term
            source_file: Source file filter (prefix match)
            limit: Max results to return
            offset: Result offset for pagination

        Returns:
            List of dicts with log entry data
        """
        conditions = []
        params = []

        if start:
            conditions.append("timestamp_epoch >= ?")
            params.append(start.timestamp())
        if end:
            conditions.append("timestamp_epoch <= ?")
            params.append(end.timestamp())
        if min_severity:
            sev_num = SEVERITY_LEVELS.get(min_severity.lower(), 6)
            conditions.append("severity_num <= ?")  # lower number = higher severity
            params.append(sev_num)
        if process:
            conditions.append("process = ?")
            params.append(process)
        if msg_code:
            conditions.append("msg_code LIKE ?")
            params.append(f"{msg_code}%")
        if source_file:
            conditions.append("source_file LIKE ?")
            params.append(f"{source_file}%")

        # FTS search uses a JOIN
        if search:
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            sql = f"""
                SELECT logs.*
                FROM logs
                JOIN logs_fts ON logs.id = logs_fts.rowid
                WHERE logs_fts MATCH ? AND {where_clause}
                ORDER BY logs.timestamp_epoch ASC
                LIMIT ? OFFSET ?
            """
            params = [search] + params + [limit, offset]
        else:
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            sql = f"""
                SELECT * FROM logs
                WHERE {where_clause}
                ORDER BY timestamp_epoch ASC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])

        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def query_count(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        min_severity: Optional[str] = None,
        process: Optional[str] = None,
    ) -> int:
        """Get count of matching entries without fetching them."""
        conditions = []
        params = []

        if start:
            conditions.append("timestamp_epoch >= ?")
            params.append(start.timestamp())
        if end:
            conditions.append("timestamp_epoch <= ?")
            params.append(end.timestamp())
        if min_severity:
            sev_num = SEVERITY_LEVELS.get(min_severity.lower(), 6)
            conditions.append("severity_num <= ?")
            params.append(sev_num)
        if process:
            conditions.append("process = ?")
            params.append(process)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT COUNT(*) FROM logs WHERE {where_clause}"

        cursor = self.conn.execute(sql, params)
        return cursor.fetchone()[0]

    def get_severity_summary(self) -> dict[str, int]:
        """Get count of entries per severity level."""
        cursor = self.conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM logs GROUP BY severity ORDER BY MIN(severity_num)"
        )
        return {row["severity"]: row["cnt"] for row in cursor.fetchall()}

    def get_process_summary(self) -> dict[str, int]:
        """Get count of entries per process, sorted by count desc."""
        cursor = self.conn.execute(
            "SELECT process, COUNT(*) as cnt FROM logs GROUP BY process ORDER BY cnt DESC"
        )
        return {row["process"]: row["cnt"] for row in cursor.fetchall()}

    def get_source_summary(self) -> dict[str, int]:
        """Get count of entries per source file."""
        cursor = self.conn.execute(
            "SELECT source_file, COUNT(*) as cnt FROM logs GROUP BY source_file ORDER BY cnt DESC"
        )
        return {row["source_file"]: row["cnt"] for row in cursor.fetchall()}

    def get_time_range(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """Get the earliest and latest timestamps in the index."""
        cursor = self.conn.execute(
            "SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts FROM logs"
        )
        row = cursor.fetchone()
        if row and row["min_ts"] and row["max_ts"]:
            return (
                datetime.fromisoformat(row["min_ts"]),
                datetime.fromisoformat(row["max_ts"]),
            )
        return (None, None)

    def get_top_msg_codes(self, limit: int = 20) -> list[tuple[str, int, str]]:
        """Get most frequent F5 message codes with a sample message.

        Returns list of (msg_code, count, sample_message).
        """
        cursor = self.conn.execute(
            """SELECT msg_code, COUNT(*) as cnt, message
               FROM logs
               WHERE msg_code IS NOT NULL
               GROUP BY msg_code
               ORDER BY cnt DESC
               LIMIT ?""",
            (limit,),
        )
        return [(row["msg_code"], row["cnt"], row["message"]) for row in cursor.fetchall()]

    def close(self):
        """Close the database connection."""
        self.conn.close()
