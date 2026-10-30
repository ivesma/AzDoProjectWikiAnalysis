"""GUI entry point and composition root."""

from __future__ import annotations

import logging
import sys

from wiki_indexer import APP_ID, APP_NAME, __version__
from wiki_indexer.config.paths import AppPaths
from wiki_indexer.services import AppServices
from wiki_indexer.utils.logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from wiki_indexer.gui.main_window import MainWindow
    from wiki_indexer.gui.style import apply_style

    paths = AppPaths.detect()
    log_file = configure_logging(paths.log_dir, console=not getattr(sys, "frozen", False))
    logging.getLogger(__name__).info("Application starting", extra={"version": __version__, "logFile": str(log_file)})

    if sys.platform == "win32":  # give the taskbar our icon rather than python.exe's
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.{__version__}")

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    apply_style(app)
    if paths.icon_file.exists():
        app.setWindowIcon(QIcon(str(paths.icon_file)))

    window = MainWindow(AppServices.create(paths))
    window.show()
    code = app.exec()
    logging.getLogger(__name__).info("Application exiting", extra={"exitCode": code})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
