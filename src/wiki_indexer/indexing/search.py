"""Local search over crawled pages: exact phrase, keyword, regex and fuzzy."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz, process

from wiki_indexer.ado.models import CrawlResult, WikiPage
from wiki_indexer.indexing.analyzers import TextAnalyzer
from wiki_indexer.indexing.index_builder import WikiIndex, page_document

SNIPPET_RADIUS = 80


class SearchMode(StrEnum):
    EXACT = "exact"
    KEYWORD = "keyword"
    REGEX = "regex"
    FUZZY = "fuzzy"


@dataclass(frozen=True, slots=True)
class SearchHit:
    location: str
    wiki: str
    path: str
    title: str
    score: float
    snippet: str


@dataclass(slots=True)
class _Document:
    page: WikiPage
    location: str
    text: str
    folded: str


class SearchEngine:
    def __init__(
        self,
        crawl: CrawlResult,
        index: WikiIndex,
        analyzer: TextAnalyzer | None,
        *,
        fuzzy_cutoff: float = 80.0,
    ) -> None:
        self._index = index
        self._analyzer = analyzer
        self._fuzzy_cutoff = fuzzy_cutoff
        self._docs: dict[str, _Document] = {}
        for page in crawl.all_pages():
            text = page_document(page.title, page.text)
            location = crawl.location(page)
            self._docs[location] = _Document(page, location, text, text.casefold())

    @property
    def document_count(self) -> int:
        return len(self._docs)

    def search(self, query: str, mode: SearchMode | str = SearchMode.KEYWORD, limit: int = 500) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        mode = SearchMode(mode)
        if mode is SearchMode.EXACT:
            hits = self._exact(query)
        elif mode is SearchMode.REGEX:
            hits = self._regex(query)
        elif mode is SearchMode.FUZZY:
            hits = self._fuzzy(query)
        else:
            hits = self._keyword(query)
        hits.sort(key=lambda h: (-h.score, h.location.casefold()))
        return hits[:limit]

    # -- modes ---------------------------------------------------------------------------------

    def _exact(self, phrase: str) -> list[SearchHit]:
        needle = phrase.casefold()
        hits = []
        for doc in self._docs.values():
            count = doc.folded.count(needle)
            if count:
                start = doc.folded.index(needle)
                hits.append(self._hit(doc, count, start, start + len(needle)))
        return hits

    def _regex(self, expression: str) -> list[SearchHit]:
        try:
            pattern = re.compile(expression, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc
        hits = []
        for doc in self._docs.values():
            matches = list(pattern.finditer(doc.text))
            if matches:
                first = matches[0]
                hits.append(self._hit(doc, len(matches), first.start(), first.end()))
        return hits

    def _keyword(self, query: str) -> list[SearchHit]:
        # A configured custom keyword is answered straight from the keyword index.
        entry = next(
            (e for e in self._index.keywords() if e.term.casefold() == " ".join(query.split()).casefold()), None
        )
        if entry is not None:
            return [self._locate(self._docs[loc], query, count) for loc, count in entry.occurrences.items()]

        terms = {t.term for t in self._analyzer.analyze(query)} if self._analyzer else set()
        if not terms:
            return self._exact(query)  # only stop words, or no analyser: fall back to phrase search
        matching: set[str] | None = None
        for term in terms:
            locations = set(self._index.postings.get(term, {}))
            matching = locations if matching is None else matching & locations
        hits = []
        total = max(1, len(self._docs))
        surface = self._first_surface(terms)
        for location in matching or set():
            score = sum(
                self._index.postings[t][location] * math.log(1 + total / len(self._index.postings[t])) for t in terms
            )
            hits.append(self._locate(self._docs[location], surface or query, round(score, 3)))
        return hits

    def _fuzzy(self, query: str) -> list[SearchHit]:
        folded = query.casefold()
        scores: dict[str, float] = {}

        vocabulary = list(self._index.surfaces)
        for surface, score, _ in process.extract(
            folded, vocabulary, scorer=fuzz.ratio, score_cutoff=self._fuzzy_cutoff, limit=25
        ):
            for location in self._index.postings.get(self._index.surfaces[surface], {}):
                scores[location] = max(scores.get(location, 0.0), score)

        for entry in self._index.keywords():
            score = fuzz.WRatio(folded, entry.term.casefold())
            if score >= self._fuzzy_cutoff:
                for location in entry.occurrences:
                    scores[location] = max(scores.get(location, 0.0), score)

        for location, doc in self._docs.items():
            score = fuzz.partial_ratio(folded, doc.page.title.casefold())
            if score >= self._fuzzy_cutoff and len(doc.page.title) >= len(query) // 2:
                scores[location] = max(scores.get(location, 0.0), score)

        return [self._locate(self._docs[loc], query, round(score, 1)) for loc, score in scores.items()]

    # -- helpers -------------------------------------------------------------------------------

    def _first_surface(self, terms: set[str]) -> str:
        for surface, term in self._index.surfaces.items():
            if term in terms:
                return surface
        return ""

    def _locate(self, doc: _Document, needle: str, score: float) -> SearchHit:
        start = doc.folded.find(needle.casefold())
        if start < 0:
            return self._hit(doc, score, 0, 0)
        return self._hit(doc, score, start, start + len(needle))

    @staticmethod
    def _hit(doc: _Document, score: float, start: int, end: int) -> SearchHit:
        return SearchHit(
            location=doc.location,
            wiki=doc.page.wiki_name,
            path=doc.page.path,
            title=doc.page.title,
            score=float(score),
            snippet=make_snippet(doc.text, start, end),
        )


def make_snippet(text: str, start: int, end: int, radius: int = SNIPPET_RADIUS) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    snippet = " ".join(text[lo:hi].split())
    return f"{'…' if lo > 0 else ''}{snippet}{'…' if hi < len(text) else ''}"


def format_results(query: str, hits: list[SearchHit]) -> str:
    lines = [f"Search Term: {query}", ""]
    if not hits:
        lines.append("No matches found.")
        return "\n".join(lines)
    lines += ["Found in:", ""]
    lines += [hit.location.lstrip("/") for hit in hits]
    return "\n".join(lines)
