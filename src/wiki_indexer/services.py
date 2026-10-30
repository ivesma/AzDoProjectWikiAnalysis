"""Application service facade: the composition root's object graph, shared by GUI and CLI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from wiki_indexer.ado.client import AdoClient
from wiki_indexer.ado.connection import ConnectionValidator
from wiki_indexer.ado.models import ConnectionResult, WikiInfo
from wiki_indexer.ado.wiki_service import WikiService
from wiki_indexer.config.keywords import KeywordStore
from wiki_indexer.config.paths import AppPaths
from wiki_indexer.config.settings import AppSettings, SettingsStore
from wiki_indexer.pipeline import IndexingPipeline, PipelineResult
from wiki_indexer.utils.cancellation import CancellationToken
from wiki_indexer.utils.credentials import (
    CredentialStore,
    InMemoryCredentialStore,
    KeyringCredentialStore,
    credential_account,
)
from wiki_indexer.utils.filenames import safe_segment
from wiki_indexer.utils.progress import ProgressCallback, null_progress
from wiki_indexer.utils.secrets import SecretString

log = logging.getLogger(__name__)

ClientFactory = Callable[[str, SecretString, AppSettings], AdoClient]


def default_client_factory(organization_url: str, pat: SecretString, settings: AppSettings) -> AdoClient:
    return AdoClient(
        organization_url,
        pat,
        timeout=settings.request_timeout_seconds,
        max_retries=settings.max_retries,
        pool_size=settings.thread_count,
    )


@dataclass(slots=True)
class AppServices:
    paths: AppPaths
    settings_store: SettingsStore
    keyword_store: KeywordStore
    credential_store: CredentialStore
    client_factory: ClientFactory = default_client_factory

    @classmethod
    def create(cls, paths: AppPaths) -> "AppServices":
        paths.ensure_user_config()
        try:
            credentials: CredentialStore = KeyringCredentialStore()
        except Exception:  # no usable keyring backend
            log.exception("Credential Manager unavailable; PATs will not be remembered")
            credentials = InMemoryCredentialStore()
        return cls(
            paths=paths,
            settings_store=SettingsStore(paths.settings_file),
            keyword_store=KeywordStore(paths.keywords_file),
            credential_store=credentials,
        )

    def output_dir(self, settings: AppSettings) -> Path:
        return Path(settings.output_directory) if settings.output_directory else self.paths.default_output_dir

    def project_output_dir(self, settings: AppSettings, organization: str, project: str) -> Path:
        """Exports go to ``<output>/<organisation>/<project>/`` so runs never overwrite each other."""
        return self.output_dir(settings) / safe_segment(organization) / safe_segment(project)

    # -- credentials ------------------------------------------------------------------------------

    def stored_pat(self, organization_url: str) -> SecretString | None:
        if not organization_url.strip():
            return None
        return self.credential_store.load(credential_account(organization_url))

    def remember_pat(self, organization_url: str, pat: SecretString) -> None:
        self.credential_store.save(credential_account(organization_url), pat)

    def forget_pat(self, organization_url: str) -> None:
        self.credential_store.delete(credential_account(organization_url))

    # -- operations -------------------------------------------------------------------------------

    def test_connection(
        self, organization_url: str, project: str, pat: SecretString, settings: AppSettings
    ) -> ConnectionResult:
        with self.client_factory(organization_url, pat, settings) as client:
            return ConnectionValidator(client).validate(project)

    def run_pipeline(
        self,
        organization_url: str,
        project: str,
        pat: SecretString,
        settings: AppSettings,
        keywords: list[str],
        *,
        wikis: list[WikiInfo] | None = None,
        token: CancellationToken | None = None,
        progress: ProgressCallback = null_progress,
    ) -> PipelineResult:
        with self.client_factory(organization_url, pat, settings) as client:
            pipeline = IndexingPipeline(
                WikiService(client, project),
                settings,
                keywords,
                self.project_output_dir(settings, client.organization, project),
                organization=client.organization,
            )
            return pipeline.run(wikis, token, progress)
