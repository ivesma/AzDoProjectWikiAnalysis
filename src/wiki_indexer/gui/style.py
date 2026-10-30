"""Application palette and stylesheet (Fusion based, Windows 11 inspired)."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

ACCENT = "#0f6cbd"
SUCCESS = "#107c10"
ERROR = "#c42b1c"

STYLESHEET = f"""
QWidget {{ font-family: "Segoe UI", sans-serif; font-size: 10pt; }}
QMainWindow, QDialog {{ background: #f5f5f5; }}
QGroupBox {{
    background: #ffffff; border: 1px solid #e0e0e0; border-radius: 6px;
    margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser, QTreeWidget, QTableView, QTableWidget {{
    background: #ffffff; border: 1px solid #d1d1d1; border-radius: 4px; padding: 4px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QSpinBox {{ min-height: 22px; }}
QPushButton {{
    background: #ffffff; border: 1px solid #d1d1d1; border-radius: 4px; padding: 6px 14px;
}}
QPushButton:hover {{ background: #f0f0f0; }}
QPushButton:disabled {{ color: #a0a0a0; background: #f5f5f5; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT}; }}
QPushButton#primary:hover {{ background: #115ea3; }}
QPushButton#primary:disabled {{ background: #9fc3e7; border-color: #9fc3e7; }}
QTabWidget::pane {{ border: 1px solid #e0e0e0; border-radius: 6px; background: #ffffff; top: -1px; }}
QTabBar::tab {{ padding: 7px 16px; border: none; background: transparent; }}
QTabBar::tab:selected {{ border-bottom: 2px solid {ACCENT}; color: {ACCENT}; font-weight: 600; }}
QProgressBar {{ border: 1px solid #d1d1d1; border-radius: 4px; background: #ffffff; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QHeaderView::section {{ background: #fafafa; border: none; border-bottom: 1px solid #e0e0e0; padding: 5px; }}
QLabel#status {{ padding: 8px; border-radius: 4px; background: #ffffff; border: 1px solid #e0e0e0; }}
"""


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
