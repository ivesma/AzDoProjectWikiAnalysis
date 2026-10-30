"""Convert Azure DevOps wiki markdown into clean plain text for indexing.

Parsing uses ``markdown-it-py`` (CommonMark + tables + strikethrough) so that
syntax is removed structurally rather than with fragile regexes:

* headings, emphasis, lists, block quotes → their text
* tables → one line per row, cells separated by spaces
* links → link text; images → alt text
* fenced / indented code → kept verbatim or dropped (configurable)
* embedded HTML → tag-stripped text (``<script>``/``<style>`` dropped)
* ADO extensions: ``[[_TOC_]]``, ``[[_TOSP_]]``, ``::: mermaid`` containers,
  ``@<GUID>`` mentions and YAML front matter delimiters are removed
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token

_ADO_MACROS = re.compile(r"\[\[_(?:TOC|TOSP)_\]\]", re.IGNORECASE)
_ADO_CONTAINER_FENCE = re.compile(r"^\s*:::.*$", re.MULTILINE)
_ADO_MENTION = re.compile(r"@<[0-9A-Fa-f-]{36}>")
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")

_BLOCK_END_TOKENS = frozenset(
    {
        "paragraph_close",
        "heading_close",
        "list_item_close",
        "blockquote_close",
        "tr_close",
        "hr",
        "table_close",
        "bullet_list_close",
        "ordered_list_close",
    }
)
_CELL_END_TOKENS = frozenset({"th_close", "td_close"})


class _HtmlTextParser(HTMLParser):
    _SKIP = frozenset({"script", "style"})
    _BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "pre"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(f" {alt} ")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def strip_html(html: str) -> str:
    parser = _HtmlTextParser()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def _preprocess(markdown: str) -> str:
    markdown = markdown.replace("\r\n", "\n")
    markdown = _FRONT_MATTER.sub(lambda m: m.group(1) + "\n\n", markdown)
    markdown = _ADO_MACROS.sub("", markdown)
    markdown = _ADO_CONTAINER_FENCE.sub("", markdown)
    return _ADO_MENTION.sub("", markdown)


def _normalize(text: str) -> str:
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


class MarkdownTextExtractor:
    def __init__(self, include_code_blocks: bool = True) -> None:
        self.include_code_blocks = include_code_blocks
        self._md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

    def extract(self, markdown: str) -> str:
        if not markdown:
            return ""
        parts: list[str] = []
        for token in self._md.parse(_preprocess(markdown)):
            if token.type == "inline":
                parts.append(self._inline_text(token.children or []))
            elif token.type in ("fence", "code_block"):
                if self.include_code_blocks:
                    parts.append(token.content)
                parts.append("\n")
            elif token.type == "html_block":
                parts.append(strip_html(token.content))
            elif token.type in _CELL_END_TOKENS:
                parts.append(" ")
            elif token.type in _BLOCK_END_TOKENS and not token.hidden:  # tight-list paragraphs are hidden
                parts.append("\n")
        return _normalize("".join(parts))

    def _inline_text(self, children: list[Token]) -> str:
        parts: list[str] = []
        for child in children:
            if child.type in ("text", "code_inline"):
                parts.append(child.content)
            elif child.type == "image":
                parts.append(child.content)  # alt text
            elif child.type in ("softbreak", "hardbreak"):
                parts.append("\n")
            elif child.type == "html_inline":
                parts.append(strip_html(child.content))
        return "".join(parts)
