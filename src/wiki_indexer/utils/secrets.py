"""In-memory handling of secrets such as the Personal Access Token.

Python strings are immutable and may be copied by the interpreter, so a secret
can never be guaranteed to be wiped. ``SecretString`` keeps the canonical copy
in a mutable ``bytearray`` that is zeroed on ``wipe()``, keeps the value out of
``repr``/``str`` (and therefore out of logs), and minimises the number of
transient ``str`` copies callers need to create.
"""

from __future__ import annotations


class SecretString:
    __slots__ = ("_buffer",)

    def __init__(self, value: str | bytes | bytearray = "") -> None:
        if isinstance(value, str):
            self._buffer = bytearray(value.encode("utf-8"))
        else:
            self._buffer = bytearray(value)

    def reveal(self) -> str:
        """Return the secret as a ``str``. Keep the result short-lived."""
        return self._buffer.decode("utf-8")

    def wipe(self) -> None:
        for i in range(len(self._buffer)):
            self._buffer[i] = 0
        self._buffer = bytearray()

    def __bool__(self) -> bool:
        return len(self._buffer) > 0

    def __len__(self) -> int:
        return len(self._buffer)

    def __repr__(self) -> str:
        return "SecretString('********')" if self._buffer else "SecretString('')"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretString):
            return self._buffer == other._buffer
        return NotImplemented

    def __hash__(self) -> int:  # pragma: no cover - secrets are not hashable keys
        raise TypeError("SecretString is unhashable")

    def __enter__(self) -> "SecretString":
        return self

    def __exit__(self, *_: object) -> None:
        self.wipe()
