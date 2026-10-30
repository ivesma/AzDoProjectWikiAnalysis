"""Run blocking work on the Qt thread pool and report back through signals."""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from wiki_indexer.utils.cancellation import CancellationToken, OperationCancelled
from wiki_indexer.utils.progress import ProgressCallback

log = logging.getLogger(__name__)

Task = Callable[[CancellationToken, ProgressCallback], Any]


class WorkerSignals(QObject):
    progress = Signal(object)  # Progress
    finished = Signal(object)  # task result
    failed = Signal(str)
    cancelled = Signal()


class TaskWorker(QRunnable):
    """Executes ``task(token, progress)`` off the GUI thread.

    Signals are delivered to the GUI thread via queued connections because the
    ``WorkerSignals`` object is created (and lives) on the GUI thread.
    """

    def __init__(self, task: Task) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._task = task
        self.token = CancellationToken()
        self.signals = WorkerSignals()

    def cancel(self) -> None:
        self.token.cancel()

    def run(self) -> None:
        try:
            result = self._task(self.token, self.signals.progress.emit)
        except OperationCancelled:
            self.signals.cancelled.emit()
        except Exception as exc:
            log.exception("Background task failed")
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.signals.finished.emit(result)
