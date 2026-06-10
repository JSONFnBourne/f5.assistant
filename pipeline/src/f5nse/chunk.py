from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import tyro
from transformers import AutoTokenizer

LOGGER = logging.getLogger("f5nse.chunk")


def iter_clean_documents(clean_dir: Path):
    for path in sorted(clean_dir.glob("*.json")):
        yields = json.loads(path.read_text())
        yield path, yields


@dataclass
class ChunkArgs:
    clean_dir: Path = Path("data/clean")
    output_path: Path = Path("data/chunks/chunks.jsonl")
    tokenizer_name: str = "meta-llama/Llama-3.1-8B-Instruct"
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    min_chunk_chars: int = 120
    max_chunks: int | None = None
    trust_remote_code: bool = False
    overwrite: bool = False


def chunk_text(
    text: str,
    tokenizer,
    chunk_size: int,
    overlap: int,
    min_chars: int,
):
    token_ids = tokenizer.encode(text, add_special_tokens=False)
    step = max(1, chunk_size - overlap)
    for start in range(0, len(token_ids), step):
        end = start + chunk_size
        chunk_ids = token_ids[start:end]
        if not chunk_ids:
            continue
        chunk_text = tokenizer.decode(chunk_ids)
        if len(chunk_text) < min_chars:
            continue
        yield start, end, chunk_text


def run_chunk(args: ChunkArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.output_path.exists() and not args.overwrite:
        LOGGER.info(
            "Chunk output %s exists. Use --overwrite to regenerate or remove the file.",
            args.output_path,
        )
        return
    tokenizer = AutoTokenizer.from_pretrained(
        args.tokenizer_name, trust_remote_code=args.trust_remote_code, use_fast=True
    )
    written = 0
    with args.output_path.open("w", encoding="utf-8") as fh:
        for _, record in iter_clean_documents(args.clean_dir):
            for start, end, chunk in chunk_text(
                record["text"],
                tokenizer,
                args.chunk_size_tokens,
                args.chunk_overlap_tokens,
                args.min_chunk_chars,
            ):
                out_record = {
                    "url": record["url"],
                    "source_root": record.get("source_root"),
                    "retrieved_at": record.get("retrieved_at"),
                    "title": record.get("title"),
                    "chunk": chunk,
                    "token_start": start,
                    "token_end": end,
                }
                fh.write(json.dumps(out_record, ensure_ascii=False) + "\n")
                written += 1
                if args.max_chunks and written >= args.max_chunks:
                    LOGGER.info("Reached max chunks=%s", args.max_chunks)
                    LOGGER.info("Chunked %s entries into %s", written, args.output_path)
                    return
    LOGGER.info("Chunked %s entries into %s", written, args.output_path)


def main() -> None:
    args = tyro.cli(ChunkArgs)
    run_chunk(args)


if __name__ == "__main__":
    main()
