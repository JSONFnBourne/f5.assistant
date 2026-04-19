#!/usr/bin/env python
"""Prepare cleaned datasets and chunked artefacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from tmsh.datasets.qa_generator import generate_dataset
from tmsh.processing.cleaning import aggregate_tmsh_and_irule_sections, clean_html_fragment
from tmsh.processing.chunker import sliding_window_chunk
from tmsh.utils import get_directory_layout


def iter_html_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.html"):
        yield path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="tmsh", choices=["tmsh", "irule"])
    parser.add_argument(
        "--output",
        default=None,
        help="Optional override for the dataset jsonl output path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    layout = get_directory_layout()
    html_paths = list(iter_html_files(layout.data_raw))
    html_fragments = [path.read_text(encoding="utf-8") for path in html_paths]

    records = aggregate_tmsh_and_irule_sections(html_fragments, domain=args.domain)
    processed_dir = layout.data_processed
    processed_dir.mkdir(parents=True, exist_ok=True)
    records_path = processed_dir / f"{args.domain}_records.jsonl"
    with records_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")

    chunks = []
    for path in html_paths:
        fragment = clean_html_fragment(path.read_text(encoding="utf-8"))
        relative_source = str(path.relative_to(layout.data_raw))
        chunks.extend(sliding_window_chunk(fragment, source=relative_source))

    chunks_path = layout.data_chunks / f"{args.domain}_chunks.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(
                json.dumps(
                    {
                        "text": chunk.text,
                        "source": chunk.source,
                        "start": chunk.start,
                        "end": chunk.end,
                    }
                )
                + "\n"
            )

    output_path = (
        Path(args.output)
        if args.output
        else layout.data_training / f"{args.domain}_qa_pairs.jsonl"
    )

    qa_pairs = generate_dataset(chunks, output_path, domain=args.domain)
    print(f"Wrote {len(records)} structured records to {records_path}")
    print(f"Wrote {len(chunks)} chunks to {chunks_path}")
    print(f"Wrote {len(qa_pairs)} QA pairs to {output_path}")


if __name__ == "__main__":
    main()
