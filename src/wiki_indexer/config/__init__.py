"""Application configuration: settings, custom keywords and file locations."""

from wiki_indexer.config.keywords import KeywordStore
from wiki_indexer.config.paths import AppPaths
from wiki_indexer.config.settings import AppSettings, SettingsStore

__all__ = ["AppPaths", "AppSettings", "KeywordStore", "SettingsStore"]
