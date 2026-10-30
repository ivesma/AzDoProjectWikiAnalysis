"""Helpers for turning wiki page paths into safe Windows file system paths."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_SEGMENT_LENGTH = 120


def safe_segment(name: str) -> str:
    """Make a single path segment valid on Windows."""
    cleaned = _INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:MAX_SEGMENT_LENGTH]


def wiki_path_segments(wiki_path: str) -> list[str]:
    """Split ``/Architecture/Components`` into safe segments."""
    return [safe_segment(part) for part in PurePosixPath(wiki_path).parts if part not in ("/", "")]
