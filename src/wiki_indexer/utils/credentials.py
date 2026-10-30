"""PAT persistence in the Windows Credential Manager (via ``keyring``).

The PAT is never written to configuration files. On Windows the default
``keyring`` backend is ``WinVaultKeyring``, which stores generic credentials in
the Credential Manager, encrypted with DPAPI for the current user.
"""

from __future__ import annotations

import logging
from typing import Protocol

from wiki_indexer import APP_ID
from wiki_indexer.utils.secrets import SecretString

log = logging.getLogger(__name__)


class CredentialStore(Protocol):
    def load(self, account: str) -> SecretString | None: ...

    def save(self, account: str, secret: SecretString) -> None: ...

    def delete(self, account: str) -> None: ...


def credential_account(organization_url: str) -> str:
    """Credentials are keyed by organisation so each org can have its own PAT."""
    return organization_url.strip().rstrip("/").lower()


class KeyringCredentialStore:
    def __init__(self, service_name: str = APP_ID, backend: object | None = None) -> None:
        self._service = service_name
        if backend is None:
            import keyring

            backend = keyring.get_keyring()
        self._backend = backend

    def load(self, account: str) -> SecretString | None:
        try:
            value = self._backend.get_password(self._service, account)  # type: ignore[attr-defined]
        except Exception:  # keyring backends raise a variety of errors
            log.exception("Failed to read credential", extra={"account": account})
            return None
        if not value:
            return None
        log.info("Loaded PAT from credential store", extra={"account": account})
        return SecretString(value)

    def save(self, account: str, secret: SecretString) -> None:
        self._backend.set_password(self._service, account, secret.reveal())  # type: ignore[attr-defined]
        log.info("Saved PAT to credential store", extra={"account": account})

    def delete(self, account: str) -> None:
        try:
            self._backend.delete_password(self._service, account)  # type: ignore[attr-defined]
            log.info("Deleted PAT from credential store", extra={"account": account})
        except Exception:
            log.debug("No stored credential to delete", extra={"account": account})


class InMemoryCredentialStore:
    """Non-persistent store used by tests and when no keyring backend exists."""

    def __init__(self) -> None:
        self._items: dict[str, bytes] = {}

    def load(self, account: str) -> SecretString | None:
        value = self._items.get(account)
        return SecretString(value) if value is not None else None

    def save(self, account: str, secret: SecretString) -> None:
        self._items[account] = secret.reveal().encode("utf-8")

    def delete(self, account: str) -> None:
        self._items.pop(account, None)
