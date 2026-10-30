"""Exception hierarchy for Azure DevOps API failures."""

from __future__ import annotations


class AdoError(Exception):
    """Base class for all Azure DevOps errors."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class AdoConnectionError(AdoError):
    """Network-level failure: DNS, TLS, timeout, connection refused."""


class AdoAuthenticationError(AdoError):
    """The PAT is invalid, expired or revoked (HTTP 401, or a 203 sign-in redirect)."""


class AdoPermissionError(AdoError):
    """Authenticated but not authorised (HTTP 403)."""


class AdoNotFoundError(AdoError):
    """The resource does not exist or is not visible to the caller (HTTP 404)."""


class AdoHttpError(AdoError):
    """Any other unexpected HTTP failure."""
