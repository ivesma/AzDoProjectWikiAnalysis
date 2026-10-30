"""Custom keyword list persisted as ``{"keywords": [...]}`` (``keywords.json``)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def normalize_keywords(keywords: list[str]) -> list[str]:
    """Trim, drop blanks and remove case-insensitive duplicates, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for keyword in keywords:
        cleaned = " ".join(str(keyword).split())
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            result.append(cleaned)
    return result


class KeywordStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read keywords", extra={"path": str(self.path)})
            return []
        return normalize_keywords(list(data.get("keywords", [])))

    def save(self, keywords: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"keywords": normalize_keywords(keywords)}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        log.info("Keywords saved", extra={"path": str(self.path), "count": len(payload["keywords"])})
