from __future__ import annotations

import json
import logging

import pytest

from wiki_indexer.utils.cancellation import CancellationToken, OperationCancelled
from wiki_indexer.utils.credentials import InMemoryCredentialStore, KeyringCredentialStore, credential_account
from wiki_indexer.utils.filenames import safe_segment, wiki_path_segments
from wiki_indexer.utils.logging_setup import LOG_FILE_NAME, JsonFormatter, configure_logging
from wiki_indexer.utils.progress import Progress, null_progress
from wiki_indexer.utils.secrets import SecretString


class TestSecretString:
    def test_reveal_and_masking(self) -> None:
        secret = SecretString("abc")
        assert secret.reveal() == "abc"
        assert "abc" not in repr(secret) and "abc" not in str(secret)
        assert bool(secret) and len(secret) == 3

    def test_wipe_zeroes_buffer(self) -> None:
        secret = SecretString("abc")
        buffer = secret._buffer
        secret.wipe()
        assert bytes(buffer) == b"\x00\x00\x00"
        assert not secret and repr(secret) == "SecretString('')"

    def test_context_manager_wipes(self) -> None:
        with SecretString(b"xyz") as secret:
            assert secret.reveal() == "xyz"
        assert not secret

    def test_equality(self) -> None:
        assert SecretString("a") == SecretString("a")
        assert SecretString("a") != SecretString("b")
        assert SecretString("a").__eq__("a") is NotImplemented


class _FakeKeyring:
    def __init__(self, fail: bool = False) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.fail = fail

    def get_password(self, service: str, account: str) -> str | None:
        if self.fail:
            raise RuntimeError("locked")
        return self.items.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.items[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        del self.items[(service, account)]


class TestCredentials:
    def test_account_normalized(self) -> None:
        assert credential_account(" https://dev.azure.com/MyOrg/ ") == "https://dev.azure.com/myorg"

    def test_keyring_roundtrip(self) -> None:
        backend = _FakeKeyring()
        store = KeyringCredentialStore("svc", backend=backend)
        assert store.load("acct") is None
        store.save("acct", SecretString("pat"))
        assert backend.items[("svc", "acct")] == "pat"
        loaded = store.load("acct")
        assert loaded is not None and loaded.reveal() == "pat"
        store.delete("acct")
        store.delete("acct")  # deleting twice is harmless
        assert store.load("acct") is None

    def test_keyring_errors_are_swallowed_on_load(self) -> None:
        assert KeyringCredentialStore("svc", backend=_FakeKeyring(fail=True)).load("acct") is None

    def test_default_backend(self) -> None:
        assert KeyringCredentialStore()._backend is not None

    def test_in_memory(self) -> None:
        store = InMemoryCredentialStore()
        store.save("a", SecretString("x"))
        assert store.load("a") == SecretString("x")
        store.delete("a")
        assert store.load("a") is None


def test_cancellation() -> None:
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel()
    assert token.is_cancelled
    with pytest.raises(OperationCancelled):
        token.raise_if_cancelled()


def test_progress_percent() -> None:
    assert Progress("x", 5, 10).percent == 50
    assert Progress("x", 1, 0).percent == 0
    assert Progress("x", 20, 10).percent == 100
    null_progress(Progress("x", 0, 0))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("My Page", "My Page"), ('a<b>c:"d|e?*', "a_b_c__d_e__"), ("CON", "_CON"), ("nul.txt", "_nul.txt"), ("...", "_"),
     ("trailing. ", "trailing")],
)
def test_safe_segment(raw: str, expected: str) -> None:
    assert safe_segment(raw) == expected


def test_safe_segment_truncates() -> None:
    assert len(safe_segment("x" * 500)) == 120


def test_wiki_path_segments() -> None:
    assert wiki_path_segments("/Architecture/Q&A: FAQ") == ["Architecture", "Q&A_ FAQ"]
    assert wiki_path_segments("/") == []


def test_configure_logging_writes_json(tmp_path) -> None:
    path = configure_logging(tmp_path, console=True)
    configure_logging(tmp_path, console=False)  # idempotent: replaces its own handlers
    logging.getLogger("t").info("hello", extra={"project": "P"})
    try:
        raise ValueError("bad")
    except ValueError:
        logging.getLogger("t").exception("failed")
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert path.name == LOG_FILE_NAME
    assert any(line["message"] == "hello" and line["project"] == "P" for line in lines)
    assert any("exception" in line for line in lines)
    assert sum(1 for h in logging.getLogger().handlers if getattr(h, "_wiki_indexer", False)) == 1


def test_json_formatter_plain_record() -> None:
    record = logging.LogRecord("n", logging.WARNING, "f", 1, "msg %s", ("x",), None)
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "msg x" and data["level"] == "WARNING"
