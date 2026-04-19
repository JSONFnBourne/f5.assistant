"""Utility helpers for tmsh project."""
from __future__ import annotations

from pathlib import Path

from .config import DirectoryLayout


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_directory_layout() -> DirectoryLayout:
    root = get_project_root()
    return DirectoryLayout.from_project_root(root)
