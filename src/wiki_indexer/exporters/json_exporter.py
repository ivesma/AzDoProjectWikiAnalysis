"""JSON export: wiki.json, toc.json, index.json and wikis.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wiki_indexer.exporters.base import ExportContext


class JsonExporter:
    name = "json"

    def __init__(self, include_content: bool = True) -> None:
        self.include_content = include_content

    def export(self, context: ExportContext) -> list[Path]:
        out = context.output_dir
        header = {
            "generatedAt": context.generated_at.isoformat(),
            "organization": context.organization,
            "project": context.project,
        }
        wiki = {
            **header,
            "wikis": [
                {
                    **tree.wiki.to_dict(),
                    "pages": [p.to_dict(include_content=self.include_content) for p in tree.pages],
                }
                for tree in context.crawl.trees
            ],
        }
        return [
            self._write(out / "wiki.json", wiki),
            self._write(out / "toc.json", {**header, "toc": [n.to_dict() for n in context.toc]}),
            self._write(out / "index.json", context.index.to_json_dict()),
            self._write(out / "wikis.json", {**header, "wikis": [t.wiki.to_dict() for t in context.crawl.trees]}),
        ]

    @staticmethod
    def _write(path: Path, data: Any) -> Path:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
