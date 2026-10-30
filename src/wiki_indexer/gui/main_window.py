"""Main application window."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Callable

from PySide6.QtCore import QModelIndex, Qt, QThreadPool, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from wiki_indexer import APP_NAME, __version__
from wiki_indexer.ado.models import ConnectionResult, WikiInfo, WikiPage
from wiki_indexer.config.settings import AppSettings
from wiki_indexer.gui.index_model import IndexFilterProxy, IndexTableModel
from wiki_indexer.gui.log_handler import QtLogHandler
from wiki_indexer.gui.settings_panel import SettingsPanel
from wiki_indexer.gui.style import ERROR, SUCCESS
from wiki_indexer.gui.workers import Task, TaskWorker
from wiki_indexer.indexing.index_builder import EntryKind, IndexEntry
from wiki_indexer.indexing.search import SearchEngine, SearchMode
from wiki_indexer.indexing.toc import TocNode
from wiki_indexer.pipeline import PipelineResult
from wiki_indexer.services import AppServices
from wiki_indexer.utils.progress import Progress
from wiki_indexer.utils.secrets import SecretString

log = logging.getLogger(__name__)

LocationRole = Qt.ItemDataRole.UserRole

_SEARCH_MODES = [
    ("Keyword", SearchMode.KEYWORD),
    ("Exact phrase", SearchMode.EXACT),
    ("Regex", SearchMode.REGEX),
    ("Fuzzy", SearchMode.FUZZY),
]


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self._services = services
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._settings = services.settings_store.load()
        self._worker: TaskWorker | None = None
        self._result: PipelineResult | None = None
        self._search: SearchEngine | None = None
        self._pages: dict[str, WikiPage] = {}
        self._toc_items: dict[str, QTreeWidgetItem] = {}

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        if services.paths.icon_file.exists():
            self.setWindowIcon(QIcon(str(services.paths.icon_file)))
        self.resize(1200, 820)
        self._build_ui()
        self.settings_panel.load(self._settings, services.keyword_store.load())
        self.org_edit.setText(self._settings.organization_url)
        self.project_edit.setText(self._settings.project)
        self.remember_check.setChecked(self._settings.remember_pat)
        self._refresh_pat_placeholder()

        self._log_handler = QtLogHandler()
        self._log_handler.message.connect(self.log_view.appendPlainText)
        logging.getLogger().addHandler(self._log_handler)
        self._set_busy(False)

    # ------------------------------------------------------------------------------------- layout

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.addWidget(self._build_connection_box())

        self.status_label = QLabel("Enter your organisation, project and PAT, then click Test Connection.")
        self.status_label.setObjectName("status")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        progress_row = QHBoxLayout()
        self.stage_label = QLabel("Idle")
        self.stage_label.setMinimumWidth(220)
        self.progress_bar = QProgressBar()
        progress_row.addWidget(self.stage_label)
        progress_row.addWidget(self.progress_bar, 1)
        root.addLayout(progress_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_wikis_tab(), "Wikis")
        self.tabs.addTab(self._build_toc_tab(), "Table of Contents")
        self.tabs.addTab(self._build_index_tab(), "Index")
        self.tabs.addTab(self._build_search_tab(), "Search")
        self.settings_panel = SettingsPanel(str(self._services.paths.default_output_dir))
        self.settings_panel.save_requested.connect(self._save_settings)
        self.settings_panel.forget_pat_requested.connect(self._forget_pat)
        self.tabs.addTab(self.settings_panel, "Settings")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.tabs.addTab(self.log_view, "Log")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Logs: {self._services.paths.log_dir}")

    def _build_connection_box(self) -> QGroupBox:
        box = QGroupBox("Azure DevOps Connection")
        form = QFormLayout()
        self.org_edit = QLineEdit()
        self.org_edit.setPlaceholderText("https://dev.azure.com/myorg")
        self.org_edit.editingFinished.connect(self._refresh_pat_placeholder)
        self.project_edit = QLineEdit()
        self.project_edit.setPlaceholderText("MyProject")
        self.pat_edit = QLineEdit()
        self.pat_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pat_edit.setClearButtonEnabled(True)
        self._reveal_action = QAction("Show", self.pat_edit)
        self._reveal_action.setCheckable(True)
        self._reveal_action.toggled.connect(
            lambda on: self.pat_edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        self.pat_edit.addAction(self._reveal_action, QLineEdit.ActionPosition.TrailingPosition)
        self.remember_check = QCheckBox("Remember PAT in Windows Credential Manager")
        form.addRow("Organisation URL", self.org_edit)
        form.addRow("Project", self.project_edit)
        form.addRow("Personal Access Token", self.pat_edit)
        form.addRow("", self.remember_check)

        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self.test_connection)
        self.index_button = QPushButton("Index Wikis")
        self.index_button.setObjectName("primary")
        self.index_button.clicked.connect(self.run_indexing)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.open_output_button = QPushButton("Open Output Folder")
        self.open_output_button.clicked.connect(self._open_output)
        buttons = QVBoxLayout()
        for button in (self.test_button, self.index_button, self.cancel_button, self.open_output_button):
            buttons.addWidget(button)
        buttons.addStretch(1)

        layout = QHBoxLayout(box)
        layout.addLayout(form, 1)
        layout.addLayout(buttons)
        return box

    def _build_wikis_tab(self) -> QWidget:
        self.wiki_table = QTableWidget(0, 6)
        self.wiki_table.setHorizontalHeaderLabels(
            ["Wiki Name", "Wiki Type", "Repository", "Root Path", "Published Versions", "Pages"]
        )
        self.wiki_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.wiki_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.wiki_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.wiki_table.verticalHeader().setVisible(False)
        return self.wiki_table

    def _build_toc_tab(self) -> QWidget:
        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderLabels(["Page"])
        self.toc_tree.currentItemChanged.connect(self._on_toc_item_changed)
        self.page_view = QTextBrowser()
        self.page_view.setOpenExternalLinks(True)
        splitter = QSplitter()
        splitter.addWidget(self.toc_tree)
        splitter.addWidget(self.page_view)
        splitter.setSizes([380, 780])
        return splitter

    def _build_index_tab(self) -> QWidget:
        self.index_filter = QLineEdit()
        self.index_filter.setPlaceholderText("Filter terms…")
        self.index_kind = QComboBox()
        self.index_kind.addItem("All entries", None)
        self.index_kind.addItem("Custom keywords", EntryKind.KEYWORD)
        self.index_kind.addItem("Standard terms", EntryKind.TERM)
        self.index_filter.textChanged.connect(self._apply_index_filter)
        self.index_kind.currentIndexChanged.connect(self._apply_index_filter)

        self.index_model = IndexTableModel()
        self.index_proxy = IndexFilterProxy()
        self.index_proxy.setSourceModel(self.index_model)
        self.index_view = QTableView()
        self.index_view.setModel(self.index_proxy)
        self.index_view.setSortingEnabled(True)
        self.index_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.index_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.index_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.index_view.verticalHeader().setVisible(False)
        self.index_view.selectionModel().currentRowChanged.connect(self._on_index_row_changed)

        self.index_locations = QListWidget()
        self.index_locations.itemDoubleClicked.connect(self._open_location_item)

        top = QHBoxLayout()
        top.addWidget(self.index_filter, 1)
        top.addWidget(self.index_kind)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(top)
        left_layout.addWidget(self.index_view)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Found in (double-click to open):"))
        right_layout.addWidget(self.index_locations)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([700, 460])
        return splitter

    def _build_search_tab(self) -> QWidget:
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search term, phrase or regular expression")
        self.search_edit.returnPressed.connect(self.run_search)
        self.search_mode = QComboBox()
        for label, mode in _SEARCH_MODES:
            self.search_mode.addItem(label, mode)
        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self.run_search)
        self.search_summary = QLabel("Run an indexing job to enable search.")
        self.search_results = QTableWidget(0, 3)
        self.search_results.setHorizontalHeaderLabels(["Found in", "Score", "Context"])
        header = self.search_results.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.search_results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.search_results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.search_results.verticalHeader().setVisible(False)
        self.search_results.itemDoubleClicked.connect(self._open_search_item)

        row = QHBoxLayout()
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.search_mode)
        row.addWidget(self.search_button)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(row)
        layout.addWidget(self.search_summary)
        layout.addWidget(self.search_results, 1)
        return page

    # ---------------------------------------------------------------------------------- helpers

    def _current_settings(self) -> AppSettings:
        return replace(
            self.settings_panel.settings(),
            organization_url=self.org_edit.text().strip(),
            project=self.project_edit.text().strip(),
            remember_pat=self.remember_check.isChecked(),
        )

    def _refresh_pat_placeholder(self) -> None:
        stored = self._services.stored_pat(self.org_edit.text())
        has_stored = bool(stored)
        if stored:
            stored.wipe()
        self.pat_edit.setPlaceholderText(
            "Using PAT stored in Windows Credential Manager (type to replace)" if has_stored else "Required"
        )

    def _take_pat(self) -> SecretString | None:
        """Prefer a newly typed PAT; otherwise fall back to the stored one."""
        typed = self.pat_edit.text()
        if typed:
            return SecretString(typed)
        return self._services.stored_pat(self.org_edit.text())

    def _show_status(self, text: str, ok: bool | None = None) -> None:
        color = {True: SUCCESS, False: ERROR}.get(ok)  # type: ignore[arg-type]
        self.status_label.setStyleSheet(f"color: {color};" if color else "")
        self.status_label.setText(text)

    def _validate_inputs(self) -> SecretString | None:
        if not self.org_edit.text().strip() or not self.project_edit.text().strip():
            self._show_status("✗ Organisation URL and Project are required", ok=False)
            return None
        pat = self._take_pat()
        if not pat:
            self._show_status("✗ A Personal Access Token is required", ok=False)
            return None
        return pat

    def _set_busy(self, busy: bool) -> None:
        self.test_button.setEnabled(not busy)
        self.index_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.settings_panel.save_button.setEnabled(not busy)
        self.open_output_button.setEnabled(not busy and self._result is not None and bool(self._result.exported_files))
        if not busy and self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 1)  # leave the indeterminate "busy" animation

    @property
    def is_busy(self) -> bool:
        return self._worker is not None

    def _start(
        self,
        task: Task,
        on_finished: Callable[[Any], None],
        stage: str,
        cleanup: Callable[[], None] = lambda: None,
    ) -> None:
        worker = TaskWorker(task)

        def done(handler: Callable[..., None]) -> Callable[..., None]:
            def wrapper(*args: Any) -> None:
                self._worker = None
                try:
                    handler(*args)
                finally:
                    cleanup()
                    self._set_busy(False)

            return wrapper

        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(done(on_finished))
        worker.signals.failed.connect(done(self._on_failed))
        worker.signals.cancelled.connect(done(self._on_cancelled))
        self._worker = worker
        self.stage_label.setText(stage)
        self.progress_bar.setRange(0, 0)  # indeterminate until the first progress report
        self._set_busy(True)
        self._pool.start(worker)

    # ---------------------------------------------------------------------------------- actions

    def test_connection(self) -> None:
        pat = self._validate_inputs()
        if pat is None:
            return
        org, project, settings = self.org_edit.text().strip(), self.project_edit.text().strip(), self._current_settings()
        self._show_status("Connecting…")

        def task(_token: Any, _progress: Any) -> ConnectionResult:
            return self._services.test_connection(org, project, pat, settings)

        self._start(task, lambda result: self._on_connection_result(result, pat), "Testing connection", pat.wipe)

    def _on_connection_result(self, result: ConnectionResult, pat: SecretString) -> None:
        self._show_status(result.summary(), ok=result.success)
        self.stage_label.setText("Connected" if result.success else "Connection failed")
        if not result.success:
            return
        self._populate_wikis(result.wikis)
        self._persist_connection(pat)

    def _persist_connection(self, pat: SecretString) -> None:
        """Save non-secret connection settings, and the PAT in Credential Manager if requested."""
        settings = self._current_settings()
        self._settings = settings
        self._services.settings_store.save(settings)
        if settings.remember_pat and self.pat_edit.text():
            try:
                self._services.remember_pat(settings.organization_url, pat)
            except Exception as exc:
                log.exception("Failed to store PAT")
                QMessageBox.warning(self, APP_NAME, f"The PAT could not be saved to Credential Manager:\n{exc}")
                return
            self.pat_edit.clear()  # the stored copy is used from now on
            self._refresh_pat_placeholder()

    def run_indexing(self) -> None:
        pat = self._validate_inputs()
        if pat is None:
            return
        org, project = self.org_edit.text().strip(), self.project_edit.text().strip()
        settings = self._current_settings()
        keywords = self.settings_panel.keyword_list()
        self._show_status("Indexing…")

        def task(token: Any, progress: Any) -> PipelineResult:
            return self._services.run_pipeline(org, project, pat, settings, keywords, token=token, progress=progress)

        self._start(task, self._on_pipeline_result, "Starting", pat.wipe)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.stage_label.setText("Cancelling…")
            self.cancel_button.setEnabled(False)

    def run_search(self) -> None:
        query = self.search_edit.text().strip()
        if self._search is None:
            self.search_summary.setText("Run an indexing job to enable search.")
            return
        if not query:
            return
        try:
            hits = self._search.search(query, self.search_mode.currentData())
        except ValueError as exc:
            self.search_summary.setText(f"✗ {exc}")
            return
        self.search_results.setRowCount(len(hits))
        for row, hit in enumerate(hits):
            location = QTableWidgetItem(hit.location.lstrip("/"))
            location.setData(LocationRole, hit.location)
            self.search_results.setItem(row, 0, location)
            score = QTableWidgetItem(f"{hit.score:g}")
            score.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.search_results.setItem(row, 1, score)
            self.search_results.setItem(row, 2, QTableWidgetItem(hit.snippet))
        self.search_summary.setText(
            f"Search Term: {query}  —  found in {len(hits)} page(s)" if hits else f"Search Term: {query}  —  no matches"
        )

    # ---------------------------------------------------------------------------------- results

    def _on_progress(self, progress: Progress) -> None:
        self.stage_label.setText(f"{progress.stage} {progress.current}/{progress.total}" if progress.total > 1
                                 else progress.stage)
        if progress.total > 0:
            self.progress_bar.setRange(0, progress.total)
            self.progress_bar.setValue(progress.current)

    def _on_failed(self, message: str) -> None:
        self._show_status(f"✗ {message}", ok=False)
        self.stage_label.setText("Failed")

    def _on_cancelled(self) -> None:
        self._show_status("Operation cancelled.")
        self.stage_label.setText("Cancelled")

    def _on_pipeline_result(self, result: PipelineResult) -> None:
        self._result = result
        crawl = result.crawl
        self._search = result.search_engine()
        self._pages = {crawl.location(p): p for p in crawl.all_pages()}
        self._populate_wikis([t.wiki for t in crawl.trees], {t.wiki.id: sum(1 for _ in t.walk()) for t in crawl.trees})
        self._populate_toc(result.toc)
        self.index_model.set_entries(result.index.sorted_entries())
        self.index_view.resizeColumnsToContents()
        self.index_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.search_summary.setText(f"Ready to search {self._search.document_count} pages.")

        lines = [
            f"✓ Indexed {crawl.page_count} pages from {len(crawl.trees)} wiki(s) in {result.elapsed_seconds}s",
            f"Custom keywords: {len(result.index.keywords())}   Terms: {len(result.index.terms())}"
            f"   Analyzer: {result.index.analyzer_name or 'disabled'}",
        ]
        if crawl.failed_pages:
            lines.append(f"⚠ {len(crawl.failed_pages)} page(s) could not be read — see the log and summary.md")
        if result.exported_files:
            lines.append(f"Output: {result.output_dir}")
        self._show_status("\n".join(lines), ok=True)
        self.stage_label.setText("Done")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)

    def _populate_wikis(self, wikis: list[WikiInfo], page_counts: dict[str, int] | None = None) -> None:
        self.wiki_table.setRowCount(len(wikis))
        for row, wiki in enumerate(wikis):
            values = [
                wiki.name,
                wiki.display_type,
                wiki.repository_name or wiki.repository_id,
                wiki.mapped_path,
                ", ".join(wiki.versions),
                "" if page_counts is None else str(page_counts.get(wiki.id, 0)),
            ]
            for column, value in enumerate(values):
                self.wiki_table.setItem(row, column, QTableWidgetItem(value))

    def _populate_toc(self, toc: list[TocNode]) -> None:
        assert self._result is not None
        crawl = self._result.crawl
        location_of = {(p.wiki_name, p.path): crawl.location(p) for p in crawl.all_pages()}
        self.toc_tree.clear()
        self._toc_items.clear()

        def add(node: TocNode, parent: QTreeWidget | QTreeWidgetItem) -> None:
            item = QTreeWidgetItem(parent, [node.title])
            location = location_of.get((node.wiki, node.path))
            if location is not None:
                item.setData(0, LocationRole, location)
                self._toc_items[location] = item
            for child in node.children:
                add(child, item)

        self.toc_tree.setUpdatesEnabled(False)
        for node in toc:
            add(node, self.toc_tree)
        self.toc_tree.expandToDepth(0)
        self.toc_tree.setUpdatesEnabled(True)

    def _on_toc_item_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        page = self._pages.get(current.data(0, LocationRole)) if current else None
        if page is None:
            self.page_view.clear()
            return
        meta = f"**Path:** `{page.path}`  \n**Wiki:** {page.wiki_name}"
        if page.last_modified_by:
            meta += f"  \n**Last modified:** {page.last_modified_date} by {page.last_modified_by}"
        if page.error:
            meta += f"  \n**Error:** {page.error}"
        self.page_view.setMarkdown(f"# {page.title}\n\n{meta}\n\n---\n\n{page.content}")

    def _apply_index_filter(self) -> None:
        self.index_proxy.set_filter(self.index_filter.text(), self.index_kind.currentData())

    def _on_index_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self.index_locations.clear()
        if not current.isValid():
            return
        entry: IndexEntry = self.index_proxy.data(current, IndexTableModel.EntryRole)
        for location in entry.locations:
            item = QListWidgetItem(f"{location.lstrip('/')}  ({entry.occurrences[location]})")
            item.setData(LocationRole, location)
            self.index_locations.addItem(item)

    def _open_location_item(self, item: QListWidgetItem) -> None:
        self.show_page(item.data(LocationRole))

    def _open_search_item(self, item: QTableWidgetItem) -> None:
        self.show_page(self.search_results.item(item.row(), 0).data(LocationRole))

    def show_page(self, location: str) -> None:
        item = self._toc_items.get(location)
        if item is not None:
            self.toc_tree.setCurrentItem(item)
            self.toc_tree.scrollToItem(item)
            self.tabs.setCurrentIndex(1)

    # ------------------------------------------------------------------------------- settings

    def _save_settings(self) -> None:
        settings = self._current_settings()
        self._services.settings_store.save(settings)
        self._services.keyword_store.save(self.settings_panel.keyword_list())
        self._settings = settings
        self.settings_panel.load(settings, self._services.keyword_store.load())
        self.settings_panel.status.setText(f"Saved to {self._services.paths.config_dir}")

    def _forget_pat(self) -> None:
        org = self.org_edit.text().strip()
        if not org:
            return
        self._services.forget_pat(org)
        self._refresh_pat_placeholder()
        self.settings_panel.status.setText("Stored PAT removed from Credential Manager")

    def _open_output(self) -> None:
        if self._result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.output_dir)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._pool.waitForDone(10_000)
        self.pat_edit.clear()
        logging.getLogger().removeHandler(self._log_handler)
        super().closeEvent(event)
