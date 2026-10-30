"""Markdown export: every page (hierarchy preserved), toc.md, index.md and summary.md.

Page files mirror the Azure DevOps wiki repository layout: a page ``/A/B`` is
written to ``wiki/A/B.md`` and its children live in ``wiki/A/B/``.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from wiki_indexer.ado.models import CrawlResult, WikiPage
from wiki_indexer.exporters.base import ExportContext
from wiki_indexer.indexing.index_builder import IndexEntry, WikiIndex
from wiki_indexer.indexing.toc import TocNode, render_markdown, render_tree
from wiki_indexer.utils.filenames import safe_segment, wiki_path_segments

WIKI_DIR = "wiki"
OWNERSHIP_MARKER = ".wiki-indexer"


def page_file_map(crawl: CrawlResult) -> dict[str, PurePosixPath]:
    """Map each page location to a unique relative ``.md`` path (case-insensitive unique on Windows)."""
    mapping: dict[str, PurePosixPath] = {}
    used: set[str] = set()
    for tree in crawl.trees:
        prefix = [safe_segment(tree.wiki.name)] if crawl.is_multi_wiki else []
        for page in tree.walk():
            segments = prefix + wiki_path_segments(page.path)
            if not segments:
                segments = prefix + ["_root"]
            candidate = PurePosixPath(WIKI_DIR, *segments).with_suffix(".md") if segments else None
            assert candidate is not None
            stem, n = candidate.with_suffix(""), 1
            while str(candidate).casefold() in used:
                n += 1
                candidate = PurePosixPath(f"{stem}~{n}.md")
            used.add(str(candidate).casefold())
            mapping[crawl.location(page)] = candidate
    return mapping


def md_link(path: PurePosixPath) -> str:
    return quote(str(path), safe="/~")


class MarkdownExporter:
    name = "markdown"

    def export(self, context: ExportContext) -> list[Path]:
        out = context.output_dir
        files = page_file_map(context.crawl)
        self._reset_wiki_dir(out)

        written: list[Path] = []
        for page in context.crawl.all_pages():
            rel = files[context.crawl.location(page)]
            target = out / Path(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page.content, encoding="utf-8")
            written.append(target)

        location_of = {(p.wiki_name, p.path): context.crawl.location(p) for p in context.crawl.all_pages()}

        def toc_link(node: TocNode) -> str | None:
            location = location_of.get((node.wiki, node.path))
            return md_link(files[location]) if location in files else None

        written.append(self._write(out / "toc.md", render_markdown(context.toc, toc_link)))
        written.append(self._write(out / "index.md", render_index(context.index, files)))
        written.append(self._write(out / "summary.md", render_summary(context)))
        return written

    @staticmethod
    def _reset_wiki_dir(out: Path) -> None:
        """Remove a previous export's page tree so deleted pages disappear—only if we created it."""
        marker = out / OWNERSHIP_MARKER
        wiki_dir = out / WIKI_DIR
        if wiki_dir.exists() and marker.exists():
            shutil.rmtree(wiki_dir)
        out.mkdir(parents=True, exist_ok=True)
        marker.write_text("Created by Azure DevOps Wiki Indexer. The wiki/ folder is regenerated on export.\n")

    @staticmethod
    def _write(path: Path, text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        return path


def _location_label(location: str) -> str:
    return location.lstrip("/")


def render_index(index: WikiIndex, files: dict[str, PurePosixPath] | None = None) -> str:
    lines = ["# Index", ""]

    def entry_lines(entry: IndexEntry) -> None:
        lines.append(f"## {entry.term}")
        if not entry.occurrences:
            lines.append("_No occurrences._")
        for location in entry.locations:
            label = _location_label(location)
            if files and location in files:
                lines.append(f"- [{label}]({md_link(files[location])})")
            else:
                lines.append(f"- {label}")
        lines.append("")

    for entry in index.sorted_entries():
        entry_lines(entry)
    return "\n".join(lines).rstrip() + "\n"


def render_summary(context: ExportContext) -> str:
    crawl, index = context.crawl, context.index
    failed: list[WikiPage] = crawl.failed_pages
    lines = [
        "# Wiki Summary",
        "",
        f"- **Generated:** {context.generated_at_text}",
        f"- **Organisation:** {context.organization}",
        f"- **Project:** {context.project}",
        f"- **Wikis:** {len(crawl.trees)}",
        f"- **Pages:** {crawl.page_count}",
        f"- **Pages that failed to load:** {len(failed)}",
        f"- **Custom keywords:** {len(index.keywords())}",
        f"- **Index terms:** {len(index.terms())}",
        f"- **Analyzer:** {index.analyzer_name or 'disabled'}",
        "",
        "## Wikis",
        "",
        "| Name | Type | Repository | Root Path | Pages |",
        "|---|---|---|---|---|",
    ]
    for tree in crawl.trees:
        w = tree.wiki
        lines.append(
            f"| {w.name} | {w.display_type} | {w.repository_name or w.repository_id} | {w.mapped_path} "
            f"| {sum(1 for _ in tree.walk())} |"
        )

    keyword_rows = [e for e in index.keywords()]
    if keyword_rows:
        lines += ["", "## Custom Keywords", "", "| Keyword | Pages | Occurrences |", "|---|---|---|"]
        for e in keyword_rows:
            lines.append(f"| {e.term} | {len(e.occurrences)} | {e.total_occurrences} |")

    top_terms = sorted(index.terms(), key=lambda e: (-len(e.occurrences), e.term))[:25]
    if top_terms:
        lines += ["", "## Most Widespread Terms", "", "| Term | Pages |", "|---|---|"]
        lines += [f"| {e.term} | {len(e.occurrences)} |" for e in top_terms]

    if failed:
        lines += ["", "## Pages That Failed To Load", ""]
        lines += [f"- {crawl.location(p)}: {p.error}" for p in failed]

    lines += ["", "## Structure", "", "```text", render_tree(context.toc), "```", ""]
    return "\n".join(lines)
