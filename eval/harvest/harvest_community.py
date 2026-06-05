#!/usr/bin/env python3
"""Concept/troubleshooting eval candidates harvested from community.f5.com.

Real questions only — NO LLM generation, NO proposed gold, retriever NEVER run here.

Flow: technicalforum sitemaps -> thread URLs -> (stratify by tag from slug) -> fetch selected
thread pages (throttled) -> extract subject + first-post body + tags from __NEXT_DATA__ ->
filter (drop announcements / <8-word posts; near-dedup on title) -> ~50 candidates.

robots.txt (checked 2026-06-03): /discussions/ and /sitemap* are allowed; only admin/user/
lightbox paths are disallowed. Polite UA + throttle.

Outputs:
  concept_candidates.jsonl   raw harvested rows (expected_doc_ids EMPTY)
  candidates_review.md       unified human-review file (identifier + concept sections)
"""
from __future__ import annotations

import gzip
import html
import json
import os
import random
import re
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "f5assistant-eval-harvester/0.1 (local eval candidate assembly; GET only)"
SITEMAPS = [
    "https://community.f5.com/sitemap_1_technicalforum.xml.gz",
    "https://community.f5.com/sitemap_2_technicalforum.xml.gz",
]
SEED = 42
THROTTLE_S = 1.3
PER_TAG_TARGET = 10
TOTAL_TARGET = 50
MAX_FETCHES = 90
MIN_WORDS = 8

OUT_JSONL = os.path.join(HERE, "concept_candidates.jsonl")
OUT_MD = os.path.join(HERE, "candidates_review.md")
ID_JSONL = os.path.join(HERE, "identifier_candidates.jsonl")

# Tag buckets (priority order avoids double-counting). query_type maps to the harness enum;
# DNS/GTM, LTM, upgrades have no dedicated enum value -> "concept".
TAG_RULES = [
    ("F5OS",    ["f5os", "velos", "rseries", "r5800", "r5900", "r10800", "r10900", "r2800", "r4800", "tenant", "chassis-partition"], "f5os"),
    ("iRules",  ["irule", "irules", "-tcl-", "tcl-", "istats"], "irule"),
    ("DNS/GTM", ["dns", "gtm", "wide-ip", "wideip", "gslb", "zonerunner", "zone-runner"], "concept"),
    ("upgrades",["upgrade", "upgrading", "migration", "migrate", "downgrade", "hotfix", "rollback"], "concept"),
    ("LTM",     ["ltm", "virtual-server", "-pool-", "snat", "persistence", "health-monitor", "-monitor-", "irule-pool", "load-balanc"], "concept"),
]
ANNOUNCE = ["announc", "now-available", "general-availability", "introducing",
            "welcome-to", "end-of-life", "eol-", "is-now", "release-notes"]


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":  # gzip magic
        data = gzip.decompress(data)
    return data


def sitemap_locs(xml: bytes) -> list[str]:
    return re.findall(r"<loc>([^<]+)</loc>", xml.decode("utf-8", "replace"))


def slug_of(url: str) -> str:
    parts = [p for p in url.split("/") if p]
    # .../discussions/technicalforum/<slug>/<id>
    return parts[-2] if len(parts) >= 2 and parts[-1].isdigit() else parts[-1]


def classify(slug: str):
    s = slug.lower()
    for tag, kws, qt in TAG_RULES:
        if any(k in s for k in kws):
            return tag, qt
    return None, None


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def extract_subject_body(page: bytes):
    m = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return None, None, []
    try:
        d = json.loads(m.group(1).decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None, None, []
    subject = {"v": None}
    body = {"v": None}
    tags: list[str] = []

    def walk(o, depth=0):
        if depth > 14:
            return
        if isinstance(o, dict):
            if subject["v"] is None and isinstance(o.get("subject"), str) and o["subject"].strip():
                subject["v"] = o["subject"].strip()
            if body["v"] is None and isinstance(o.get("body"), str) and len(strip_html(o["body"])) > 20:
                body["v"] = o["body"]
            t = o.get("tags")
            if isinstance(t, list):
                for x in t:
                    if isinstance(x, str):
                        tags.append(x)
                    elif isinstance(x, dict) and isinstance(x.get("text"), str):
                        tags.append(x["text"])
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for x in o:
                walk(x, depth + 1)

    walk(d)
    return subject["v"], body["v"], tags


def main() -> None:
    rnd = random.Random(SEED)

    # 1. gather thread URLs from sitemaps
    urls: list[str] = []
    for sm in SITEMAPS:
        try:
            urls += sitemap_locs(fetch(sm))
        except Exception as e:  # noqa: BLE001
            print(f"  sitemap skip {sm}: {e}")
    threads = [u for u in urls if "/discussions/technicalforum/" in u and u.rstrip("/").split("/")[-1].isdigit()]
    print(f"thread URLs in sitemaps: {len(threads)}")

    # 2. bucket by tag (slug-inferred), drop announcements, queue per tag
    buckets: dict[str, list[str]] = {t[0]: [] for t in TAG_RULES}
    for u in threads:
        slug = slug_of(u)
        if any(a in slug.lower() for a in ANNOUNCE):
            continue
        tag, _qt = classify(slug)
        if tag:
            buckets[tag].append(u)
    for t in buckets:
        rnd.shuffle(buckets[t])
        print(f"  bucket {t:8s}: {len(buckets[t])} candidate URLs")

    # 3. fetch round-robin per tag until targets met (bounded, throttled, post-filter on real data)
    qt_by_tag = {t[0]: t[2] for t in TAG_RULES}
    accepted: list[dict] = []
    per_tag_count = {t: 0 for t in buckets}
    seen_titles: set[str] = set()
    fetches = 0
    n = 0
    progressing = True
    while progressing and fetches < MAX_FETCHES and len(accepted) < TOTAL_TARGET:
        progressing = False
        for tag in buckets:
            if per_tag_count[tag] >= PER_TAG_TARGET or not buckets[tag]:
                continue
            if fetches >= MAX_FETCHES or len(accepted) >= TOTAL_TARGET:
                break
            url = buckets[tag].pop()
            progressing = True
            try:
                page = fetch(url)
                fetches += 1
                time.sleep(THROTTLE_S)
            except Exception as e:  # noqa: BLE001
                print(f"    fetch fail {url}: {e}")
                continue
            subject, body, _tags = extract_subject_body(page)
            if not subject:
                continue
            qtext = strip_html(body) if body else ""
            wc = len(qtext.split())
            if wc < MIN_WORDS:
                continue  # drop too-short first posts
            norm = re.sub(r"[^a-z0-9]+", "", subject.lower())
            if norm in seen_titles:
                continue  # near-dedup on title
            seen_titles.add(norm)
            n += 1
            per_tag_count[tag] += 1
            accepted.append({
                "id": f"c{n:03d}",
                "tag": tag,
                "query_type": qt_by_tag[tag],
                "question": subject,
                "expected_doc_ids": [],
                "source_url": url,
                "first_post_excerpt": qtext[:300],
                "first_post_words": wc,
            })

    print(f"fetched {fetches} threads; accepted {len(accepted)} concept candidates")
    print("  per tag:", json.dumps(per_tag_count))

    with open(OUT_JSONL, "w", encoding="utf-8") as fh:
        for r in accepted:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    write_review_md(accepted)
    print(f"wrote {OUT_JSONL}")
    print(f"wrote {OUT_MD}")


def write_review_md(concept: list[dict]) -> None:
    ident = []
    if os.path.exists(ID_JSONL):
        ident = [json.loads(l) for l in open(ID_JSONL, encoding="utf-8") if l.strip()]

    lines = []
    lines.append("# Eval candidate review\n")
    lines.append("Flip `- [ ] accept` to `- [x] accept` to keep a row. Edit `- question:` freely. "
                 "For concept rows, fill `- expected_doc_ids:` with real `documents.doc_id` values "
                 "(comma-separated). Identifier rows have tautological gold pre-filled.\n")
    lines.append(f"Identifier candidates: {len(ident)} · Concept candidates: {len(concept)}\n")

    lines.append("\n---\n\n## Identifier class (auto · gold pre-filled)\n")
    for r in ident:
        lines.append(f"### {r['id']}  ·  tag: identifier  ·  query_type: {r['query_type']}")
        lines.append("- [ ] accept")
        lines.append(f"- question: {r['question']}")
        lines.append(f"- expected_doc_ids: {', '.join(r['expected_doc_ids'])}")
        lines.append(f"- source_url: ")
        lines.append(f"- notes: {r['notes']}")
        lines.append("")

    lines.append("\n---\n\n## Concept / troubleshooting class (harvested · fill expected_doc_ids)\n")
    by_tag: dict[str, list[dict]] = {}
    for r in concept:
        by_tag.setdefault(r["tag"], []).append(r)
    for tag in sorted(by_tag):
        lines.append(f"\n### ── tag: {tag} ({len(by_tag[tag])}) ──\n")
        for r in by_tag[tag]:
            lines.append(f"### {r['id']}  ·  tag: {r['tag']}  ·  query_type: {r['query_type']}")
            lines.append("- [ ] accept")
            lines.append(f"- question: {r['question']}")
            lines.append("- expected_doc_ids: ")
            lines.append(f"- source_url: {r['source_url']}")
            lines.append(f"- first_post: {r['first_post_excerpt']}")
            lines.append(f"- notes: harvested first-post; {r['first_post_words']}-word OP")
            lines.append("")

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
