"""CSV export: page_inventory.csv and keyword_index.csv (UTF-8 with BOM for Excel)."""

from __future__ import annotations

import csv
from pathlib import Path

from wiki_indexer.exporters.base import ExportContext


class CsvExporter:
    name = "csv"

    def export(self, context: ExportContext) -> list[Path]:
        return [self._inventory(context), self._keyword_index(context)]

    @staticmethod
    def _inventory(context: ExportContext) -> Path:
        path = context.output_dir / "page_inventory.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["Wiki", "Path", "Title", "PageId", "Depth", "ChildPages", "WordCount",
                 "LastModifiedBy", "LastModifiedDate", "Error"]
            )
            for page in context.crawl.all_pages():
                writer.writerow(
                    [
                        page.wiki_name,
                        page.path,
                        page.title,
                        "" if page.id is None else page.id,
                        page.depth,
                        len(page.children),
                        len(page.text.split()),
                        page.last_modified_by,
                        page.last_modified_date,
                        page.error,
                    ]
                )
        return path

    @staticmethod
    def _keyword_index(context: ExportContext) -> Path:
        path = context.output_dir / "keyword_index.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Term", "Type", "Location", "Occurrences"])
            for entry in context.index.sorted_entries():
                for location in entry.locations:
                    writer.writerow([entry.term, entry.kind.value, location, entry.occurrences[location]])
        return path
