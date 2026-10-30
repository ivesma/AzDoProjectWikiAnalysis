"""Progress reporting primitives shared by the pipeline, CLI and GUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class Progress:
    stage: str
    current: int
    total: int
    message: str = ""

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.current * 100 / self.total))


ProgressCallback = Callable[[Progress], None]


def null_progress(_: Progress) -> None:
    """Progress sink that discards updates."""
