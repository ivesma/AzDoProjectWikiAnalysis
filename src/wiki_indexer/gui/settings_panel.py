"""Settings tab: edit appsettings.json and keywords.json from the GUI."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from wiki_indexer.config.keywords import normalize_keywords
from wiki_indexer.config.settings import ANALYZERS, AppSettings


class SettingsPanel(QWidget):
    save_requested = Signal()
    forget_pat_requested = Signal()

    def __init__(self, default_output_dir: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base = AppSettings()

        self.export_markdown = QCheckBox("Markdown (wiki/, toc.md, index.md, summary.md)")
        self.export_json = QCheckBox("JSON (wiki.json, toc.json, index.json)")
        self.export_csv = QCheckBox("CSV (page_inventory.csv, keyword_index.csv)")
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText(default_output_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir, 1)
        output_row.addWidget(browse)

        self.thread_count = self._spin(1, 64)
        self.timeout = self._spin(5, 300, " s")
        self.max_retries = self._spin(0, 10)
        self.fetch_last_modified = QCheckBox("Look up last author and date (one extra API call per page)")

        self.index_keywords = QCheckBox("Index custom keywords")
        self.index_terms = QCheckBox("Index standard terms (stemming / lemmatisation)")
        self.analyzer = QComboBox()
        self.analyzer.addItems(list(ANALYZERS))
        self.include_code = QCheckBox("Include code blocks in indexed text")
        self.min_term_length = self._spin(1, 20)
        self.min_df = self._spin(1, 10_000)

        self.keywords = QPlainTextEdit()
        self.keywords.setPlaceholderText("One keyword or phrase per line, e.g.\nTerraform\nAzure DevOps")

        export_box = QGroupBox("Export")
        export_form = QFormLayout(export_box)
        export_form.addRow(self.export_markdown)
        export_form.addRow(self.export_json)
        export_form.addRow(self.export_csv)
        export_form.addRow("Output folder", output_row)

        crawl_box = QGroupBox("Retrieval")
        crawl_form = QFormLayout(crawl_box)
        crawl_form.addRow("Threads", self.thread_count)
        crawl_form.addRow("Request timeout", self.timeout)
        crawl_form.addRow("Max retries", self.max_retries)
        crawl_form.addRow(self.fetch_last_modified)

        index_box = QGroupBox("Indexing")
        index_form = QFormLayout(index_box)
        index_form.addRow(self.index_keywords)
        index_form.addRow(self.index_terms)
        index_form.addRow("Analyzer", self.analyzer)
        index_form.addRow(self.include_code)
        index_form.addRow("Minimum term length", self.min_term_length)
        index_form.addRow("Minimum pages per term", self.min_df)

        keyword_box = QGroupBox("Custom keywords")
        keyword_layout = QVBoxLayout(keyword_box)
        keyword_layout.addWidget(self.keywords)

        self.save_button = QPushButton("Save Settings")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save_requested)
        self.forget_button = QPushButton("Forget Stored PAT")
        self.forget_button.clicked.connect(self.forget_pat_requested)
        self.status = QLabel()
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.forget_button)
        buttons.addWidget(self.save_button)

        left = QVBoxLayout()
        left.addWidget(export_box)
        left.addWidget(crawl_box)
        left.addWidget(index_box)
        left.addStretch(1)
        columns = QHBoxLayout()
        columns.addLayout(left, 3)
        columns.addWidget(keyword_box, 2)
        root = QVBoxLayout(self)
        root.addLayout(columns, 1)
        root.addLayout(buttons)

    @staticmethod
    def _spin(lo: int, hi: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setSuffix(suffix)
        spin.setMaximumWidth(120)
        return spin

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_dir.text())
        if folder:
            self.output_dir.setText(folder)

    def load(self, settings: AppSettings, keywords: list[str]) -> None:
        self._base = settings
        self.export_markdown.setChecked(settings.export_markdown)
        self.export_json.setChecked(settings.export_json)
        self.export_csv.setChecked(settings.export_csv)
        self.output_dir.setText(settings.output_directory)
        self.thread_count.setValue(settings.thread_count)
        self.timeout.setValue(settings.request_timeout_seconds)
        self.max_retries.setValue(settings.max_retries)
        self.fetch_last_modified.setChecked(settings.fetch_last_modified)
        self.index_keywords.setChecked(settings.index_custom_keywords)
        self.index_terms.setChecked(settings.index_standard_terms)
        self.analyzer.setCurrentText(settings.analyzer)
        self.include_code.setChecked(settings.include_code_blocks)
        self.min_term_length.setValue(settings.min_term_length)
        self.min_df.setValue(settings.min_term_document_frequency)
        self.keywords.setPlainText("\n".join(keywords))

    def settings(self) -> AppSettings:
        return replace(
            self._base,
            export_markdown=self.export_markdown.isChecked(),
            export_json=self.export_json.isChecked(),
            export_csv=self.export_csv.isChecked(),
            output_directory=self.output_dir.text().strip(),
            thread_count=self.thread_count.value(),
            request_timeout_seconds=self.timeout.value(),
            max_retries=self.max_retries.value(),
            fetch_last_modified=self.fetch_last_modified.isChecked(),
            index_custom_keywords=self.index_keywords.isChecked(),
            index_standard_terms=self.index_terms.isChecked(),
            analyzer=self.analyzer.currentText(),
            include_code_blocks=self.include_code.isChecked(),
            min_term_length=self.min_term_length.value(),
            min_term_document_frequency=self.min_df.value(),
        ).validated()

    def keyword_list(self) -> list[str]:
        return normalize_keywords(self.keywords.toPlainText().splitlines())
