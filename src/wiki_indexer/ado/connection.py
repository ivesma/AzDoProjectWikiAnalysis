"""Connection validation: PAT, organisation, project and wiki permissions."""

from __future__ import annotations

import logging
from urllib.parse import quote

from wiki_indexer.ado.client import AdoClient
from wiki_indexer.ado.errors import (
    AdoAuthenticationError,
    AdoConnectionError,
    AdoError,
    AdoNotFoundError,
    AdoPermissionError,
)
from wiki_indexer.ado.models import ConnectionResult
from wiki_indexer.ado.wiki_service import WikiService

log = logging.getLogger(__name__)


class ConnectionValidator:
    """Runs the checks in dependency order so the first failure names the real cause."""

    def __init__(self, client: AdoClient) -> None:
        self._client = client

    def validate(self, project: str) -> ConnectionResult:
        org = self._client.organization
        project = project.strip()
        log.info("Validating connection", extra={"organization": org, "project": project})
        if not project:
            return self._fail("Project name is required", org, project)

        # 1. Organisation reachable and PAT accepted.
        try:
            data = self._client.get_json("_apis/connectionData", api_version=None)
        except AdoAuthenticationError as exc:
            return self._fail("Invalid PAT", org, project, "The PAT is invalid, expired or revoked.", exc)
        except AdoNotFoundError as exc:
            return self._fail("Organisation not found", org, project, self._client.organization_url, exc)
        except AdoConnectionError as exc:
            return self._fail("Cannot reach Azure DevOps", org, project, str(exc), exc)
        except AdoError as exc:
            return self._fail("Organisation check failed", org, project, str(exc), exc)
        user = (data.get("authenticatedUser") or {}).get("providerDisplayName", "")

        # 2. Project exists and is visible.
        try:
            project_data = self._client.get_json(f"_apis/projects/{quote(project, safe='')}")
            project = str(project_data.get("name", project))
        except AdoNotFoundError as exc:
            return self._fail("Project not found", org, project, f"'{project}' does not exist or is not visible.", exc)
        except (AdoAuthenticationError, AdoPermissionError) as exc:
            return self._fail("Access denied to project", org, project, str(exc), exc)
        except AdoError as exc:
            return self._fail("Project check failed", org, project, str(exc), exc)

        # 3. Wiki read permission.
        try:
            wikis = WikiService(self._client, project).list_wikis()
        except (AdoAuthenticationError, AdoPermissionError) as exc:
            return self._fail(
                "Access denied to Wiki", org, project, "The PAT needs the 'Wiki (Read)' scope.", exc
            )
        except AdoError as exc:
            return self._fail("Wiki check failed", org, project, str(exc), exc)

        result = ConnectionResult(
            success=True, organization=org, project=project, wiki_count=len(wikis), user=user, wikis=wikis
        )
        log.info("Connection validated", extra={"organization": org, "project": project, "wikiCount": len(wikis)})
        return result

    @staticmethod
    def _fail(
        error: str, org: str, project: str, detail: str = "", exc: Exception | None = None
    ) -> ConnectionResult:
        log.warning(
            "Connection validation failed",
            extra={"organization": org, "project": project, "error": error, "cause": str(exc) if exc else ""},
        )
        return ConnectionResult(success=False, organization=org, project=project, error=error, detail=detail)
