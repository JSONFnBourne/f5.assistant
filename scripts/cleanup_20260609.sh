#!/usr/bin/env bash
# Repo hygiene cleanup — staged from the 2026-06-09 audit. Review, then run:
#   bash scripts/cleanup_20260609.sh phase1     # verified-safe deletes (~40 MB + clutter)
#   bash scripts/cleanup_20260609.sh phase2     # big reclaims (~4.4 GB) — stops/restarts services
# Every target was individually verified during the audit (see notes inline).
set -euo pipefail
cd "$(dirname "$0")/.."

phase1() {
  # HAR captures — my.f5.com.har is an authenticated-session capture (cookies/tokens); shred-worthy
  rm -fv archive/localhost*.har archive/v7_localhost.har data/my.f5.com.har

  # Editor .bak files — real history is in git (commit 1da9454 / torch-2.5 bump)
  rm -fv backend/qkview_analyzer/extractor.py.bak-20260603-064623 \
         backend/qkview_analyzer/tmstat_parser.py.bak-20260603-064623 \
         pipeline/requirements.txt.bak-20260526-072513-pre-torch2.5

  # April dev-launcher logs — logging moved to journald with the systemd units
  rm -fv logs/backend.log logs/frontend.log logs/frontend-dev.log

  # Stale forklift runbook (untracked, every step completed; superseded by SESSION_STATE.md)
  rm -fv HANDOFF.md

  # 0-byte decoy — the live DB is backend/f5_assistant.db; this empty file misleads (docs ref it)
  rm -fv db/f5_assistant.db

  # Byte-identical (md5 17007a26…) to the tracked knowledge/tmos/tmsh_17.0.0.pdf
  rm -fv data/tmsh_17.0.0.pdf

  # Never-populated dirs from retired scrape paths
  rmdir -v data/markdown data/f5/raw data/f5/processed data/rfc/docs data/rfc/index 2>/dev/null || true
  rmdir -v data/f5 data/rfc archive 2>/dev/null || true
  echo "phase1 done"
}

phase2() {
  # 1) Prune backend/f5_assistant.db: 186 rows / 2.36 GB of summary blobs (one row is 498 MB).
  #    Keeps the last 30 days. Stop the backend so the file isn't open during VACUUM.
  systemctl --user stop f5-backend.service
  python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('backend/f5_assistant.db')
n = c.execute("DELETE FROM analyses WHERE analysis_date < datetime('now','-30 days')").rowcount
c.commit(); print(f'deleted {n} rows'); c.execute('VACUUM'); c.close()
EOF
  systemctl --user start f5-backend.service

  # 2) Superseded trainer checkpoints (1.15 GB) — final adapter lives at irule-lora/ root
  rm -rfv pipeline/data/models/irule-lora/checkpoint-100 pipeline/data/models/irule-lora/checkpoint-180

  # 3) Pre-rejudge knowledge.db backup from Jun 3 (943 MB + sidecars) — delete once Jun 3 change is trusted
  rm -fv db/knowledge.db.bak-20260603-070947 db/knowledge.db.bak-20260603-070947-shm db/knowledge.db.bak-20260603-070947-wal

  # 4) Fold the 221 MB knowledge.db WAL back in (webapp must not hold the DB open)
  systemctl --user stop f5-webapp.service
  python3 -c "import sqlite3; sqlite3.connect('db/knowledge.db').execute('PRAGMA wal_checkpoint(TRUNCATE)')"
  systemctl --user start f5-webapp.service
  echo "phase2 done"
}

case "${1:-}" in
  phase1) phase1 ;;
  phase2) phase2 ;;
  *) echo "usage: $0 {phase1|phase2}"; exit 1 ;;
esac
