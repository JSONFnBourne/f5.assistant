#!/usr/bin/env bash
# Resumable, throttled fetch of all F5 bug tracker pages → data/bugtracker/pages/.
# Canonical (version-controlled) fetcher for the quarterly bugtracker refresh.
#
# Endpoint: https://cdn.f5.com/product/bugtracker/<ID######>.html  (200, ~17 KB;
# the .html suffix is REQUIRED — without it the CDN 404s).
#
# Inputs (under data/bugtracker/, gitignored):
#   ids.txt           one "ID######.html" per line (the bug IDs to fetch)
# Outputs (gitignored):
#   pages/ID######.html        one page per bug
#   fetch_log.jsonl            durable per-doc scrape provenance (id, ts, code, bytes)
#   download_failures.txt      "FAIL <code> <id>" lines (404s = withdrawn bug IDs)
#
# Resumable: re-running skips pages already present >1 KB, so only new IDs are
# pulled. To force-refresh existing bugs (they get updated), delete pages first.
# The ingester (scripts/ingest_bugtracker.py) reads fetch_log.jsonl for the
# "Data scraped: <date>" provenance stamped into each document.
set -u
DIR="$(cd "$(dirname "$0")/../data/bugtracker" && pwd)"
cd "$DIR" || { echo "missing data/bugtracker (needs ids.txt)"; exit 1; }
mkdir -p pages
: > download_failures.txt

fetch_one() {
  local id="$1" out="pages/$1"
  if [ -s "$out" ] && [ "$(stat -c%s "$out")" -gt 1000 ]; then return 0; fi
  local code
  code=$(curl -sS -A "Mozilla/5.0 (X11; Linux x86_64)" -L --max-time 45 --retry 3 --retry-delay 2 \
    "https://cdn.f5.com/product/bugtracker/$id" -o "$out" -w "%{http_code}" 2>/dev/null)
  if [ "$code" != "200" ] || [ ! -s "$out" ] || [ "$(stat -c%s "$out")" -le 1000 ]; then
    echo "FAIL $code $id" >> download_failures.txt
    rm -f "$out"
  else
    printf '{"id":"%s","ts":"%s","code":200,"bytes":%s}\n' \
      "${id%.html}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(stat -c%s "$out")" >> fetch_log.jsonl
  fi
}
export -f fetch_one

total=$(wc -l < ids.txt)
echo "fetching $total bugtracker pages (concurrency 6, resumable) into $DIR/pages …"
xargs -P 6 -I {} bash -c 'fetch_one "$@"' _ {} < ids.txt

have=$(find pages -name 'ID*.html' -size +1k | wc -l)
fail=$(wc -l < download_failures.txt)
echo "done: $have/$total pages present (>1KB); $fail failures (see download_failures.txt)"
