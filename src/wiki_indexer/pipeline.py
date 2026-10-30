"""End-to-end orchestration: discover → crawl → extract text → index → TOC → export."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from wiki_indexer.ado.models import CrawlResult, WikiInfo
from wiki_indexer.ado.wiki_service import WikiCrawler, WikiService
from wiki_indexer.config.settings import AppSettings
from wiki_indexer.exporters import CsvExporter, ExportContext, Exporter, ExportService, JsonExporter, MarkdownExporter
from wiki_indexer.indexing.analyzers import TextAnalyzer, create_analyzer
from wiki_indexer.indexing.index_builder import IndexBuilder, WikiIndex
from wiki_indexer.indexing.markdown_text import MarkdownTextExtractor
from wiki_indexer.indexing.search import SearchEngine
from wiki_indexer.indexing.toc import TocNode, build_toc
from wiki_indexer.utils.cancellation import CancellationToken
from wiki_indexer.utils.progress import Progress, ProgressCallback, null_progress

log = logging.getLogger(__name__)

AnalyzerFactory = Callable[[str, int], TextAnalyzer]


@dataclass(slots=True)
class PipelineResult:
    crawl: CrawlResult
    index: WikiIndex
    toc: list[TocNode]
    analyzer: TextAnalyzer | None
    output_dir: Path
    exported_files: list[Path] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def search_engine(self) -> SearchEngine:
        return SearchEngine(self.crawl, self.index, self.analyzer)


def build_exporters(settings: AppSettings) -> list[Exporter]:
    exporters: list[Exporter] = []
    if settings.export_markdown:
        exporters.append(MarkdownExporter())
    if settings.export_json:
        exporters.append(JsonExporter())
    if settings.export_csv:
        exporters.append(CsvExporter())
    return exporters


class IndexingPipeline:
    def __init__(
        self,
        service: WikiService,
        settings: AppSettings,
        keywords: list[str],
        output_dir: Path,
        *,
        organization: str = "",
        analyzer_factory: AnalyzerFactory = create_analyzer,
        exporters: list[Exporter] | None = None,
    ) -> None:
        self._service = service
        self._settings = settings.validated()
        self._keywords = keywords if self._settings.index_custom_keywords else []
        self._output_dir = output_dir
        self._organization = organization
        self._analyzer_factory = analyzer_factory
        self._exporters = build_exporters(self._settings) if exporters is None else exporters

    def run(
        self,
        wikis: list[WikiInfo] | None = None,
        token: CancellationToken | None = None,
        progress: ProgressCallback = null_progress,
    ) -> PipelineResult:
        token = token or CancellationToken()
        started = time.perf_counter()
        s = self._settings

        if wikis is None:
            progress(Progress("Discovering wikis", 0, 1))
            wikis = self._service.list_wikis()

        crawler = WikiCrawler(self._service, thread_count=s.thread_count, fetch_last_modified=s.fetch_last_modified)
        crawl = crawler.crawl(wikis, token, progress)

        extractor = MarkdownTextExtractor(include_code_blocks=s.include_code_blocks)
        pages = list(crawl.all_pages())
        for n, page in enumerate(pages, start=1):
            page.text = extractor.extract(page.content)
            if n % 100 == 0:
                token.raise_if_cancelled()
                progress(Progress("Processing content", n, len(pages)))

        analyzer = self._analyzer_factory(s.analyzer, s.min_term_length) if s.index_standard_terms else None
        index = IndexBuilder(
            analyzer, self._keywords, min_document_frequency=s.min_term_document_frequency
        ).build(crawl, token, progress)
        toc = build_toc(crawl)

        result = PipelineResult(crawl=crawl, index=index, toc=toc, analyzer=analyzer, output_dir=self._output_dir)
        if self._exporters:
            progress(Progress("Exporting", 0, 1))
            context = ExportContext(
                crawl=crawl,
                index=index,
                toc=toc,
                output_dir=self._output_dir,
                organization=self._organization,
                project=self._service.project,
            )
            result.exported_files = ExportService(self._exporters).export(context, token)
        result.elapsed_seconds = round(time.perf_counter() - started, 2)
        progress(Progress("Done", 1, 1, f"{crawl.page_count} pages in {result.elapsed_seconds}s"))
        log.info(
            "Pipeline complete",
            extra={"pages": crawl.page_count, "files": len(result.exported_files), "seconds": result.elapsed_seconds},
        )
        return result
