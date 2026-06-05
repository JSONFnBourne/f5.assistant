#!/usr/bin/env python3
"""Identifier-class eval candidates (automated, deterministic).

Samples real identifiers from knowledge.db (read-only) and renders each through a FIXED template.
No LLM. Gold (expected_doc_ids) = the sampled doc_id (tautological).

Output: eval/harvest/identifier_candidates.jsonl  (harness schema, gold pre-filled)
"""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DB = os.path.join(REPO, "db", "knowledge.db")
OUT = os.path.join(HERE, "identifier_candidates.jsonl")
SEED = 42
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)


def ro():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def sample(rows, k, rnd):
    rows = list(rows)
    return rnd.sample(rows, min(k, len(rows)))


def main() -> None:
    rnd = random.Random(SEED)
    c = ro().cursor()
    cands: list[dict] = []
    n = 0

    def add(question, doc_id, qt, note):
        nonlocal n
        n += 1
        cands.append({
            "id": f"i{n:03d}",
            "question": question,
            "expected_doc_ids": [doc_id],
            "query_type": qt,
            "notes": note,
        })

    # 8 K-articles: 5 f5_kb + 3 f5_security (real K-number doc_ids)
    kb = sample(c.execute(
        "SELECT doc_id,title FROM documents WHERE source='f5_kb' AND doc_id LIKE 'K%'").fetchall(), 5, rnd)
    sec = sample(c.execute(
        "SELECT doc_id,title FROM documents WHERE source='f5_security' AND doc_id LIKE 'K%'").fetchall(), 3, rnd)
    for did, _title in kb + sec:
        add(f"What does F5 article {did} address?", did, "k-number",
            "identifier-class; tautological gold=sampled doc_id; seed=42")

    # 3 CVEs from keywords (prefer f5_security); gold = the article carrying that CVE
    cve_rows = c.execute(
        "SELECT doc_id,keywords,source FROM documents "
        "WHERE keywords LIKE '%CVE-%' ORDER BY (source='f5_security') DESC").fetchall()
    seen_cve: set[str] = set()
    picks = []
    for did, kw, _src in cve_rows:
        m = CVE_RE.search(kw or "")
        if not m:
            continue
        cve = m.group(0).upper()
        if cve in seen_cve:
            continue
        seen_cve.add(cve)
        picks.append((cve, did))
    for cve, did in sample(picks, 3, rnd):
        add(f"Which F5 article documents {cve}?", did, "cve",
            "identifier-class; tautological gold=article carrying this CVE in keywords; seed=42")

    # 3 iRule commands (title is the 'NS::cmd' form)
    iru = sample(c.execute(
        "SELECT doc_id,title FROM documents WHERE source='irules' AND title LIKE '%::%'").fetchall(), 3, rnd)
    for did, title in iru:
        add(f"What does the {title} iRule command do?", did, "irule",
            "identifier-class; tautological gold=sampled doc_id; seed=42")

    # 2 RFCs
    rfc = sample(c.execute(
        "SELECT doc_id,title FROM documents WHERE source='rfc' AND doc_id LIKE 'rfc%'").fetchall(), 2, rnd)
    for did, _title in rfc:
        num = did[3:]
        add(f"What does RFC {num} specify?", did, "rfc",
            "identifier-class; tautological gold=sampled doc_id; seed=42")

    with open(OUT, "w", encoding="utf-8") as fh:
        for row in cands:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    by = {}
    for r in cands:
        by[r["query_type"]] = by.get(r["query_type"], 0) + 1
    print(f"wrote {len(cands)} identifier candidates -> {OUT}")
    print("by query_type:", json.dumps(by))


if __name__ == "__main__":
    main()
