"""Forward log records to the GUI log panel in a thread-safe way."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class _Emitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._emitter = _Emitter()
        self.message = self._emitter.message
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        # The API-call firehose is useful in the log file but noise in the panel.
        if record.levelno < logging.WARNING and record.getMessage() == "API call":
            return
        try:
            self.message.emit(self.format(record))
        except RuntimeError:  # emitter already deleted during shutdown
            pass
