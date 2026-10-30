"""Case-insensitive whole-word matching of custom keywords and phrases."""

from __future__ import annotations

import re


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """``Azure DevOps`` matches ``azure  devops`` / ``Azure\\nDevOps`` but not ``MyAzure DevOpsX``.

    Look-arounds on ``\\w`` are used instead of ``\\b`` so keywords that start or
    end with symbols (``C#``, ``.NET``) still match.
    """
    body = r"\s+".join(re.escape(part) for part in keyword.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


class KeywordMatcher:
    def __init__(self, keywords: list[str]) -> None:
        self._patterns = {kw: keyword_pattern(kw) for kw in keywords if kw.strip()}

    @property
    def keywords(self) -> list[str]:
        return list(self._patterns)

    def count(self, text: str) -> dict[str, int]:
        """Return occurrence counts for every keyword found in ``text``."""
        counts: dict[str, int] = {}
        for keyword, pattern in self._patterns.items():
            n = sum(1 for _ in pattern.finditer(text))
            if n:
                counts[keyword] = n
        return counts
