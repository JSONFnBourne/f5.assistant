# tmsh Pipeline

This repository contains an end-to-end data curation and fine-tuning pipeline for the **tmsh** subject-matter expert model. The workflow is modular and scriptable so you can re-run individual stages as new content is approved.

All commands assume the project root (`/home/jsonfnbourne/ml_projects/tmsh`) and the active `tmsh` conda environment.  
Add the package to your `PYTHONPATH` (one-time per shell): `export PYTHONPATH=src`.

---

## 1. Pipeline Overview

Stages map to the requested flow (scrape → clean → chunk → QA → grade → split → train → eval → merge) with two extras for deployment (GGUF export, inference server):

1. **scrape** – crawl whitelisted sources, respect robots.txt, enforce a 90-day TTL via `state/crawl_state.sqlite3`.
2. **clean** – strip markup, dedupe, and persist structured text.
3. **chunk** – tokenize with the chosen base tokenizer and emit overlapping passages.
4. **generate** – produce JSON QA pairs from chunks using the generator model (defaults to `meta-llama/Llama-3.2-3B-Instruct` in 4-bit on GPU).
5. **grade** – judge QA quality and filter by score threshold.
6. **split** – create deterministic 90/10 train/eval splits.
7. **train** – run QLoRA fine-tuning (TRL `SFTTrainer`) on the curated training split.
8. **evaluate** – grade model outputs on the eval split and report accuracy plus average judge score.
9. **merge** – merge LoRA adapters back into full weights for export.
10. **export** – convert merged weights to GGUF through the official `llama.cpp` script.
11. **serve** – launch a lightweight FastAPI inference server backed by the LoRA (or merged) weights.

Every stage is exposed via a single CLI: `python -m tmsh.cli <command> [flags]`.

---

## 2. Seed Commands & Flags

Note: The canonical, up-to-date commands are reflected here and match the 3B Quickstart. Use this section as your single source of truth to avoid duplication.
All paths are configurable.

### Scrape

```bash
python -m tmsh.cli scrape \
  --source-config configs/crawl_sources.yaml \
  --output-dir data/raw \
  --state-path state/crawl_state.sqlite3 \
  --concurrency 2 \
  --max-pages 0 \
  --throttle-seconds 1.0 \
  --force-refresh False \
  --respect-robots True \
  --allowed-content-types text/html text/plain application/json \
  --allowed-extensions .html .htm .shtml .xhtml \
  --log-level INFO
```

Use `--max-pages` (0 or omitted = unlimited) to cap downloads per run. `--force-refresh` bypasses the 90-day TTL when you explicitly need immediate refreshes.

### Clean

```bash
python -m tmsh.cli clean \
  --raw-dir data/raw \
  --output-dir data/clean \
  --preserve-headings True \
  --min-characters 200 \
  --dedupe True \
  --overwrite False
```

- Clean now strips the BIG-IP API disclaimer, removes NBSP/`¶` artifacts, and (for iRules pages) stores a `sections` object containing Description, Command List, Associated Events, Syntax, and Available Commands.

### Chunk

```bash
python -m tmsh.cli chunk \
  --clean-dir data/clean \
  --output-path data/chunks/chunks.jsonl \
  --tokenizer-name meta-llama/Llama-3.2-3B-Instruct \
  --chunk-size-tokens 512 \
  --chunk-overlap-tokens 64 \
  --min-chunk-chars 120 \
  --max-chunks 0 \
  --overwrite
```

### Rulebook (Optional but Recommended)

```bash
python -m tmsh.cli rulebook \
  --chunks-path data/chunks/chunks.jsonl \
  --output-path data/rulebook/command_tokens.json \
  --event-order-path tmshs_https_event_order.jsonl
```

- Extracts valid iRules command tokens (e.g., `HTTP::respond`), event names (e.g., `SERVERSSL_DATA`), and supported operators (`contains`, `&&`, etc.) from the current chunks. Generation and grading reject QA pairs that reference tokens outside this rulebook.

### QA Generation

```bash
python -m tmsh.cli generate \
  --chunks-path data/chunks/chunks.jsonl \
  --output-path data/datasets/qa_raw.jsonl \
  --generator-model meta-llama/Llama-3.2-3B-Instruct \
  --pairs-per-chunk 2 \
  --max-records 250 \
  --temperature 0.2 \
  --top-p 0.9 \
  --max-new-tokens 512 \
  --device cuda \
  --overwrite \
  --skip-if-exists
```

Generation logs any unparseable, rulebook-invalid, or out-of-context responses to `data/datasets/qa_failures.jsonl` for later review.

### Judge & Filter

```bash
python -m tmsh.cli grade \
  --qa-path data/datasets/qa_raw.jsonl \
  --output-path data/datasets/qa_graded.jsonl \
  --judge-model meta-llama/Llama-3.2-3B-Instruct \
  --min-overall 0.85 \
  --overwrite \
  --skip-if-exists
```

- Judge output now mirrors the external schema: each record includes `judge.scores.{factual, linguistic, domain, overall}`, plus `verdict`, `feedback`, `missing_facts`, `tags`, and `notes`. Retention thresholds apply to the `overall` score.
- When you plan to rely on externally graded data (e.g., `qa_graded.jsonl` produced by ChatGPT/GPT-5), run `grade` with `--skip-if-exists` or skip the command entirely after validating the imported file (see below).

### Package for External Review

```bash
python -m tmsh.cli package \
  --input-path data/chunks/chunks.jsonl \
  --additional-file data/datasets/qa_raw.jsonl \
  --output-path data/external/qa_package.zip
```

- The archive includes the selected files plus an auto-generated `summary.json` (record counts and sample prompts) for quick off-platform grading.

### Validate External Scores

```bash
python -m tmsh.cli validate \
  --graded-path data/datasets/qa_graded.jsonl \
  --min-records 200 \
  --min-overall 0.7 \
  --no-expect-scores \
  --no-require-context
```

- Verifies schema, counts, and overall score statistics before you proceed to `split`/`train`.

### Split

```bash
python -m tmsh.cli split \
  --graded-path data/datasets/qa_graded.jsonl \
  --train-path data/datasets/train.jsonl \
  --eval-path data/datasets/eval.jsonl \
  --train-ratio 0.9 \
  --seed 42 \
  --shuffle True
```

### Train (QLoRA)

```bash
python -m tmsh.cli train \
  --train-path data/datasets/train.jsonl \
  --eval-path data/datasets/eval.jsonl \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --output-dir data/models/tmsh-lora \
  --lora-r 16 \
  --lora-alpha 16 \
  --lora-dropout 0.05 \
  --learning-rate 2e-4 \
  --num-train-epochs 4 \
  --gradient-accumulation-steps 8 \
  --per-device-train-batch-size 1 \
  --max-seq-length 256 \
  --packing False
```

- Defaults target modest GPUs with 3B in 4‑bit. If you see CUDA OOM, first drop `--max-seq-length` (e.g., 192/128). Adjust `--gradient-accumulation-steps` to trade off memory vs. updates. The trainer logs a pre-train summary so you can avoid “instant” runs.

### Evaluate

```bash
python -m tmsh.cli evaluate \
  --model-path data/models/tmsh-lora \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --eval-path data/datasets/eval.jsonl \
  --judge-model meta-llama/Llama-3.2-3B-Instruct \
  --output-metrics data/models/eval_metrics.json \
  --judge-threshold 0.85 \
  --max-new-tokens 128
```

### Merge Adapters

```bash
python -m tmsh.cli merge \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --lora-path data/models/tmsh-lora \
  --output-dir data/models/tmsh-merged \
  --dtype float16
```

### GGUF Export

```bash
python -m tmsh.cli export \
  --model-dir data/models/tmsh-merged \
  --llama-cpp-path ~/src/llama.cpp \
  --output-path data/models/tmsh.gguf \
  --quantization q4_k_m
```

### Inference Server

```bash
python -m tmsh.cli serve \
  --model-path data/models/tmsh-lora \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --host 0.0.0.0 \
  --port 8000
```

Once `fastapi` and `uvicorn` are installed you can hit `POST /generate` with `{"prompt": "..."}`

---

## 3. Model & Resource Guidance

- **Recommended base**: `meta-llama/Llama-3.2-3B-Instruct`. With 4‑bit quantization it fits comfortably on smaller GPUs and is strong for instruction following.
- **Alternatives**: `Qwen/Qwen2.5-3B-Instruct` and `meta-llama/Llama-3.2-1B-Instruct` for tighter VRAM; `mistralai/Mistral-7B-Instruct-v0.2/0.3` is a solid judge if you have ≥12–16 GB VRAM.
- **GPU**: Prefer 4‑bit with `device_map=auto` and avoid artificial VRAM caps. If you see OOM, reduce `--max-seq-length` or set `--judge-on-cpu` during evaluation.
- **Data scaling**: Start with ~250 records (`generate --max-records`) as requested. Once evaluation plateaus, simply increase `generate`/`grade` limits, re-run `split`, and restart training with the richer dataset.

---

## 4. Adding New Sources

Edit `configs/crawl_sources.yaml` to append new URLs and depth/TTL values. Each entry may optionally specify `allowed_domains`:

```yaml
- url: https://example.com/docs/
  max_depth: 2
  ttl_days: 90
  allowed_domains:
    - example.com
    - docs.example.com
```

Re-run the scrape stage; the TTL will prevent churn unless you pass `--force-refresh True`.

---

## 5. Data Storage Layout

- `data/raw/` – raw HTML payloads (`<sha1>.json` with metadata).
- `data/clean/` – cleaned documents ready to chunk.
- `data/chunks/chunks.jsonl` – tokenized passages with offsets.
- `data/datasets/` – generated QA sets, graded output, and train/eval splits.
- `data/models/` – LoRA checkpoints, merged weights, metrics, and exported GGUF.
- `state/crawl_state.sqlite3` – TTL bookkeeping for the crawler.

Nothing is committed by default so you retain control over artifacts.

---

## 6. Dependencies & Environment Notes

`requirements.txt` covers the core ML stack. Optional extras to enable server/export and faster cleaning:

- fastapi (inference REST server)
- uvicorn[standard] (ASGI runtime)
- lxml (faster HTML parsing during cleaning)
- llama-cpp-python (optional helper alongside llama.cpp conversions)

After updating, run `pip install -r requirements.txt` and install any extras you need.

---

## Quickstart (3B Defaults, GPU)

The repository defaults have been refactored to use `meta-llama/Llama-3.2-3B-Instruct` across generate/judge/train/evaluate. All stages prefer GPU with 4‑bit quantization and `device_map="auto"` for best utilization.

- Ensure your shell has `export PYTHONPATH=src` and the `tmsh` environment active.

Chunk

```bash
python -m tmsh.cli chunk \
  --clean-dir data/clean \
  --output-path data/chunks/chunks.jsonl \
  --tokenizer-name meta-llama/Llama-3.2-3B-Instruct \
  --chunk-size-tokens 512 \
  --chunk-overlap-tokens 64 \
  --overwrite
```

Generate (3B, 4‑bit)

```bash
python -m tmsh.cli generate \
  --chunks-path data/chunks/chunks.jsonl \
  --output-path data/datasets/qa_raw.jsonl \
  --generator-model meta-llama/Llama-3.2-3B-Instruct \
  --pairs-per-chunk 2 \
  --max-records 250 \
  --temperature 0.2 \
  --top-p 0.9 \
  --max-new-tokens 512 \
  --device cuda \
  --overwrite
```

Grade (3B judge, 4‑bit, GPU)

```bash
python -m tmsh.cli grade \
  --qa-path data/datasets/qa_raw.jsonl \
  --output-path data/datasets/qa_graded.jsonl \
  --judge-model meta-llama/Llama-3.2-3B-Instruct \
  --min-overall 0.8 \
  --overwrite
```

Split

```bash
python -m tmsh.cli split \
  --graded-path data/datasets/qa_graded.jsonl \
  --train-path data/datasets/train.jsonl \
  --eval-path data/datasets/eval.jsonl \
  --train-ratio 0.9 \
  --seed 42
```

Train (QLoRA, 3B, VRAM‑friendly)

```bash
python -m tmsh.cli train \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --output-dir data/models/tmsh-lora \
  --lora-r 16 \
  --lora-alpha 16 \
  --learning-rate 2e-4 \
  --num-train-epochs 1 \
  --gradient-accumulation-steps 1 \
  --per-device-train-batch-size 1 \
  --max-seq-length 256 \
  --packing False
```

Evaluate (GPU judge, two‑phase eval)

```bash
python -m tmsh.cli evaluate \
  --model-path data/models/tmsh-lora \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --eval-path data/datasets/eval.jsonl \
  --judge-model meta-llama/Llama-3.2-3B-Instruct \
  --output-metrics data/models/eval_metrics.json \
  --judge-threshold 0.8 \
  --max-new-tokens 128
```

Merge (optional)

```bash
python -m tmsh.cli merge \
  --base-model meta-llama/Llama-3.2-3B-Instruct \
  --lora-path data/models/tmsh-lora \
  --output-dir data/models/tmsh-merged \
  --dtype float16
```

Tips

- If you see CUDA OOM during training, first drop `--max-seq-length` (192/128). The trainer logs the expected updates and effective dataloader length so you can avoid “instant” runs.
- Evaluation loads only one large model into VRAM at a time (generation → free → judge). Keep `--judge-on-cpu` disabled for speed unless VRAM is extremely tight.

`requirements.txt` already covers the core ML stack. Add the following packages to enable the seeded scripts fully:

- `fastapi==0.115.5` (inference REST server)
- `uvicorn[standard]==0.32.0` (ASGI runtime for the server)
- `lxml==5.3.0` (optional but recommended for faster HTML parsing during cleaning)
- `llama-cpp-python==0.2.90` (optional helper to install alongside `llama.cpp` conversions; only needed if you want an in-Python GGUF exporter)

After updating, run `pip install -r requirements.txt` (or `pip install` for the new extras).

---

## 7. Kick-off Checklist

1. **Install extras** – add the missing packages above.
2. **Authenticate** – confirm `huggingface-cli login` and `gh auth status` (already configured).
3. **Seed crawl** – `python -m tmsh.cli scrape`.
4. **Run preprocessing** – clean, chunk, generate, grade, and split using the commands in Section 2.
5. **Train & evaluate** – kick off `train`, then `evaluate` and inspect `data/models/eval_metrics.json` to ensure accuracy meets your bar.
6. **Merge/export** – after a successful run, merge adapters and export to GGUF for CPU inference.
7. **Serve** – launch the FastAPI server and exercise `POST /generate` with sample prompts.

Iterate by increasing `generate --max-records` and repeating grading/training when new documentation is added or model quality needs a boost.
# tmsh
