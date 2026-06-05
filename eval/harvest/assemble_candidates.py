#!/usr/bin/env python3
"""Validate identifier gold against documents.doc_id and assemble a runnable candidate set.

This is the step-3 validation/assembly, run early (user asked to "hit it" before the manual
accept pass). It does NOT overwrite eval/questions.jsonl — it writes a separate
assembled_candidates.jsonl so the curated smoke set stays intact and nothing is marked "accepted".

- Identifier rows: gold validated vs documents.doc_id (exact -> K/bare-numeric normalization).
- Concept rows: carried through with EMPTY gold (they remain UNSCORED downstream).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DB = os.path.join(REPO, "db", "knowledge.db")
IDENT = os.path.join(HERE, "identifier_candidates.jsonl")
CONCEPT = os.path.join(HERE, "concept_candidates.jsonl")
OUT = os.path.join(HERE, "assembled_candidates.jsonl")


def norm(doc_id: str) -> str:
    """Normalize for tolerant matching: K-articles -> bare numeric (drop K + leading zeros)."""
    s = doc_id.strip()
    m = re.match(r"^[Kk]0*([0-9]+)$", s)
    return m.group(1) if m else s.lower()


def main() -> None:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True).cursor()
    exact = {d for (d,) in c.execute("SELECT doc_id FROM documents")}
    normed = {}
    for d in exact:
        normed.setdefault(norm(d), d)

    ident = [json.loads(l) for l in open(IDENT, encoding="utf-8") if l.strip()]
    concept = [json.loads(l) for l in open(CONCEPT, encoding="utf-8") if l.strip()]

    print("===== identifier gold validation vs documents.doc_id =====")
    found = notfound = normfixed = 0
    for r in ident:
        for g in r["expected_doc_ids"]:
            if g in exact:
                found += 1
            elif norm(g) in normed:
                normfixed += 1
                print(f"  ~ {r['id']}: {g!r} not exact, matched via normalization -> {normed[norm(g)]!r}")
            else:
                notfound += 1
                print(f"  ✗ {r['id']}: gold {g!r} NOT FOUND in documents.doc_id")
    print(f"  exact={found}  norm-matched={normfixed}  NOT-found={notfound}  (of {sum(len(r['expected_doc_ids']) for r in ident)} gold refs)")

    # assemble into harness schema
    rows = []
    for r in ident:
        rows.append({
            "id": r["id"], "question": r["question"],
            "expected_doc_ids": r["expected_doc_ids"],
            "query_type": r["query_type"], "notes": r.get("notes", ""),
        })
    for r in concept:
        rows.append({
            "id": r["id"], "question": r["question"],
            "expected_doc_ids": [],  # UNSCORED until human review supplies gold
            "query_type": r["query_type"],
            "notes": f"harvested concept (unscored); src={r.get('source_url','')}",
        })
    with open(OUT, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nassembled {len(rows)} rows ({len(ident)} identifier scored + {len(concept)} concept unscored) -> {OUT}")


if __name__ == "__main__":
    main()
