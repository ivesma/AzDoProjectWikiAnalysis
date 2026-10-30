"""Thin, thread-safe HTTP client for the Azure DevOps REST API."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from wiki_indexer import APP_ID, __version__
from wiki_indexer.ado.errors import (
    AdoAuthenticationError,
    AdoConnectionError,
    AdoError,
    AdoHttpError,
    AdoNotFoundError,
    AdoPermissionError,
)
from wiki_indexer.utils.secrets import SecretString

log = logging.getLogger(__name__)

API_VERSION = "7.1"


def normalize_organization_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        raise ValueError("Organisation URL is required")
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid organisation URL: {url}")
    return url


def organization_name(url: str) -> str:
    """``https://dev.azure.com/MyOrg`` → ``MyOrg``; ``https://myorg.visualstudio.com`` → ``myorg``."""
    parsed = urlparse(normalize_organization_url(url))
    host = parsed.netloc.lower()
    segments = [s for s in parsed.path.split("/") if s]
    if host.endswith(".visualstudio.com"):
        return parsed.netloc.split(".")[0]
    if segments:
        return segments[-1]
    return parsed.netloc


def basic_auth_header(pat: SecretString) -> str:
    token = base64.b64encode(f":{pat.reveal()}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


class AdoClient:
    """Wraps a pooled ``requests.Session`` with Basic PAT auth, retries and error mapping."""

    def __init__(
        self,
        organization_url: str,
        pat: SecretString,
        *,
        timeout: int = 30,
        max_retries: int = 5,
        pool_size: int = 10,
        session: requests.Session | None = None,
    ) -> None:
        self.organization_url = normalize_organization_url(organization_url)
        self.organization = organization_name(self.organization_url)
        self.timeout = timeout
        self._session = session or requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._session.headers.update(
            {
                "Authorization": basic_auth_header(pat),
                "Accept": "application/json",
                "User-Agent": f"{APP_ID}/{__version__}",
            }
        )

    def project_url(self, project: str) -> str:
        return f"{self.organization_url}/{quote(project, safe='')}"

    def get_json(self, url: str, params: dict[str, Any] | None = None, *, api_version: str | None = API_VERSION) -> Any:
        """GET ``url`` (absolute, or relative to the organisation) and return decoded JSON."""
        if not url.startswith(("http://", "https://")):
            url = f"{self.organization_url}/{url.lstrip('/')}"
        query = dict(params or {})
        if api_version:
            query.setdefault("api-version", api_version)

        started = time.perf_counter()
        try:
            response = self._session.get(url, params=query, timeout=self.timeout, allow_redirects=False)
        except requests.Timeout as exc:
            log.warning("API call timed out", extra={"url": url})
            raise AdoConnectionError(f"Request timed out: {url}", url=url) from exc
        except requests.RequestException as exc:
            log.warning("API call failed", extra={"url": url, "error": str(exc)})
            raise AdoConnectionError(f"Could not connect to {self.organization_url}: {exc}", url=url) from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info("API call", extra={"method": "GET", "url": url, "status": response.status_code, "elapsedMs": elapsed_ms})
        self._raise_for_status(response, url)
        try:
            return response.json()
        except ValueError as exc:
            raise AdoAuthenticationError(
                "Azure DevOps returned a non-JSON response; the PAT was probably rejected",
                status_code=response.status_code,
                url=url,
            ) from exc

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            return str(response.json().get("message", "")) or response.reason
        except ValueError:
            return response.reason or f"HTTP {response.status_code}"

    def _raise_for_status(self, response: requests.Response, url: str) -> None:
        status = response.status_code
        content_type = response.headers.get("Content-Type", "")
        # An invalid PAT is often answered with 203 + an HTML sign-in page, or a 302 to the sign-in page.
        if status == 401 or status == 203 or (300 <= status < 400) or (
            status == 200 and "text/html" in content_type
        ):
            raise AdoAuthenticationError("Invalid or expired PAT", status_code=status, url=url)
        if status < 400:
            return
        message = self._error_message(response)
        error_type: type[AdoError]
        if status == 403:
            error_type = AdoPermissionError
        elif status == 404:
            error_type = AdoNotFoundError
        else:
            error_type = AdoHttpError
        log.warning("API error", extra={"url": url, "status": status, "error": message})
        raise error_type(message, status_code=status, url=url)

    def close(self) -> None:
        self._session.headers.pop("Authorization", None)
        self._session.close()

    def __enter__(self) -> "AdoClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
