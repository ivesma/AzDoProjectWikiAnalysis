"""Exporter contract and the service that runs the configured exporters."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from wiki_indexer.ado.models import CrawlResult
from wiki_indexer.indexing.index_builder import WikiIndex
from wiki_indexer.indexing.toc import TocNode
from wiki_indexer.utils.cancellation import CancellationToken

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ExportContext:
    crawl: CrawlResult
    index: WikiIndex
    toc: list[TocNode]
    output_dir: Path
    organization: str = ""
    project: str = ""
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def generated_at_text(self) -> str:
        return self.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")


class Exporter(Protocol):
    name: str

    def export(self, context: ExportContext) -> list[Path]: ...


class ExportService:
    def __init__(self, exporters: list[Exporter]) -> None:
        self._exporters = exporters

    @property
    def exporter_names(self) -> list[str]:
        return [e.name for e in self._exporters]

    def export(self, context: ExportContext, token: CancellationToken | None = None) -> list[Path]:
        context.output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for exporter in self._exporters:
            if token:
                token.raise_if_cancelled()
            files = exporter.export(context)
            written.extend(files)
            log.info(
                "Export complete",
                extra={"exporter": exporter.name, "files": len(files), "outputDir": str(context.output_dir)},
            )
        return written
