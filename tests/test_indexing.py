from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from tests.conftest import make_page
from wiki_indexer.ado.models import CrawlResult, WikiInfo, WikiTree
from wiki_indexer.indexing.analyzers import (
    BasicAnalyzer,
    NltkAnalyzer,
    SpacyAnalyzer,
    Token,
    create_analyzer,
    tokenize,
)
from wiki_indexer.indexing.index_builder import EntryKind, IndexBuilder
from wiki_indexer.indexing.keyword_matcher import KeywordMatcher
from wiki_indexer.indexing.markdown_text import MarkdownTextExtractor, strip_html
from wiki_indexer.indexing.search import SearchEngine, SearchMode, format_results, make_snippet
from wiki_indexer.indexing.toc import build_toc, render_markdown, render_tree
from wiki_indexer.utils.cancellation import CancellationToken, OperationCancelled

KEYWORDS = ["Terraform", "AKS", "Azure DevOps", "PowerShell", "Databricks"]


class TestMarkdownText:
    def setup_method(self) -> None:
        self.extract = MarkdownTextExtractor().extract

    def test_headings_and_emphasis(self) -> None:
        assert self.extract("# Title\n\nSome **bold** and _italic_ ~~gone~~ text") == (
            "Title\nSome bold and italic gone text"
        )

    def test_links_and_images_keep_text(self) -> None:
        assert self.extract("See [the docs](https://x.y) ![arch diagram](img.png)") == "See the docs arch diagram"

    def test_tables(self) -> None:
        text = self.extract("| Tool | Use |\n|---|---|\n| Terraform | IaC |\n| AKS | Hosting |")
        assert text.splitlines() == ["Tool Use", "Terraform IaC", "AKS Hosting"]

    def test_code_blocks_configurable(self) -> None:
        md = "Intro\n\n```bash\nterraform apply\n```\n\n    indented code\n\nUse `kubectl` now"
        assert "terraform apply" in self.extract(md) and "indented code" in self.extract(md)
        stripped = MarkdownTextExtractor(include_code_blocks=False).extract(md)
        assert "terraform apply" not in stripped and "kubectl" in stripped

    def test_embedded_html(self) -> None:
        md = '<div class="x">Hello <b>world</b><script>alert(1)</script><style>p{}</style></div>\n\nText <span>inline</span>'
        text = self.extract(md)
        assert "Hello world" in text and "alert" not in text and "p{}" not in text and "Text inline" in text

    def test_html_tables_and_images(self) -> None:
        text = strip_html("<table><tr><td>A</td><td>B</td></tr></table><img alt='logo'><br>&amp; done")
        assert "A" in text and "B" in text and "logo" in text and "& done" in text

    def test_ado_extensions(self) -> None:
        md = (
            "---\ntitle: Front\n---\n[[_TOC_]]\n[[_TOSP_]]\n::: mermaid\ngraph TD\n:::\n"
            "Ping @<5F2C3AB1-1234-4567-89AB-0123456789AB> please"
        )
        text = self.extract(md)
        assert "_TOC_" not in text and ":::" not in text and "@<" not in text
        assert "title: Front" in text and "graph TD" in text and "Ping please" in text

    def test_lists_and_quotes(self) -> None:
        text = self.extract("- one\n- two\n\n> quoted\n\n1. first")
        assert text.startswith("one\ntwo\n")  # tight list items are not separated by blank lines
        assert [line for line in text.splitlines() if line] == ["one", "two", "quoted", "first"]

    def test_empty(self) -> None:
        assert self.extract("") == ""


class TestAnalyzers:
    def test_tokenize(self) -> None:
        assert tokenize("C# and C++ in pre-prod, Bob's 2024 v2") == ["c#", "and", "c++", "in", "pre-prod", "bob", "v2"]

    def test_basic_filters_stop_words_and_folds_plurals(self) -> None:
        terms = [t.term for t in BasicAnalyzer().analyze("The policies for the clusters and with from AKS")]
        assert terms == ["policy", "cluster", "aks"]

    def test_basic_keeps_ss_words(self) -> None:
        assert [t.term for t in BasicAnalyzer().analyze("access status")] == ["access", "status"]

    def test_nltk_stems(self) -> None:
        tokens = NltkAnalyzer().analyze("Deploying deployments deployed")
        assert {t.term for t in tokens} == {"deploy"}
        assert tokens[0] == Token("deploy", "deploying")

    def test_nltk_without_stopword_corpus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import nltk.corpus

        class Missing:
            def words(self, _lang: str) -> list[str]:
                raise LookupError("not downloaded")

        monkeypatch.setattr(nltk.corpus, "stopwords", Missing())
        assert NltkAnalyzer().analyze("the cluster")[0].term == "cluster"

    def test_spacy_with_injected_pipeline(self) -> None:
        def tok(text: str, lemma: str, alpha: bool = True, stop: bool = False) -> SimpleNamespace:
            return SimpleNamespace(text=text, lemma_=lemma, is_alpha=alpha, is_stop=stop)

        nlp = lambda text: [tok("Clusters", "cluster"), tok("the", "the", stop=True), tok("42", "42", alpha=False),
                            tok("ran", "run")]  # noqa: E731
        nlp.Defaults = SimpleNamespace(stop_words={"foo"})  # type: ignore[attr-defined]
        analyzer = SpacyAnalyzer(nlp=nlp)
        assert analyzer.analyze("ignored") == [Token("cluster", "clusters"), Token("run", "ran")]
        assert "foo" in analyzer.stop_words

    def test_factory_fallbacks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_import = builtins.__import__

        def no_spacy(name: str, *args, **kwargs):
            if name == "spacy":
                raise ImportError("no spacy")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_spacy)
        assert create_analyzer("auto").name == "nltk"
        assert create_analyzer("spacy").name == "nltk"
        assert create_analyzer("nltk").name == "nltk"
        assert create_analyzer("basic").name == "basic"
        assert create_analyzer("unknown").name == "basic"


class TestKeywordMatcher:
    def test_counts_whole_words_case_insensitive(self) -> None:
        matcher = KeywordMatcher(["AKS", "Azure DevOps", "C#", ""])
        counts = matcher.count("aks, AKS! tasks. Azure\n DevOps and azure devops. C# not C#x")
        assert counts == {"AKS": 2, "Azure DevOps": 2, "C#": 1}
        assert matcher.keywords == ["AKS", "Azure DevOps", "C#"]


class TestIndexBuilder:
    def test_build(self, sample_crawl: CrawlResult) -> None:
        index = IndexBuilder(NltkAnalyzer(), KEYWORDS).build(sample_crawl)
        data = index.to_json_dict()
        assert data["Terraform"] == ["/Architecture/IaC", "/Runbooks/BuildPipeline"]
        assert data["Databricks"] == ["/DataPlatform"]
        assert index.entries["Terraform"].occurrences["/Architecture/IaC"] == 2  # every occurrence is counted
        assert index.entries["Terraform"].kind is EntryKind.KEYWORD
        # Terms that duplicate a custom keyword are not listed twice.
        assert "terraform" not in data and "clusters" in data
        assert "the" not in data and "and" not in data
        assert index.analyzer_name == "nltk"
        assert all(e.kind is EntryKind.TERM for e in index.terms())

    def test_min_document_frequency(self, sample_crawl: CrawlResult) -> None:
        index = IndexBuilder(BasicAnalyzer(), [], min_document_frequency=2).build(sample_crawl)
        assert "clusters" in index.to_json_dict() and "modules" not in index.to_json_dict()

    def test_keywords_only(self, sample_crawl: CrawlResult) -> None:
        index = IndexBuilder(None, ["Kubernetes"]).build(sample_crawl)
        assert index.to_json_dict() == {"Kubernetes": []} and index.analyzer_name == ""

    def test_multi_wiki_locations(self, sample_crawl: CrawlResult) -> None:
        other = WikiTree(WikiInfo("w2", "Docs", "codeWiki"), [make_page("/Guide", "Terraform guide", wiki="Docs")])
        sample_crawl.trees.append(other)
        index = IndexBuilder(None, ["Terraform"]).build(sample_crawl)
        assert "Docs:/Guide" in index.to_json_dict()["Terraform"]
        assert "Wiki:/Architecture/IaC" in index.to_json_dict()["Terraform"]

    def test_cancellation(self) -> None:
        pages = [make_page(f"/P{i}", "text") for i in range(60)]
        crawl = CrawlResult([WikiTree(WikiInfo("w", "W", "projectWiki"), pages)])
        token = CancellationToken()
        token.cancel()
        with pytest.raises(OperationCancelled):
            IndexBuilder(BasicAnalyzer(), []).build(crawl, token)


class TestToc:
    def test_markdown_matches_spec_shape(self, sample_crawl: CrawlResult) -> None:
        md = render_markdown(build_toc(sample_crawl))
        assert md.splitlines() == [
            "# Table of Contents", "", "- Architecture", "  - IaC", "  - Security", "- Runbooks",
            "  - BuildPipeline", "- DataPlatform",
        ]

    def test_links(self, sample_crawl: CrawlResult) -> None:
        md = render_markdown(build_toc(sample_crawl), lambda n: f"wiki{n.path}.md" if n.path != "/Runbooks" else None)
        assert "- [IaC](wiki/Architecture/IaC.md)" in md and "- Runbooks" in md

    def test_tree_and_json(self, sample_crawl: CrawlResult) -> None:
        toc = build_toc(sample_crawl)
        assert render_tree(toc).splitlines()[:3] == ["Architecture", "├── IaC", "└── Security"]
        assert toc[0].to_dict()["children"][0]["path"] == "/Architecture/IaC"

    def test_multi_wiki_roots(self, sample_crawl: CrawlResult) -> None:
        sample_crawl.trees.append(WikiTree(WikiInfo("w2", "Docs [v2]", "codeWiki"), [make_page("/A", wiki="Docs")]))
        toc = build_toc(sample_crawl)
        assert [n.title for n in toc] == ["Wiki", "Docs [v2]"]
        assert r"[Docs \[v2\]](x)" in render_markdown(toc, lambda n: "x")


class TestSearch:
    @pytest.fixture
    def engine(self, sample_crawl: CrawlResult) -> SearchEngine:
        analyzer = NltkAnalyzer()
        return SearchEngine(sample_crawl, IndexBuilder(analyzer, KEYWORDS).build(sample_crawl), analyzer)

    def paths(self, engine: SearchEngine, query: str, mode: SearchMode) -> list[str]:
        return sorted(h.path for h in engine.search(query, mode))

    def test_exact_phrase(self, engine: SearchEngine) -> None:
        assert self.paths(engine, "terraform plan", SearchMode.EXACT) == ["/Runbooks/BuildPipeline"]
        hit = engine.search("remote", SearchMode.EXACT)[0]
        assert "remote" in hit.snippet and hit.title == "IaC"

    def test_keyword_uses_stemming(self, engine: SearchEngine) -> None:
        assert self.paths(engine, "cluster", SearchMode.KEYWORD) == ["/Architecture/IaC", "/DataPlatform"]
        assert self.paths(engine, "clusters workspace", SearchMode.KEYWORD) == ["/DataPlatform"]

    def test_keyword_uses_custom_keywords(self, engine: SearchEngine) -> None:
        assert self.paths(engine, "azure  devops", "keyword") == ["/Architecture/Security"]

    def test_keyword_only_stop_words_falls_back_to_phrase(self, engine: SearchEngine) -> None:
        assert self.paths(engine, "then", SearchMode.KEYWORD) == ["/Runbooks/BuildPipeline"]

    def test_regex(self, engine: SearchEngine) -> None:
        assert self.paths(engine, r"terraform\s+(plan|state)", SearchMode.REGEX) == [
            "/Architecture/IaC", "/Runbooks/BuildPipeline"
        ]
        with pytest.raises(ValueError, match="Invalid regular expression"):
            engine.search("(", SearchMode.REGEX)

    def test_fuzzy_tolerates_typos(self, engine: SearchEngine) -> None:
        assert "/DataPlatform" in self.paths(engine, "Databriks", SearchMode.FUZZY)
        assert "/Architecture/IaC" in self.paths(engine, "terrafrom", SearchMode.FUZZY)
        assert "/Runbooks" in self.paths(engine, "Runbok", SearchMode.FUZZY)

    def test_empty_query_and_no_analyzer(self, sample_crawl: CrawlResult) -> None:
        engine = SearchEngine(sample_crawl, IndexBuilder(None, []).build(sample_crawl), None)
        assert engine.search("   ") == []
        assert [h.path for h in engine.search("Databricks")] == ["/DataPlatform"]
        assert engine.document_count == 6

    def test_results_sorted_by_score(self, engine: SearchEngine) -> None:
        hits = engine.search("terraform", SearchMode.EXACT)
        assert hits[0].path == "/Architecture/IaC" and hits[0].score >= hits[-1].score

    def test_format_results(self, engine: SearchEngine) -> None:
        text = format_results("Terraform", engine.search("Terraform"))
        assert text.splitlines()[:4] == ["Search Term: Terraform", "", "Found in:", ""]
        assert "Architecture/IaC" in text
        assert format_results("x", []).endswith("No matches found.")

    def test_snippet(self) -> None:
        text = "a" * 200 + "MATCH" + "b" * 200
        snippet = make_snippet(text, 200, 205, radius=10)
        assert snippet == "…" + "a" * 10 + "MATCH" + "b" * 10 + "…"
        assert make_snippet("short", 0, 5) == "short"
