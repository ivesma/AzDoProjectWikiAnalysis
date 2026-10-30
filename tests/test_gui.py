"""GUI tests (pytest-qt). Run headless with QT_QPA_PLATFORM=offscreen (set below)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import Qt, QThreadPool  # noqa: E402

from tests.conftest import ORG, PAT, PROJECT, FakeAdo  # noqa: E402
from wiki_indexer.config.settings import AppSettings  # noqa: E402
from wiki_indexer.gui.log_handler import QtLogHandler  # noqa: E402
from wiki_indexer.gui.main_window import MainWindow  # noqa: E402
from wiki_indexer.gui.style import apply_style  # noqa: E402
from wiki_indexer.gui.workers import TaskWorker  # noqa: E402
from wiki_indexer.indexing.index_builder import EntryKind  # noqa: E402
from wiki_indexer.services import AppServices  # noqa: E402
from wiki_indexer.utils.cancellation import OperationCancelled  # noqa: E402
from wiki_indexer.utils.secrets import SecretString  # noqa: E402

TIMEOUT = 15_000


@pytest.fixture
def window(qtbot, services: AppServices, tmp_path) -> MainWindow:
    services.settings_store.save(AppSettings(output_directory=str(tmp_path / "out"), analyzer="basic", thread_count=2))
    services.keyword_store.save(["Terraform", "AKS"])
    win = MainWindow(services, QThreadPool())
    qtbot.addWidget(win)
    win.org_edit.setText(ORG)
    win.project_edit.setText(PROJECT)
    return win


def wait_idle(qtbot, win: MainWindow) -> None:
    qtbot.waitUntil(lambda: not win.is_busy, timeout=TIMEOUT)


def test_style_applies(qapp) -> None:
    apply_style(qapp)
    assert "QPushButton#primary" in qapp.styleSheet()


def test_initial_state(window: MainWindow) -> None:
    assert window.pat_edit.echoMode() == window.pat_edit.EchoMode.Password
    assert window.test_button.isEnabled() and not window.cancel_button.isEnabled()
    assert window.settings_panel.keyword_list() == ["Terraform", "AKS"]
    window._reveal_action.setChecked(True)
    assert window.pat_edit.echoMode() == window.pat_edit.EchoMode.Normal


def test_requires_inputs(window: MainWindow) -> None:
    window.project_edit.clear()
    window.test_connection()
    assert "required" in window.status_label.text()
    window.project_edit.setText(PROJECT)
    window.run_indexing()
    assert "Personal Access Token is required" in window.status_label.text()


def test_connection_stores_pat_and_lists_wikis(qtbot, window: MainWindow, fake_ado: FakeAdo, services) -> None:
    window.pat_edit.setText(PAT)
    window.remember_check.setChecked(True)
    window.test_connection()
    wait_idle(qtbot, window)
    assert window.status_label.text().startswith("✓ Connected")
    assert window.wiki_table.rowCount() == 1 and window.wiki_table.item(0, 1).text() == "Project Wiki"
    assert window.pat_edit.text() == ""  # moved into the credential store
    assert services.stored_pat(ORG).reveal() == PAT
    assert "Credential Manager" in window.pat_edit.placeholderText()
    assert services.settings_store.load().project == PROJECT

    window._forget_pat()
    assert services.stored_pat(ORG) is None


def test_connection_failure(qtbot, window: MainWindow, mocked) -> None:
    mocked.add("GET", f"{ORG}/_apis/connectionData", status=401)
    window.pat_edit.setText("bad")
    window.test_connection()
    wait_idle(qtbot, window)
    assert window.status_label.text().startswith("✗ Invalid PAT")
    assert window.pat_edit.text() == "bad"


def test_full_indexing_flow(qtbot, window: MainWindow, fake_ado: FakeAdo, tmp_path) -> None:
    window.pat_edit.setText(PAT)
    window.run_indexing()
    assert window.is_busy and not window.index_button.isEnabled()
    wait_idle(qtbot, window)
    assert window.status_label.text().startswith("✓ Indexed 6 pages"), window.status_label.text()
    assert window.open_output_button.isEnabled()
    assert (tmp_path / "out" / "toc.md").exists()

    # Table of contents
    root = window.toc_tree.topLevelItem(0)
    assert root.text(0) == "Home" and root.childCount() == 3
    window.toc_tree.setCurrentItem(root.child(0))
    assert "Architecture" in window.page_view.toPlainText()
    window.toc_tree.setCurrentItem(None)

    # Index view with filtering
    window.index_kind.setCurrentIndex(1)  # custom keywords
    assert window.index_proxy.rowCount() == 2
    window.index_filter.setText("terra")
    assert window.index_proxy.rowCount() == 1
    first = window.index_proxy.index(0, 0)
    window.index_view.setCurrentIndex(first)
    assert window.index_locations.count() == 2
    window._open_location_item(window.index_locations.item(0))
    assert window.tabs.currentIndex() == 1
    window.index_filter.clear()
    window.index_kind.setCurrentIndex(2)
    assert all(
        window.index_proxy.data(window.index_proxy.index(r, 1)) == "Term" for r in range(window.index_proxy.rowCount())
    )
    model = window.index_model
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Term"
    assert model.data(model.index(0, 2), Qt.ItemDataRole.TextAlignmentRole) is not None
    assert model.data(model.index(0, 0), Qt.ItemDataRole.UserRole) is not None
    assert model.data(model.index(-1, 0)) is None
    assert model.data(model.index(0, 0), Qt.ItemDataRole.EntryRole if hasattr(Qt.ItemDataRole, "EntryRole")
                      else model.EntryRole).kind in (EntryKind.KEYWORD, EntryKind.TERM)

    # Search in every mode
    window.search_edit.setText("Terraform")
    window.run_search()
    assert window.search_results.rowCount() == 2 and "found in 2 page(s)" in window.search_summary.text()
    window._open_search_item(window.search_results.item(0, 2))
    assert window.tabs.currentIndex() == 1
    window.search_mode.setCurrentIndex(2)  # regex
    window.search_edit.setText("(")
    window.run_search()
    assert "Invalid regular expression" in window.search_summary.text()
    window.search_mode.setCurrentIndex(3)  # fuzzy
    window.search_edit.setText("zzzzqqq")
    window.run_search()
    assert "no matches" in window.search_summary.text()
    window.search_edit.clear()
    window.run_search()


def test_search_before_indexing(window: MainWindow) -> None:
    window.search_edit.setText("x")
    window.run_search()
    assert "Run an indexing job" in window.search_summary.text()


def test_cancel(qtbot, window: MainWindow, services, monkeypatch) -> None:
    def slow(self, *args, token=None, progress=None, **kwargs):
        qtbot.waitUntil(lambda: token.is_cancelled, timeout=TIMEOUT)
        token.raise_if_cancelled()

    monkeypatch.setattr(AppServices, "run_pipeline", slow)
    window.pat_edit.setText(PAT)
    window.run_indexing()
    window.cancel()
    wait_idle(qtbot, window)
    assert window.status_label.text() == "Operation cancelled."


def test_worker_failure(qtbot, window: MainWindow, monkeypatch) -> None:
    def broken(self, *args, **kwargs):
        raise RuntimeError("exploded")

    monkeypatch.setattr(AppServices, "test_connection", broken)
    window.pat_edit.setText(PAT)
    window.test_connection()
    wait_idle(qtbot, window)
    assert window.status_label.text() == "✗ exploded"


def test_settings_save(window: MainWindow, services, tmp_path) -> None:
    panel = window.settings_panel
    panel.thread_count.setValue(7)
    panel.export_csv.setChecked(False)
    panel.analyzer.setCurrentText("nltk")
    panel.keywords.setPlainText("Kubernetes\n\nkubernetes\nHelm")
    window._save_settings()
    saved = services.settings_store.load()
    assert saved.thread_count == 7 and not saved.export_csv and saved.analyzer == "nltk"
    assert saved.organization_url == ORG
    assert services.keyword_store.load() == ["Kubernetes", "Helm"]
    assert "Saved" in panel.status.text()


def test_close_cancels_running_worker(qtbot, window: MainWindow, monkeypatch) -> None:
    def slow(self, *args, token=None, progress=None, **kwargs):
        while not token.is_cancelled:
            pass
        raise OperationCancelled()

    monkeypatch.setattr(AppServices, "run_pipeline", slow)
    window.pat_edit.setText(PAT)
    window.run_indexing()
    window.close()
    assert window.pat_edit.text() == ""


def test_task_worker_signals(qtbot) -> None:
    worker = TaskWorker(lambda token, progress: 42)
    with qtbot.waitSignal(worker.signals.finished, timeout=TIMEOUT) as blocker:
        QThreadPool.globalInstance().start(worker)
    assert blocker.args == [42]


def test_log_handler_filters_api_noise(qtbot) -> None:
    handler = QtLogHandler()
    received: list[str] = []
    handler.message.connect(received.append)
    logger = logging.getLogger("gui-test")
    logger.addHandler(handler)
    try:
        logger.warning("visible")
        logger.info("API call")
    finally:
        logger.removeHandler(handler)
    assert len(received) == 1 and "visible" in received[0]


def test_secret_not_kept_in_closure_after_use(window: MainWindow, services) -> None:
    services.remember_pat(ORG, SecretString(PAT))
    pat = window._take_pat()
    assert pat is not None and pat.reveal() == PAT
