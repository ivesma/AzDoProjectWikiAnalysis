from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests.conftest import ORG, PAT, PROJECT, FakeAdo, make_page
from wiki_indexer import cli
from wiki_indexer.ado.client import AdoClient
from wiki_indexer.ado.models import CrawlResult, WikiInfo, WikiTree
from wiki_indexer.ado.wiki_service import WikiService
from wiki_indexer.config.settings import AppSettings
from wiki_indexer.exporters import CsvExporter, ExportContext, ExportService, JsonExporter, MarkdownExporter
from wiki_indexer.exporters.markdown_exporter import OWNERSHIP_MARKER, page_file_map, render_index
from wiki_indexer.indexing.analyzers import BasicAnalyzer
from wiki_indexer.indexing.index_builder import IndexBuilder
from wiki_indexer.indexing.toc import build_toc
from wiki_indexer.pipeline import IndexingPipeline, build_exporters
from wiki_indexer.services import AppServices
from wiki_indexer.utils.cancellation import CancellationToken, OperationCancelled
from wiki_indexer.utils.secrets import SecretString

KEYWORDS = ["Terraform", "Databricks", "Kubernetes"]


def context_for(crawl: CrawlResult, out: Path) -> ExportContext:
    index = IndexBuilder(BasicAnalyzer(), KEYWORDS).build(crawl)
    return ExportContext(crawl, index, build_toc(crawl), out, organization="myorg", project="P")


class TestMarkdownExporter:
    def test_layout(self, sample_crawl: CrawlResult, tmp_path: Path) -> None:
        sample_crawl.trees[0].pages[0].content = "# Original **markdown**"
        files = MarkdownExporter().export(context_for(sample_crawl, tmp_path))
        assert (tmp_path / "wiki" / "Architecture.md").read_text(encoding="utf-8") == "# Original **markdown**"
        assert (tmp_path / "wiki" / "Architecture" / "IaC.md").exists()
        assert (tmp_path / "wiki" / "Runbooks" / "BuildPipeline.md").exists()
        assert {p.name for p in files} >= {"toc.md", "index.md", "summary.md"}

        toc = (tmp_path / "toc.md").read_text(encoding="utf-8")
        assert "  - [IaC](wiki/Architecture/IaC.md)" in toc

        index = (tmp_path / "index.md").read_text(encoding="utf-8")
        assert "## Terraform\n- [Architecture/IaC](wiki/Architecture/IaC.md)\n- [Runbooks/BuildPipeline]" in index
        assert "## Kubernetes\n_No occurrences._" in index

        summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
        assert "**Pages:** 6" in summary and "| Terraform | 2 | 3 |" in summary and "├── IaC" in summary

    def test_regenerates_owned_wiki_folder_only(self, sample_crawl: CrawlResult, tmp_path: Path) -> None:
        stale = tmp_path / "wiki" / "Deleted.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("old")
        MarkdownExporter().export(context_for(sample_crawl, tmp_path))
        assert stale.exists()  # folder wasn't ours yet: never delete user files
        assert (tmp_path / OWNERSHIP_MARKER).exists()
        MarkdownExporter().export(context_for(sample_crawl, tmp_path))
        assert not stale.exists()

    def test_file_names_are_safe_and_unique(self, tmp_path: Path) -> None:
        pages = [make_page("/Q&A: What?"), make_page("/q&a: what?"), make_page("/CON")]
        crawl = CrawlResult([WikiTree(WikiInfo("w", "W", "projectWiki"), pages)])
        mapping = page_file_map(crawl)
        assert [str(p) for p in mapping.values()] == ["wiki/Q&A_ What_.md", "wiki/q&a_ what_~2.md", "wiki/_CON.md"]

    def test_failed_pages_in_summary_and_multi_wiki_folders(self, sample_crawl: CrawlResult, tmp_path: Path) -> None:
        broken = make_page("/Broken", wiki="Docs")
        broken.error = "HTTP 500"
        sample_crawl.trees.append(WikiTree(WikiInfo("d", "Docs", "codeWiki", mapped_path="/docs"), [broken]))
        MarkdownExporter().export(context_for(sample_crawl, tmp_path))
        assert (tmp_path / "wiki" / "Docs" / "Broken.md").exists()
        assert (tmp_path / "wiki" / "Wiki" / "Architecture" / "IaC.md").exists()
        assert "Docs:/Broken: HTTP 500" in (tmp_path / "summary.md").read_text(encoding="utf-8")

    def test_render_index_without_links(self, sample_crawl: CrawlResult) -> None:
        index = IndexBuilder(None, ["Databricks"]).build(sample_crawl)
        assert render_index(index) == "# Index\n\n## Databricks\n- DataPlatform\n"


def test_json_exporter(sample_crawl: CrawlResult, tmp_path: Path) -> None:
    JsonExporter().export(context_for(sample_crawl, tmp_path))
    wiki = json.loads((tmp_path / "wiki.json").read_text(encoding="utf-8"))
    page = wiki["wikis"][0]["pages"][0]
    assert set(page) >= {"id", "path", "title", "content", "lastModifiedBy", "lastModifiedDate", "children"}
    assert page["children"][0]["path"] == "/Architecture/IaC"
    assert json.loads((tmp_path / "index.json").read_text())["Databricks"] == ["/DataPlatform"]
    assert json.loads((tmp_path / "toc.json").read_text())["toc"][0]["title"] == "Architecture"
    assert json.loads((tmp_path / "wikis.json").read_text())["wikis"][0]["rootPath"] == "/"


def test_csv_exporter(sample_crawl: CrawlResult, tmp_path: Path) -> None:
    CsvExporter().export(context_for(sample_crawl, tmp_path))
    with (tmp_path / "page_inventory.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 6 and rows[0]["Path"] == "/Architecture" and rows[0]["ChildPages"] == "2"
    with (tmp_path / "keyword_index.csv").open(encoding="utf-8-sig") as fh:
        keyword_rows = list(csv.DictReader(fh))
    assert {"Term": "Terraform", "Type": "keyword", "Location": "/Architecture/IaC", "Occurrences": "2"} in keyword_rows
    assert any(r["Type"] == "term" for r in keyword_rows)


def test_export_service_honours_cancellation(sample_crawl: CrawlResult, tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        ExportService([CsvExporter()]).export(context_for(sample_crawl, tmp_path), token)
    assert ExportService([CsvExporter(), JsonExporter()]).exporter_names == ["csv", "json"]


def test_build_exporters() -> None:
    assert [e.name for e in build_exporters(AppSettings())] == ["markdown", "json", "csv"]
    assert build_exporters(AppSettings(export_markdown=False, export_json=False, export_csv=False)) == []


class TestPipeline:
    def test_end_to_end(self, fake_ado: FakeAdo, tmp_path: Path) -> None:
        client = AdoClient(ORG, SecretString(PAT), max_retries=0)
        settings = AppSettings(thread_count=3, analyzer="basic")
        stages: list[str] = []
        result = IndexingPipeline(
            WikiService(client, PROJECT), settings, ["Terraform", "AKS", "PowerShell"], tmp_path, organization="myorg"
        ).run(progress=lambda p: stages.append(p.stage))
        assert result.crawl.page_count == 6
        assert result.index.to_json_dict()["Terraform"] == ["/Home/Architecture", "/Home/Runbooks"]
        assert "Invoke-Build" in next(p for p in result.crawl.all_pages() if p.path == "/Home/Runbooks").text
        assert {"toc.md", "index.md", "summary.md", "wiki.json", "page_inventory.csv"} <= {
            p.name for p in result.exported_files
        }
        assert stages[0] == "Discovering wikis" and stages[-1] == "Done"
        assert [h.path for h in result.search_engine().search("PowerShell")] == ["/Home/Operations"]

    def test_keywords_disabled_and_no_export(self, fake_ado: FakeAdo, tmp_path: Path) -> None:
        client = AdoClient(ORG, SecretString(PAT), max_retries=0)
        settings = AppSettings(index_custom_keywords=False, index_standard_terms=False, fetch_last_modified=False)
        result = IndexingPipeline(WikiService(client, PROJECT), settings, ["Terraform"], tmp_path, exporters=[]).run()
        assert result.index.entries == {} and result.analyzer is None and result.exported_files == []


class TestServicesAndCli:
    def test_services_pat_roundtrip(self, services: AppServices) -> None:
        assert services.stored_pat("") is None
        services.remember_pat(ORG, SecretString(PAT))
        assert services.stored_pat(ORG + "/").reveal() == PAT
        services.forget_pat(ORG)
        assert services.stored_pat(ORG) is None

    def test_output_dir(self, services: AppServices, tmp_path: Path) -> None:
        assert services.output_dir(AppSettings()) == services.paths.default_output_dir
        assert services.output_dir(AppSettings(output_directory=str(tmp_path))) == tmp_path

    def test_project_output_dir(self, services: AppServices, tmp_path: Path) -> None:
        settings = AppSettings(output_directory=str(tmp_path))
        assert services.project_output_dir(settings, "myorg", "My Project") == tmp_path / "myorg" / "My Project"
        assert services.project_output_dir(settings, "org", "a/b:c") == tmp_path / "org" / "a_b_c"
        assert services.project_output_dir(settings, "org1", "P") != services.project_output_dir(settings, "org2", "P")

    def test_create_with_paths(self, app_paths, monkeypatch: pytest.MonkeyPatch) -> None:
        import wiki_indexer.services as services_module

        monkeypatch.setattr(services_module, "KeyringCredentialStore", lambda: (_ for _ in ()).throw(RuntimeError()))
        created = AppServices.create(app_paths)
        assert created.settings_store.path == app_paths.settings_file
        assert type(created.credential_store).__name__ == "InMemoryCredentialStore"

    def run_cli(self, services: AppServices, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
        monkeypatch.setenv("WIKI_INDEXER_HOME", str(services.paths.config_dir.parent))
        return cli.main(list(args), services=services)

    def test_cli_full_run(self, fake_ado: FakeAdo, services: AppServices, monkeypatch, capsys, tmp_path) -> None:
        monkeypatch.setenv("AZDO_PAT", PAT)
        services.keyword_store.save(["Terraform"])
        code = self.run_cli(services, monkeypatch, "--org", ORG, "--project", PROJECT, "--output", str(tmp_path),
                            "--search", "Terraform", "--threads", "2")
        out = capsys.readouterr().out
        assert code == 0
        assert "✓ Connected" in out and "Indexed 6 pages" in out and "Search Term: Terraform" in out
        assert (tmp_path / "myorg" / PROJECT / "index.md").exists()

    def test_cli_test_only_with_stored_pat(self, fake_ado: FakeAdo, services: AppServices, monkeypatch, capsys) -> None:
        monkeypatch.delenv("AZDO_PAT", raising=False)
        services.remember_pat(ORG, SecretString(PAT))
        assert self.run_cli(services, monkeypatch, "--org", ORG, "--project", PROJECT, "--test") == 0
        assert "Wiki Count: 1" in capsys.readouterr().out

    def test_cli_missing_inputs(self, services: AppServices, monkeypatch, capsys) -> None:
        monkeypatch.delenv("AZDO_PAT", raising=False)
        assert self.run_cli(services, monkeypatch) == 2
        assert self.run_cli(services, monkeypatch, "--org", ORG, "--project", PROJECT) == 2
        assert "no PAT" in capsys.readouterr().err

    def test_cli_connection_failure(self, mocked, services: AppServices, monkeypatch) -> None:
        mocked.add("GET", f"{ORG}/_apis/connectionData", status=401)
        monkeypatch.setenv("AZDO_PAT", "bad")
        assert self.run_cli(services, monkeypatch, "--org", ORG, "--project", PROJECT) == 1

    def test_cli_unexpected_error(self, services: AppServices, monkeypatch, capsys) -> None:
        monkeypatch.setenv("AZDO_PAT", PAT)

        def boom(*_args, **_kwargs):
            raise RuntimeError("kaput")

        monkeypatch.setattr(AppServices, "test_connection", boom)
        assert self.run_cli(services, monkeypatch, "--org", ORG, "--project", PROJECT) == 1
        assert "kaput" in capsys.readouterr().err

    def test_cli_redirected_output_becomes_utf8(self, monkeypatch) -> None:
        import io

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        monkeypatch.setattr("sys.stdout", stream)
        cli._utf8_redirected_output()
        print("✓ Connected", file=stream)
        stream.flush()
        assert raw.getvalue().decode("utf-8").startswith("✓ Connected")

    def test_cli_cancel(self, fake_ado: FakeAdo, services: AppServices, monkeypatch) -> None:
        monkeypatch.setenv("AZDO_PAT", PAT)

        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(AppServices, "run_pipeline", interrupted)
        assert self.run_cli(services, monkeypatch, "--org", ORG, "--project", PROJECT) == 130
