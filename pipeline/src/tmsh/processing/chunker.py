"""Utilities for chunking cleaned documentation into model-ready segments."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass


@dataclass
class Chunk:
    """A slice of cleaned text with optional metadata."""

    text: str
    source: str
    start: int
    end: int


def sliding_window_chunk(
    text: str,
    *,
    window_size: int = 1200,
    overlap: int = 200,
    source: str = "",
) -> list[Chunk]:
    """Chunk ``text`` using a simple character based sliding window."""

    chunks: list[Chunk] = []
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    if overlap >= window_size:
        raise ValueError("overlap must be smaller than window_size")

    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + window_size, text_length)
        chunks.append(Chunk(text=text[start:end], source=source, start=start, end=end))
        if end == text_length:
            break
        start = end - overlap
    return chunks


def batch_chunks(chunks: Sequence[Chunk], batch_size: int) -> Iterator[Sequence[Chunk]]:
    """Yield fixed size batches of chunks."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    for index in range(0, len(chunks), batch_size):
        yield chunks[index : index + batch_size]
