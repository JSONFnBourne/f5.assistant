"""FastAPI endpoint tests against a tiny synthetic TMOS qkview.

Unlike the extractor suites (which need real multi-hundred-MB archives from
``F5/data/qkview/``), these tests fabricate a minimal-but-valid TMOS qkview
in memory: a gzip tar with ``config/bigip.conf``, ``config/bigip_base.conf``
(hostname), ``VERSION.LTM``, and a ``var/log/ltm`` containing syslog lines
that trip known rules (01010028 pool_no_members; 01070638/01070727 flap pair).

The analyses DB is monkeypatched to a tmp_path-scoped file per test.
"""

from __future__ import annotations

import io
import json
import pathlib
import sqlite3
import tarfile

import main
import pytest
from fastapi.testclient import TestClient

_BIGIP_CONF = """\
ltm virtual /Common/test_vs {
    destination /Common/192.0.2.10:80
    ip-protocol tcp
    pool /Common/test_pool
}
ltm pool /Common/test_pool {
    members {
        /Common/192.0.2.20:80 {
            address 192.0.2.20
        }
    }
}
"""

_BASE_CONF = """\
sys global-settings {
    hostname test-bigip.example.com
}
"""

_VERSION_LTM = "Product: BIG-IP\nVersion: 17.1.1\nBuild: 0.0.4\nEdition: Point Release\n"

# One oversized message to exercise raw_line truncation in the persisted copy.
_LONG_MSG = "X" * 5000

_LTM_LOG = (
    "\n".join(
        [
            "Jun  1 10:00:00 test-bigip err mcpd[1234]: 01010028:3: "
            "No members available for pool /Common/test_pool",
            "Jun  1 10:00:05 test-bigip notice mcpd[1234]: 01070638:5: "
            "Pool /Common/test_pool member /Common/192.0.2.20:80 monitor status down.",
            "Jun  1 10:05:00 test-bigip notice mcpd[1234]: 01070727:5: "
            "Pool /Common/test_pool member /Common/192.0.2.20:80 monitor status up.",
            f"Jun  1 10:10:00 test-bigip err tmm[2222]: 01010101:3: {_LONG_MSG}",
        ]
    )
    + "\n"
)


def _build_synthetic_qkview() -> bytes:
    """Build a minimal TMOS-shaped qkview (flat gzip tar) in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in [
            ("config/bigip.conf", _BIGIP_CONF),
            ("config/bigip_base.conf", _BASE_CONF),
            ("VERSION.LTM", _VERSION_LTM),
            ("var/log/ltm", _LTM_LOG),
        ]:
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = 1750000000
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


_QKVIEW_BYTES = _build_synthetic_qkview()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with the analyses DB + log-index dir redirected to tmp_path."""
    db_path = str(tmp_path / "f5_assistant_test.db")
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "LOG_INDEX_DIR", str(tmp_path / "log_index"))
    with TestClient(main.app) as c:
        yield c, db_path


def _upload(c: TestClient, filename: str = "synthetic.qkview", body: bytes = _QKVIEW_BYTES):
    return c.post("/api/analyze", content=body, headers={"X-Filename": filename})


def _ndjson_events(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


class TestUploadValidation:
    def test_rejects_bad_extension(self, client):
        c, _ = client
        resp = _upload(c, filename="notes.txt", body=b"hello")
        assert resp.status_code == 400
        assert "archive" in resp.json()["detail"]

    def test_rejects_non_archive_content_via_magic_bytes(self, client):
        c, _ = client
        resp = _upload(c, body=b"this is definitely not a gzip tar archive")
        assert resp.status_code == 400
        assert "not a valid gzip/qkview archive" in resp.json()["detail"]


class TestAnalyzeStream:
    def test_progress_then_result_with_trimmed_persistence(self, client):
        c, db_path = client
        resp = _upload(c)
        assert resp.status_code == 200

        events = _ndjson_events(resp)
        progress = [e for e in events if e["type"] == "progress"]
        results = [e for e in events if e["type"] == "result"]
        assert progress, "expected at least one progress event before the result"
        assert len(results) == 1
        result = results[0]
        assert result["status"] == "success"
        assert result["filename"] == "synthetic.qkview"

        data = result["data"]
        assert data["device_info"]["hostname"] == "test-bigip.example.com"
        assert data["device_info"]["product"] == "BIG-IP"
        rule_names = {f["rule_name"] for f in data["findings"]}
        assert "pool_no_members" in rule_names  # msg_code 01010028
        assert "pool_member_flap" in rule_names  # 01070638 + 01070727 pair
        # tmos_config never travels on the client stream
        assert "tmos_config" not in data
        analysis_id = data["analysis_id"]

        # Persisted summary row is TRIMMED (entries lose `message`, long
        # raw_line truncated) but keeps tmos_config/apps/partitions full.
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT summary FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        assert row is not None
        stored = json.loads(row[0])
        assert stored["entries"], "expected warning+ entries in the stored summary"
        for entry in stored["entries"]:
            assert "message" not in entry
            assert len(entry["raw_line"]) < 3000
        long_lines = [e["raw_line"] for e in stored["entries"] if "…[truncated," in e["raw_line"]]
        assert long_lines, "the 5000-byte raw_line should be truncated in the stored copy"
        assert "tmos_config" in stored
        assert stored["partitions"] == ["Common"]

        # /apps endpoints still serve full-fidelity data from the stored row.
        apps_resp = c.get(f"/api/qkview/{analysis_id}/apps")
        assert apps_resp.status_code == 200
        apps_body = apps_resp.json()
        assert apps_body["partitions"] == ["Common"]
        assert [a["fullPath"] for a in apps_body["apps"]] == ["/Common/test_vs"]

        details_resp = c.get(f"/api/qkview/{analysis_id}/apps/Common/test_vs")
        assert details_resp.status_code == 200
        assert details_resp.json()["app"]


class TestRetention:
    def test_sweep_deletes_rows_older_than_30_days(self, client):
        c, db_path = client
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO analyses (filename, analysis_date, summary) "
                "VALUES ('ancient.qkview', datetime('now','-45 days'), '{}')"
            )
            conn.execute(
                "INSERT INTO analyses (filename, analysis_date, summary) "
                "VALUES ('recent.qkview', datetime('now','-5 days'), '{}')"
            )
            conn.commit()

        resp = _upload(c)
        assert resp.status_code == 200
        assert any(e["type"] == "result" for e in _ndjson_events(resp))

        with sqlite3.connect(db_path) as conn:
            names = {r[0] for r in conn.execute("SELECT filename FROM analyses").fetchall()}
        assert "ancient.qkview" not in names
        assert "recent.qkview" in names
        assert "synthetic.qkview" in names

    def test_sweep_removes_orphaned_log_index_files(self, client):
        c, db_path = client
        index_dir = pathlib.Path(main.LOG_INDEX_DIR)
        index_dir.mkdir(parents=True, exist_ok=True)

        # An expired row WITH an index file, plus a fileless orphan index.
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO analyses (filename, analysis_date, summary) "
                "VALUES ('ancient.qkview', datetime('now','-45 days'), '{}')"
            )
            ancient_id = cur.lastrowid
            conn.commit()
        ancient_index = index_dir / f"{ancient_id}.db"
        ancient_index.write_bytes(b"stale index")
        orphan_index = index_dir / "424242.db"
        orphan_index.write_bytes(b"orphan index")

        resp = _upload(c)
        assert resp.status_code == 200
        result = next(e for e in _ndjson_events(resp) if e["type"] == "result")
        new_id = result["data"]["analysis_id"]

        assert not ancient_index.exists(), "expired row's index file must be swept"
        assert not orphan_index.exists(), "row-less index file must be swept"
        assert (index_dir / f"{new_id}.db").exists(), "new analysis index must survive"
        # No temp files left behind on the success path either.
        assert not list(index_dir.glob("tmp_*.db"))


def _analyze(c: TestClient) -> int:
    """Upload the synthetic qkview and return its analysis_id."""
    resp = _upload(c)
    assert resp.status_code == 200
    result = next(e for e in _ndjson_events(resp) if e["type"] == "result")
    return result["data"]["analysis_id"]


class TestLogSearch:
    def test_index_persisted_and_logs_returned(self, client):
        c, _ = client
        analysis_id = _analyze(c)

        index_file = pathlib.Path(main.LOG_INDEX_DIR) / f"{analysis_id}.db"
        assert index_file.exists(), "per-analysis log index file must exist after analyze"
        assert index_file.stat().st_size > 0

        resp = c.get(f"/api/qkview/{analysis_id}/logs", params={"facets": "true"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4  # all four synthetic ltm lines
        assert len(body["entries"]) == 4
        assert body["capped"] is False
        first = body["entries"][0]
        assert first["host"] == "test-bigip"
        assert first["process"] == "mcpd"
        assert first["severity"] == "err"
        assert "No members available" in first["raw_line"]
        # Facets feed the UI dropdowns.
        sev_values = {f["value"] for f in body["facets"]["severities"]}
        assert {"err", "notice"} <= sev_values
        proc_values = {f["value"] for f in body["facets"]["processes"]}
        assert {"mcpd", "tmm"} <= proc_values

    def test_fulltext_q_filter(self, client):
        c, _ = client
        analysis_id = _analyze(c)

        resp = c.get(f"/api/qkview/{analysis_id}/logs", params={"q": "monitor status down"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert "monitor status down" in body["entries"][0]["raw_line"]

        # FTS5 operator characters in user input must not 500 (sanitized to
        # quoted word terms: '"pool" "member"').
        resp = c.get(f"/api/qkview/{analysis_id}/logs", params={"q": '"-pool* (member:'})
        assert resp.status_code == 200
        assert resp.json()["total"] == 2  # the two pool-member monitor lines

    def test_severity_filter(self, client):
        c, _ = client
        analysis_id = _analyze(c)

        resp = c.get(f"/api/qkview/{analysis_id}/logs", params={"severity": "err"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert all(e["severity"] == "err" for e in body["entries"])

    def test_limit_offset_pagination(self, client):
        c, _ = client
        analysis_id = _analyze(c)

        page1 = c.get(f"/api/qkview/{analysis_id}/logs", params={"limit": 3}).json()
        assert page1["total"] == 4
        assert len(page1["entries"]) == 3
        assert page1["capped"] is True

        page2 = c.get(
            f"/api/qkview/{analysis_id}/logs", params={"limit": 3, "offset": 3}
        ).json()
        assert len(page2["entries"]) == 1
        assert page2["capped"] is False
        # Pages must not overlap (stable timestamp+id ordering).
        ids1 = {e["raw_line"] for e in page1["entries"]}
        assert page2["entries"][0]["raw_line"] not in ids1

    def test_unknown_analysis_404(self, client):
        c, _ = client
        resp = c.get("/api/qkview/999999/logs")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Analysis not found"

    def test_missing_index_404_for_prefeature_analysis(self, client):
        c, _ = client
        analysis_id = _analyze(c)
        (pathlib.Path(main.LOG_INDEX_DIR) / f"{analysis_id}.db").unlink()

        resp = c.get(f"/api/qkview/{analysis_id}/logs")
        assert resp.status_code == 404
        assert "No log index" in resp.json()["detail"]


class TestAppsEndpoints:
    def test_unknown_analysis_returns_404(self, client):
        c, _ = client
        resp = c.get("/api/qkview/999999/apps")
        assert resp.status_code == 404
