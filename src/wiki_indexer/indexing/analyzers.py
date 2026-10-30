"""Language analysers that turn text into normalised index terms.

``create_analyzer("auto")`` picks the best available implementation:

1. ``SpacyAnalyzer`` – lemmatisation with spaCy (needs ``spacy`` + ``en_core_web_sm``)
2. ``NltkAnalyzer`` – Snowball stemming with NLTK (no corpus download needed)
3. ``BasicAnalyzer`` – dependency-free lower-casing and plural folding

All analysers share the same tokeniser and stop-word handling so results are
comparable, and each ``Token`` keeps its surface form for human-readable output.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)

# Words start with a letter; may contain digits, '+', '#', '-', '_' or an apostrophe (C#, C++, pre-prod, don't).
_WORD = re.compile(r"[^\W\d_](?:[\w'+#-]*[\w+#])?", re.UNICODE)

STOP_WORDS: frozenset[str] = frozenset(
    """
    a about above after again against all also am an and any are aren't as at be because been before being below
    between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each
    eg etc few for from further get gets got had hadn't has hasn't have haven't having he he'd he'll he's her here
    here's hers herself him himself his how how's however i i'd i'll i'm i've ie if in into is isn't it it's its
    itself just let's like may me might more most must mustn't my myself need no nor not now of off on once only or
    other ought our ours ourselves out over own per same shall shan't she she'd she'll she's should shouldn't so
    some such than that that's the their theirs them themselves then there there's these they they'd they'll
    they're they've this those through to too under until up upon us use used using very via was wasn't we we'd
    we'll we're we've were weren't what what's when when's where where's whether which while who who's whom why
    why's will with within without won't would wouldn't yes yet you you'd you'll you're you've your yours yourself
    yourselves
    """.split()
)


@dataclass(frozen=True, slots=True)
class Token:
    term: str  # normalised key (stem / lemma)
    surface: str  # lower-cased word as written


class TextAnalyzer(Protocol):
    name: str

    def analyze(self, text: str) -> list[Token]: ...


def tokenize(text: str) -> list[str]:
    words = []
    for match in _WORD.finditer(text):
        word = match.group(0).lower().strip("'-")
        if word.endswith("'s"):
            word = word[:-2]
        if word:
            words.append(word)
    return words


class _BaseAnalyzer:
    name = "base"

    def __init__(self, min_length: int = 3, stop_words: frozenset[str] = STOP_WORDS) -> None:
        self.min_length = min_length
        self.stop_words = stop_words

    def _keep(self, word: str) -> bool:
        return len(word) >= self.min_length and word not in self.stop_words

    def normalize(self, word: str) -> str:
        return word

    def analyze(self, text: str) -> list[Token]:
        return [Token(self.normalize(w), w) for w in tokenize(text) if self._keep(w)]


class BasicAnalyzer(_BaseAnalyzer):
    name = "basic"

    def normalize(self, word: str) -> str:
        return _fold_plural(word)


@lru_cache(maxsize=200_000)
def _fold_plural(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


class NltkAnalyzer(_BaseAnalyzer):
    name = "nltk"

    def __init__(self, min_length: int = 3) -> None:
        from nltk.stem.snowball import SnowballStemmer

        super().__init__(min_length, STOP_WORDS | _nltk_stop_words())
        self._stem: Callable[[str], str] = lru_cache(maxsize=200_000)(SnowballStemmer("english").stem)

    def normalize(self, word: str) -> str:
        return self._stem(word)


def _nltk_stop_words() -> frozenset[str]:
    try:
        from nltk.corpus import stopwords

        return frozenset(stopwords.words("english"))
    except LookupError:
        return frozenset()  # corpus not downloaded; the built-in list is used


class SpacyAnalyzer(_BaseAnalyzer):
    name = "spacy"
    MAX_CHUNK = 100_000

    def __init__(self, min_length: int = 3, nlp: Any | None = None, model: str = "en_core_web_sm") -> None:
        if nlp is None:
            import spacy

            nlp = spacy.load(model, disable=["parser", "ner"])
        super().__init__(min_length, STOP_WORDS | frozenset(getattr(nlp.Defaults, "stop_words", ())))
        self._nlp = nlp

    def analyze(self, text: str) -> list[Token]:
        tokens: list[Token] = []
        for start in range(0, len(text), self.MAX_CHUNK):
            for tok in self._nlp(text[start : start + self.MAX_CHUNK]):
                surface = tok.text.lower()
                if not tok.is_alpha or tok.is_stop or not self._keep(surface):
                    continue
                tokens.append(Token(tok.lemma_.lower() or surface, surface))
        return tokens


def create_analyzer(name: str = "auto", min_length: int = 3) -> TextAnalyzer:
    """Build the requested analyser, falling back gracefully when libraries are missing."""
    order = {"auto": ["spacy", "nltk", "basic"], "spacy": ["spacy", "nltk", "basic"], "nltk": ["nltk", "basic"]}.get(
        name, ["basic"]
    )
    factories: dict[str, Callable[[], TextAnalyzer]] = {
        "spacy": lambda: SpacyAnalyzer(min_length),
        "nltk": lambda: NltkAnalyzer(min_length),
        "basic": lambda: BasicAnalyzer(min_length),
    }
    for candidate in order:
        try:
            analyzer = factories[candidate]()
        except (ImportError, OSError) as exc:
            log.info("Analyzer unavailable", extra={"analyzer": candidate, "reason": str(exc)})
            continue
        log.info("Using analyzer", extra={"analyzer": analyzer.name})
        return analyzer
    raise RuntimeError("No analyzer available")  # pragma: no cover - BasicAnalyzer cannot fail
