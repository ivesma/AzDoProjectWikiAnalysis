"""Application settings persisted as camelCase JSON (``appsettings.json``)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ANALYZERS = ("auto", "spacy", "nltk", "basic")


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


@dataclass(slots=True)
class AppSettings:
    # Export
    export_markdown: bool = True
    export_json: bool = True
    export_csv: bool = True
    output_directory: str = ""  # empty = platform default
    # Crawling
    thread_count: int = 10
    request_timeout_seconds: int = 30
    max_retries: int = 5
    fetch_last_modified: bool = True
    # Indexing
    index_custom_keywords: bool = True
    index_standard_terms: bool = True
    analyzer: str = "auto"
    include_code_blocks: bool = True
    min_term_length: int = 3
    min_term_document_frequency: int = 1
    # Last-used connection (never the PAT)
    organization_url: str = ""
    project: str = ""
    remember_pat: bool = True

    def validated(self) -> "AppSettings":
        """Return a copy with values clamped to sane ranges."""
        return replace(
            self,
            thread_count=max(1, min(int(self.thread_count), 64)),
            request_timeout_seconds=max(5, min(int(self.request_timeout_seconds), 300)),
            max_retries=max(0, min(int(self.max_retries), 10)),
            min_term_length=max(1, min(int(self.min_term_length), 20)),
            min_term_document_frequency=max(1, int(self.min_term_document_frequency)),
            analyzer=self.analyzer if self.analyzer in ANALYZERS else "auto",
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {_to_camel(k): v for k, v in asdict(self).items()}

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "AppSettings":
        known = {f.name: f for f in fields(cls)}
        values: dict[str, Any] = {}
        for key, value in data.items():
            name = _to_snake(key)
            if name not in known:
                log.warning("Ignoring unknown setting", extra={"setting": key})
                continue
            default = known[name].default
            if isinstance(default, bool):
                values[name] = value.strip().lower() in ("true", "1", "yes") if isinstance(value, str) else bool(value)
            elif isinstance(default, int):
                values[name] = int(value)
            else:
                values[name] = str(value)
        return cls(**values).validated()


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read settings; using defaults", extra={"path": str(self.path)})
            return AppSettings()
        return AppSettings.from_json_dict(data)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings.validated().to_json_dict(), indent=2) + "\n", encoding="utf-8")
        log.info("Settings saved", extra={"path": str(self.path)})
