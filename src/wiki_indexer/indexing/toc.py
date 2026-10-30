"""Hierarchical table of contents built from the crawled page trees."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from wiki_indexer.ado.models import CrawlResult, WikiPage


@dataclass(slots=True)
class TocNode:
    title: str
    path: str
    wiki: str
    children: list["TocNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "path": self.path,
            "wiki": self.wiki,
            "children": [c.to_dict() for c in self.children],
        }


# Maps a node to a link target (e.g. an exported markdown file); ``None`` renders plain text.
LinkResolver = Callable[[TocNode], str | None]


def _from_page(page: WikiPage) -> TocNode:
    return TocNode(page.title, page.path, page.wiki_name, [_from_page(c) for c in page.children])


def build_toc(crawl: CrawlResult) -> list[TocNode]:
    """One node per top-level page; with several wikis, one root node per wiki."""
    if not crawl.is_multi_wiki:
        return [_from_page(p) for tree in crawl.trees for p in tree.pages]
    return [
        TocNode(tree.wiki.name, "/", tree.wiki.name, [_from_page(p) for p in tree.pages]) for tree in crawl.trees
    ]


def render_markdown(nodes: list[TocNode], link: LinkResolver | None = None) -> str:
    lines = ["# Table of Contents", ""]

    def visit(node: TocNode, depth: int) -> None:
        target = link(node) if link else None
        label = f"[{_escape(node.title)}]({target})" if target else node.title
        lines.append(f"{'  ' * depth}- {label}")
        for child in node.children:
            visit(child, depth + 1)

    for node in nodes:
        visit(node, 0)
    return "\n".join(lines) + "\n"


def render_tree(nodes: list[TocNode]) -> str:
    """Box-drawing tree, e.g. for the summary or the console."""
    lines: list[str] = []

    def visit(node: TocNode, prefix: str, last: bool, root: bool) -> None:
        lines.append(node.title if root else f"{prefix}{'└── ' if last else '├── '}{node.title}")
        child_prefix = "" if root else prefix + ("    " if last else "│   ")
        for i, child in enumerate(node.children):
            visit(child, child_prefix, i == len(node.children) - 1, False)

    for node in nodes:
        visit(node, "", True, True)
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("[", r"\[").replace("]", r"\]")
