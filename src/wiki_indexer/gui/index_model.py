"""Table model for the index view; virtualised so tens of thousands of terms stay responsive."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, QSortFilterProxyModel, Qt

from wiki_indexer.indexing.index_builder import EntryKind, IndexEntry

_Index = QModelIndex | QPersistentModelIndex


class IndexTableModel(QAbstractTableModel):
    HEADERS = ("Term", "Type", "Pages", "Occurrences")
    EntryRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[IndexEntry] = []

    def set_entries(self, entries: list[IndexEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008 - Qt signature
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        if role == self.EntryRole:
            return entry
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                entry.term,
                "Custom keyword" if entry.kind is EntryKind.KEYWORD else "Term",
                len(entry.occurrences),
                entry.total_occurrences,
            )[column]
        if role == Qt.ItemDataRole.UserRole:  # sort key
            return (entry.term.casefold(), entry.kind.value, len(entry.occurrences), entry.total_occurrences)[column]
        if role == Qt.ItemDataRole.TextAlignmentRole and column >= 2:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class IndexFilterProxy(QSortFilterProxyModel):
    """Filters by substring and entry kind."""

    def __init__(self) -> None:
        super().__init__()
        self._text = ""
        self._kind: EntryKind | None = None
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def set_filter(self, text: str, kind: EntryKind | str | None) -> None:
        self.beginFilterChange()
        self._text = text.casefold().strip()
        self._kind = EntryKind(kind) if kind else None  # Qt may hand the enum back as a plain str
        self.endFilterChange()

    def filterAcceptsRow(self, source_row: int, source_parent: _Index) -> bool:
        model = self.sourceModel()
        entry: IndexEntry = model.data(model.index(source_row, 0, source_parent), IndexTableModel.EntryRole)
        if self._kind is not None and entry.kind is not self._kind:
            return False
        return not self._text or self._text in entry.term.casefold()
