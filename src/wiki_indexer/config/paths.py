"""Resolve where configuration, logs and output live.

* Development (running from source): everything lives in the repository root
  (``config/``, ``logs/``, ``output/``).
* Installed (PyInstaller bundle): the install directory is read-only, so
  editable config goes to ``%APPDATA%``, logs to ``%LOCALAPPDATA%`` and output
  to ``Documents``. Default config files are copied from the bundle on first run.
* ``WIKI_INDEXER_HOME`` overrides both and puts everything under one folder.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from wiki_indexer import APP_ID

SETTINGS_FILE = "appsettings.json"
KEYWORDS_FILE = "keywords.json"


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class AppPaths:
    resources_dir: Path
    config_dir: Path
    log_dir: Path
    default_output_dir: Path

    @property
    def settings_file(self) -> Path:
        return self.config_dir / SETTINGS_FILE

    @property
    def keywords_file(self) -> Path:
        return self.config_dir / KEYWORDS_FILE

    @property
    def icon_file(self) -> Path:
        return self.resources_dir / "assets" / "app.ico"

    @classmethod
    def for_home(cls, home: Path, resources_dir: Path | None = None) -> "AppPaths":
        return cls(
            resources_dir=resources_dir or _bundle_root(),
            config_dir=home / "config",
            log_dir=home / "logs",
            default_output_dir=home / "output",
        )

    @classmethod
    def detect(cls) -> "AppPaths":
        override = os.environ.get("WIKI_INDEXER_HOME")
        if override:
            return cls.for_home(Path(override))
        bundle = _bundle_root()
        if not getattr(sys, "frozen", False):
            return cls.for_home(bundle, bundle)
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return cls(
            resources_dir=bundle,
            config_dir=appdata / APP_ID,
            log_dir=local / APP_ID / "logs",
            default_output_dir=Path.home() / "Documents" / APP_ID / "output",
        )

    def ensure_user_config(self) -> None:
        """Copy bundled default config files into the user config dir if missing."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        for name in (SETTINGS_FILE, KEYWORDS_FILE):
            target = self.config_dir / name
            source = self.resources_dir / "config" / name
            if not target.exists() and source.exists() and source != target:
                shutil.copyfile(source, target)
