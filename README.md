## What It Is

A self-contained, locally-hosted intelligence platform for F5 BIG-IP network
infrastructure — combining a diagnostic engine, a knowledge assistant, iRule
tooling, and a fine-tuning pipeline for training domain-expert language models.
Everything runs on a single desktop machine. No external APIs, no cloud
dependencies, full data control.

---

## The Three Pillars

### 1. The Web Application (`/webapp`, port 3000)

A Next.js 16 app that is the front door to everything. Six tools:

- **QKView Analyzer** — Drag and drop a BIG-IP diagnostic archive (`.qkview`),
  get back a structured report of what's wrong: pool members flapping, HA
  failures, licensing issues, memory pressure, certificate problems. The rule
  engine is YAML-driven so new known-issues can be added without touching code.

- **Knowledge Base** — Chat interface backed by a 46,931-document SQLite
  database of F5 documentation. Questions are classified, relevant docs are
  fetched via multi-pass FTS5 retrieval, and a local LLM generates answers
  strictly grounded in the retrieved context — no hallucinated facts.

- **iRule Reference** — Alphabetical, searchable index of every iRule command,
  event, and operator. An offline copy of the F5 iRules API reference.

- **iRule Generator** — Protocol-aware UI that lets you pick a traffic event,
  conditions, and actions, then sends a skeleton to the local LLM to produce
  valid Tcl/iRule code.

- **iRule Validator** — Static analyzer that catches syntax errors, invalid
  event/command combinations, and missing profile dependencies before you ever
  touch a BIG-IP.

- **Discussion** — Open-ended chat with the local LLM for general F5 Q&A.

---

### 2. The QKView Analyzer Backend (`/backend`, port 8000)

A FastAPI service that does the heavy lifting when a QKView is uploaded:

1. Decompresses and unpacks the archive (`.qkview`, `.tgz`, `.tar`)
2. Detects the archive family — four incompatible layouts (TMOS VE/BIG-IP,
   F5OS rSeries, VELOS partition, VELOS controller/syscon)
3. Parses syslog-format logs — both classic `MMM DD HH:MM:SS` and ISO 8601
4. Builds an in-memory SQLite FTS5 log index for fast rule queries
5. Parses `bigip.conf` for VLANs, self-IPs, and hostnames (BIG-IP only)
6. Runs a YAML rule engine — matching on F5 message codes (e.g. `01070638`)
   or regex patterns, with time-window correlation for paired events
7. Returns severity-ranked findings with remediation recommendations

Two rule files cover BIG-IP TMOS issues (`tmos_known_issues.yaml`) and F5OS
hardware health (`f5os_hardware.yaml`).

---

### 3. The ML Pipeline (`/pipeline`)

An end-to-end supervised fine-tuning system for producing domain-expert language
models. Two active pipelines:

- **`f5nse`** — Broad F5 expert (TMSH, LTM, DNS, and more). Base model:
  `Llama-3.1-8B-Instruct`. Stages: scrape approved F5 URLs → clean → chunk at
  512 tokens → auto-generate QA pairs → LLM-grade quality → package → validate →
  90/10 split → QLoRA fine-tune → evaluate → merge LoRA adapters → export GGUF →
  serve.

- **`irule`** — iRules specialist. Base model: `Llama-3.2-3B-Instruct` (4-bit).
  Same stages plus two extras after `chunk`: entity extraction and rulebook
  derivation that enforce valid iRule syntax during training.

Training data is sourced **exclusively from approved F5 documentation URLs** —
no human-written data. A judge LLM auto-generates and scores every QA pair.
Active crawl sources: 3 iRules API pages (Operators, Events, Commands) and 3
TMSH reference sections (general, commands, modules).

---

## The Knowledge Layer

The `/knowledge` directory is a curated library of F5 domain assets organized by
product module: `tmos/`, `ltm/`, `dns/`, `apm/`, `asm/`, `sslo/`, `swg/`,
`f5os/`, `irules/`, `automation/`. It includes full OpenAPI/Swagger specs for
F5OS-A 1.8.3 and F5OS-C 1.8.1.

The `/db` directory holds the primary SQLite knowledge database (`knowledge.db`,
FTS5-indexed, 46,931 documents). The session/analysis state database is
`backend/f5_assistant.db` (populated at runtime) — **not** in `/db`.

### Knowledge Database Breakdown

| Source         | Documents | Content                                      |
|----------------|-----------|----------------------------------------------|
| `f5_kb`        | 29,445    | F5 K-articles (my.f5.com)                   |
| `rfc`          |  9,657    | IETF RFC standards                           |
| `f5_security`  |  4,711    | F5 security advisories (CVE-tagged)          |
| `irules`       |  1,574    | iRules API reference (clouddocs)             |
| `techdocs`     |    717    | F5 TechDocs (APM, BIG-IP manuals)           |
| `xc_techdocs`  |    708    | F5 Distributed Cloud TechDocs               |
| `clouddocs`    |    116    | AS3/DO schema, F5OS API                     |
| `community`    |      3    | F5 community articles                        |
| **Total**      | **46,931**|                                              |

Retrieval is multi-pass: K-number exact match → CVE exact match → iRules
`Namespace::command` match → FTS5 BM25 search (title weighted 10×, keywords 5×,
content 1×) → fallback OR search. A 3× prefetch + title deduplication step
prevents any single document title from flooding the result slots.

---

## The Stack

| Layer         | Technology                                                  |
|---------------|-------------------------------------------------------------|
| Frontend      | Next.js 16, TypeScript, Tailwind CSS                        |
| Backend       | FastAPI, Python 3.12, Uvicorn                               |
| Local LLM     | Ollama — `qwen2.5:14b` (Q4_K_M, 14B params, 9.0 GB)        |
| Other models  | `llama3.1:8b`, `qwen2.5:7b`, `llama3.2:latest` (code fallback) installed |
| Fine-tuning   | PyTorch 2.5, HuggingFace TRL, PEFT, BitsAndBytes (4-bit QLoRA) |
| Storage       | SQLite (FTS5), JSONL                                        |
| Hardware      | RTX 4080 SUPER (16 GB VRAM), Ryzen 7 5800X3D, 30 GiB RAM   |

---

## End-to-End Workflow Examples

### QKView Upload
1. Engineer drags `.qkview` into the `/qkview` page
2. Frontend POSTs to `/api/analyze` (proxied to FastAPI on port 8000)
3. Backend extracts, parses logs, indexes, applies rules, returns JSON
4. UI renders findings with severity colors, recommendations, and sample log lines

### Knowledge Question
1. Engineer types a question in `/knowledge`
2. Classifier routes to F5, RFC, or general context
3. `/api/knowledge` runs multi-pass retrieval against `knowledge.db`
4. The local LLM (qwen2.5:14b) answers strictly from retrieved context at temperature 0.3
5. Streamed markdown response with source citations

### iRule Generation
1. Engineer selects protocol and desired traffic events in `/generator`
2. Builder constructs a Tcl skeleton
3. `/api/generate` sends skeleton + protocol to qwen2.5:14b via Ollama
4. Model returns completed, hardened iRule code

---

## Bottom Line

A private, air-gapped F5 expert system that can diagnose broken BIG-IP devices,
answer deep F5 technical questions from 46,931 authoritative source documents,
help engineers write and validate iRules, and continuously train domain-specific
language models — all on a single desktop machine, with no external API
dependencies, no cloud costs, and full data ownership.
