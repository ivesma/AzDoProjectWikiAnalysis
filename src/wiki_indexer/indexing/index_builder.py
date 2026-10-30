"""Build the term → pages index from crawled wiki pages."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from wiki_indexer.ado.models import CrawlResult
from wiki_indexer.indexing.analyzers import TextAnalyzer
from wiki_indexer.indexing.keyword_matcher import KeywordMatcher
from wiki_indexer.utils.cancellation import CancellationToken
from wiki_indexer.utils.progress import Progress, ProgressCallback, null_progress

log = logging.getLogger(__name__)


class EntryKind(StrEnum):
    KEYWORD = "keyword"
    TERM = "term"


@dataclass(slots=True)
class IndexEntry:
    term: str
    kind: EntryKind
    occurrences: dict[str, int] = field(default_factory=dict)  # location -> count

    @property
    def locations(self) -> list[str]:
        return sorted(self.occurrences, key=str.casefold)

    @property
    def total_occurrences(self) -> int:
        return sum(self.occurrences.values())


@dataclass(slots=True)
class WikiIndex:
    entries: dict[str, IndexEntry] = field(default_factory=dict)
    # Normalised term -> {location: term frequency}; used by the search engine.
    postings: dict[str, dict[str, int]] = field(default_factory=dict)
    # Every surface form seen -> normalised term; used for fuzzy search.
    surfaces: dict[str, str] = field(default_factory=dict)
    analyzer_name: str = ""

    def sorted_entries(self) -> list[IndexEntry]:
        return sorted(self.entries.values(), key=lambda e: (e.term.casefold(), e.kind))

    def keywords(self) -> list[IndexEntry]:
        return [e for e in self.sorted_entries() if e.kind is EntryKind.KEYWORD]

    def terms(self) -> list[IndexEntry]:
        return [e for e in self.sorted_entries() if e.kind is EntryKind.TERM]

    def to_json_dict(self) -> dict[str, list[str]]:
        return {e.term: e.locations for e in self.sorted_entries()}


def page_document(title: str, text: str) -> str:
    """Text indexed for a page; the title is included so it is searchable too."""
    return f"{title}\n{text}"


class IndexBuilder:
    def __init__(
        self,
        analyzer: TextAnalyzer | None,
        keywords: list[str] | None = None,
        *,
        min_document_frequency: int = 1,
    ) -> None:
        self._analyzer = analyzer
        self._matcher = KeywordMatcher(keywords or [])
        self._min_df = max(1, min_document_frequency)

    def build(
        self,
        crawl: CrawlResult,
        token: CancellationToken | None = None,
        progress: ProgressCallback = null_progress,
    ) -> WikiIndex:
        token = token or CancellationToken()
        index = WikiIndex(analyzer_name=self._analyzer.name if self._analyzer else "")
        keyword_hits: dict[str, dict[str, int]] = defaultdict(dict)
        surface_counts: dict[str, Counter[str]] = defaultdict(Counter)
        pages = list(crawl.all_pages())

        for done, page in enumerate(pages, start=1):
            if done % 50 == 0:
                token.raise_if_cancelled()
                progress(Progress("Indexing", done, len(pages)))
            location = crawl.location(page)
            document = page_document(page.title, page.text)

            for keyword, count in self._matcher.count(document).items():
                keyword_hits[keyword][location] = count

            if self._analyzer is not None:
                for tok in self._analyzer.analyze(document):
                    postings = index.postings.setdefault(tok.term, {})
                    postings[location] = postings.get(location, 0) + 1
                    surface_counts[tok.term][tok.surface] += 1
        progress(Progress("Indexing", len(pages), len(pages)))

        for keyword in self._matcher.keywords:
            index.entries[keyword] = IndexEntry(keyword, EntryKind.KEYWORD, keyword_hits.get(keyword, {}))

        keyword_keys = {k.casefold() for k in self._matcher.keywords}
        for term, postings in index.postings.items():
            surfaces = surface_counts[term]
            for surface in surfaces:
                index.surfaces[surface] = term
            if len(postings) < self._min_df:
                continue
            display = surfaces.most_common(1)[0][0]
            if display.casefold() in keyword_keys or display in index.entries:
                continue
            index.entries[display] = IndexEntry(display, EntryKind.TERM, dict(postings))

        log.info(
            "Index built",
            extra={
                "pages": len(pages),
                "keywords": len(self._matcher.keywords),
                "terms": len(index.entries) - len(self._matcher.keywords),
                "analyzer": index.analyzer_name,
            },
        )
        return index
