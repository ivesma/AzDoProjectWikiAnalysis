from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from wiki_indexer.config.keywords import KeywordStore, normalize_keywords
from wiki_indexer.config.paths import AppPaths
from wiki_indexer.config.settings import AppSettings, SettingsStore

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSettings:
    def test_defaults_when_missing(self, tmp_path: Path) -> None:
        assert SettingsStore(tmp_path / "none.json").load() == AppSettings()

    def test_roundtrip_uses_camel_case(self, tmp_path: Path) -> None:
        store = SettingsStore(tmp_path / "s.json")
        store.save(AppSettings(thread_count=4, export_csv=False, organization_url="https://dev.azure.com/x"))
        raw = json.loads(store.path.read_text())
        assert raw["threadCount"] == 4 and raw["exportCsv"] is False and "thread_count" not in raw
        loaded = store.load()
        assert loaded.thread_count == 4 and loaded.export_csv is False

    def test_spec_example_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"exportMarkdown": True, "exportJson": True, "exportCsv": "false",
                                    "threadCount": "10", "indexCustomKeywords": True, "unknownThing": 1}))
        settings = SettingsStore(path).load()
        assert settings.thread_count == 10 and settings.export_csv is False and settings.index_custom_keywords

    def test_validation_clamps(self) -> None:
        s = AppSettings(thread_count=500, request_timeout_seconds=1, max_retries=-3, analyzer="bogus",
                        min_term_length=0, min_term_document_frequency=0).validated()
        assert (s.thread_count, s.request_timeout_seconds, s.max_retries) == (64, 5, 0)
        assert s.analyzer == "auto" and s.min_term_length == 1 and s.min_term_document_frequency == 1

    def test_corrupt_file_falls_back(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text("{not json")
        assert SettingsStore(path).load() == AppSettings()

    def test_shipped_appsettings_is_valid(self) -> None:
        settings = SettingsStore(REPO_ROOT / "config" / "appsettings.json").load()
        assert settings.thread_count == 10 and settings.export_markdown


class TestKeywords:
    def test_normalize(self) -> None:
        assert normalize_keywords([" Terraform ", "terraform", "", "Azure   DevOps"]) == ["Terraform", "Azure DevOps"]

    def test_roundtrip(self, tmp_path: Path) -> None:
        store = KeywordStore(tmp_path / "k.json")
        assert store.load() == []
        store.save(["AKS", "aks", "Databricks"])
        assert json.loads(store.path.read_text()) == {"keywords": ["AKS", "Databricks"]}
        assert store.load() == ["AKS", "Databricks"]

    def test_corrupt(self, tmp_path: Path) -> None:
        (tmp_path / "k.json").write_text("[")
        assert KeywordStore(tmp_path / "k.json").load() == []

    def test_shipped_keywords(self) -> None:
        assert "Terraform" in KeywordStore(REPO_ROOT / "config" / "keywords.json").load()


class TestPaths:
    def test_dev_mode_uses_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WIKI_INDEXER_HOME", raising=False)
        paths = AppPaths.detect()
        assert paths.config_dir == REPO_ROOT / "config"
        assert paths.settings_file.name == "appsettings.json" and paths.keywords_file.name == "keywords.json"
        assert paths.icon_file == REPO_ROOT / "assets" / "app.ico"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("WIKI_INDEXER_HOME", str(tmp_path))
        paths = AppPaths.detect()
        assert paths.log_dir == tmp_path / "logs" and paths.default_output_dir == tmp_path / "output"

    def test_frozen_mode(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("WIKI_INDEXER_HOME", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        paths = AppPaths.detect()
        assert paths.resources_dir == tmp_path / "bundle"
        assert paths.config_dir == tmp_path / "roaming" / "AzureDevOpsWikiIndexer"
        assert paths.log_dir == tmp_path / "local" / "AzureDevOpsWikiIndexer" / "logs"

    def test_ensure_user_config_copies_defaults(self, tmp_path: Path) -> None:
        resources = tmp_path / "res"
        (resources / "config").mkdir(parents=True)
        (resources / "config" / "appsettings.json").write_text("{}")
        (resources / "config" / "keywords.json").write_text('{"keywords": ["X"]}')
        paths = AppPaths.for_home(tmp_path / "home", resources)
        paths.ensure_user_config()
        assert paths.keywords_file.read_text() == '{"keywords": ["X"]}'
        paths.keywords_file.write_text('{"keywords": ["Y"]}')
        paths.ensure_user_config()  # never overwrites user edits
        assert "Y" in paths.keywords_file.read_text()
