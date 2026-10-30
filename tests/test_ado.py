from __future__ import annotations

import base64
import threading

import pytest
import requests
import responses

from tests.conftest import ORG, PAT, PROJECT, FakeAdo
from wiki_indexer.ado.client import AdoClient, normalize_organization_url, organization_name
from wiki_indexer.ado.connection import ConnectionValidator
from wiki_indexer.ado.errors import (
    AdoAuthenticationError,
    AdoConnectionError,
    AdoHttpError,
    AdoNotFoundError,
    AdoPermissionError,
)
from wiki_indexer.ado.models import ConnectionResult, WikiInfo, WikiPage
from wiki_indexer.ado.wiki_service import WikiCrawler, WikiService
from wiki_indexer.utils.cancellation import CancellationToken, OperationCancelled
from wiki_indexer.utils.secrets import SecretString


def client(**kwargs) -> AdoClient:
    return AdoClient(ORG, SecretString(PAT), max_retries=0, **kwargs)


class TestUrls:
    @pytest.mark.parametrize(
        ("url", "name"),
        [
            ("https://dev.azure.com/MyOrg/", "MyOrg"),
            ("dev.azure.com/MyOrg", "MyOrg"),
            ("https://myorg.visualstudio.com", "myorg"),
            ("https://tfs.local/tfs/DefaultCollection", "DefaultCollection"),
            ("https://tfs.local", "tfs.local"),
        ],
    )
    def test_organization_name(self, url: str, name: str) -> None:
        assert organization_name(url) == name

    @pytest.mark.parametrize("url", ["", "   ", "ftp://x"])
    def test_invalid(self, url: str) -> None:
        with pytest.raises(ValueError):
            normalize_organization_url(url)


class TestClient:
    def test_basic_auth_header(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/thing", json={"ok": True})
        with client() as c:
            assert c.get_json("_apis/thing") == {"ok": True}
        sent = mocked.calls[0].request
        assert sent.headers["Authorization"] == "Basic " + base64.b64encode(f":{PAT}".encode()).decode()
        assert "api-version=7.1" in sent.url

    @pytest.mark.parametrize(
        ("status", "error", "content_type"),
        [
            (401, AdoAuthenticationError, "application/json"),
            (203, AdoAuthenticationError, "text/html"),
            (200, AdoAuthenticationError, "text/html"),
            (302, AdoAuthenticationError, "text/html"),
            (403, AdoPermissionError, "application/json"),
            (404, AdoNotFoundError, "application/json"),
            (409, AdoHttpError, "application/json"),
        ],
    )
    def test_error_mapping(self, mocked: responses.RequestsMock, status: int, error: type, content_type: str) -> None:
        body = '{"message": "nope"}' if "json" in content_type else "<html/>"
        mocked.add(responses.GET, f"{ORG}/_apis/x", status=status, body=body, content_type=content_type)
        with pytest.raises(error) as info:
            client().get_json("_apis/x")
        assert info.value.status_code == status

    def test_non_json_body_is_auth_error(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/x", body="not json", content_type="text/plain")
        with pytest.raises(AdoAuthenticationError):
            client().get_json("_apis/x")

    def test_error_without_json_message(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/x", status=500, body="oops", content_type="text/plain")
        with pytest.raises(AdoHttpError):
            client().get_json("_apis/x")

    def test_connection_errors(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/a", body=requests.ConnectionError("dns"))
        mocked.add(responses.GET, f"{ORG}/_apis/b", body=requests.Timeout("slow"))
        with pytest.raises(AdoConnectionError):
            client().get_json("_apis/a")
        with pytest.raises(AdoConnectionError, match="timed out"):
            client().get_json("_apis/b")

    def test_close_drops_auth_header(self) -> None:
        c = client()
        c.close()
        assert "Authorization" not in c._session.headers

    def test_project_url_is_quoted(self) -> None:
        assert client().project_url("My Project") == f"{ORG}/My%20Project"


class TestConnectionValidator:
    def test_success(self, fake_ado: FakeAdo) -> None:
        result = ConnectionValidator(client()).validate(PROJECT)
        assert result.success and result.wiki_count == 1 and result.user == "Test User"
        assert result.summary().splitlines()[:4] == [
            "✓ Connected", "Organisation: myorg", f"Project: {PROJECT}", "Wiki Count: 1"
        ]

    def test_missing_project_name(self) -> None:
        assert ConnectionValidator(client()).validate("  ").error == "Project name is required"

    @pytest.mark.parametrize(
        ("status", "body", "error"),
        [(401, "{}", "Invalid PAT"), (404, "{}", "Organisation not found"), (500, "{}", "Organisation check failed")],
    )
    def test_org_failures(self, mocked: responses.RequestsMock, status: int, body: str, error: str) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/connectionData", status=status, body=body, content_type="application/json")
        result = ConnectionValidator(client()).validate(PROJECT)
        assert not result.success and result.error == error
        assert result.summary().startswith(f"✗ {error}")

    def test_unreachable(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/connectionData", body=requests.ConnectionError("dns"))
        assert ConnectionValidator(client()).validate(PROJECT).error == "Cannot reach Azure DevOps"

    @pytest.mark.parametrize(
        ("status", "error"),
        [(404, "Project not found"), (403, "Access denied to project"), (500, "Project check failed")],
    )
    def test_project_failures(self, mocked: responses.RequestsMock, status: int, error: str) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/connectionData", json={})
        mocked.add(responses.GET, f"{ORG}/_apis/projects/{PROJECT}", status=status, json={"message": "x"})
        assert ConnectionValidator(client()).validate(PROJECT).error == error

    @pytest.mark.parametrize(("status", "error"), [(403, "Access denied to Wiki"), (500, "Wiki check failed")])
    def test_wiki_failures(self, mocked: responses.RequestsMock, status: int, error: str) -> None:
        mocked.add(responses.GET, f"{ORG}/_apis/connectionData", json={})
        mocked.add(responses.GET, f"{ORG}/_apis/projects/{PROJECT}", json={"name": PROJECT})
        mocked.add(responses.GET, f"{ORG}/{PROJECT}/_apis/wiki/wikis", status=status, json={"message": "x"})
        result = ConnectionValidator(client()).validate(PROJECT)
        assert result.error == error
        assert result.summary().startswith(f"✗ {error}")


class TestWikiService:
    def test_list_wikis(self, fake_ado: FakeAdo) -> None:
        wikis = WikiService(client(), PROJECT).list_wikis()
        assert wikis[0].name == "MyProject.wiki" and wikis[0].repository_name == "MyProject.wiki"
        assert wikis[0].display_type == "Project Wiki" and wikis[0].default_version == "wikiMaster"

    def test_repository_name_falls_back_to_id(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/{PROJECT}/_apis/wiki/wikis",
                   json={"value": [{"id": "w", "name": "Code", "type": "codeWiki", "repositoryId": "r9",
                                    "mappedPath": "/docs", "versions": [{"version": "main"}]}]})
        mocked.add(responses.GET, f"{ORG}/{PROJECT}/_apis/git/repositories/r9", status=403, json={})
        wiki = WikiService(client(), PROJECT).list_wikis()[0]
        assert wiki.repository_name == "r9" and wiki.is_code_wiki and wiki.display_type == "Code Wiki (Published)"

    def test_page_tree_hierarchy(self, fake_ado: FakeAdo) -> None:
        service = WikiService(client(), PROJECT)
        top = service.get_page_tree(service.list_wikis()[0])
        assert [p.path for p in top] == ["/Home"]
        assert [c.title for c in top[0].children] == ["Architecture", "Operations", "Runbooks"]
        assert [c.title for c in top[0].children[0].children] == ["Components", "Security"]

    def test_code_wiki_sends_version_descriptor(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/{PROJECT}/_apis/wiki/wikis/w/pages",
                   json={"path": "/Only", "content": "x", "subPages": []})
        wiki = WikiInfo(id="w", name="Code", type="codeWiki", versions=["main"])
        pages = WikiService(client(), PROJECT).get_page_tree(wiki)
        assert [p.path for p in pages] == ["/Only"]
        assert "versionDescriptor.version=main" in mocked.calls[0].request.url

    def test_last_modified_tries_mapped_path(self, mocked: responses.RequestsMock) -> None:
        url = f"{ORG}/{PROJECT}/_apis/git/repositories/r/commits"
        mocked.add(responses.GET, url, json={"value": []})
        mocked.add(responses.GET, url, json={"value": [{"author": {"name": "Ann", "date": "2026-01-01"}}]})
        wiki = WikiInfo(id="w", name="C", type="codeWiki", repository_id="r", mapped_path="/docs", versions=["main"])
        page = WikiPage(path="/A", git_item_path="/A.md")
        assert WikiService(client(), PROJECT).get_last_modified(wiki, page) == ("Ann", "2026-01-01")
        assert "docs%2FA.md" in mocked.calls[1].request.url or "/docs/A.md" in mocked.calls[1].request.url

    def test_last_modified_without_repo(self) -> None:
        wiki = WikiInfo(id="w", name="C", type="projectWiki")
        assert WikiService(client(), PROJECT).get_last_modified(wiki, WikiPage(path="/A")) == ("", "")

    def test_last_modified_no_commits(self, mocked: responses.RequestsMock) -> None:
        mocked.add(responses.GET, f"{ORG}/{PROJECT}/_apis/git/repositories/r/commits", json={"value": []})
        wiki = WikiInfo(id="w", name="C", type="projectWiki", repository_id="r")
        assert WikiService(client(), PROJECT).get_last_modified(wiki, WikiPage(path="/A", git_item_path="/A.md")) == ("", "")


class TestCrawler:
    def test_crawl_reads_all_pages(self, fake_ado: FakeAdo) -> None:
        service = WikiService(client(pool_size=4), PROJECT)
        updates = []
        result = WikiCrawler(service, thread_count=4).crawl(service.list_wikis(), progress=updates.append)
        pages = {p.path: p for p in result.all_pages()}
        assert set(pages) == set(fake_ado.pages)
        assert pages["/Home/Operations"].content == fake_ado.pages["/Home/Operations"]
        assert pages["/Home"].last_modified_by == "Jane Doe"
        assert updates[-1].current == updates[-1].total == len(fake_ado.pages)

    def test_failed_page_is_recorded_not_fatal(self, fake_ado: FakeAdo) -> None:
        fake_ado.failing_paths.add("/Home/Runbooks")
        service = WikiService(client(), PROJECT)
        result = WikiCrawler(service, fetch_last_modified=False).crawl(service.list_wikis())
        assert [p.path for p in result.failed_pages] == ["/Home/Runbooks"]
        assert result.page_count == len(fake_ado.pages)

    def test_cancellation_stops_crawl(self, fake_ado: FakeAdo) -> None:
        service = WikiService(client(), PROJECT)
        token = CancellationToken()

        def cancel_on_first_page(progress) -> None:
            if progress.stage == "Reading pages" and progress.current >= 1:
                token.cancel()

        with pytest.raises(OperationCancelled):
            WikiCrawler(service, thread_count=1).crawl(service.list_wikis(), token, cancel_on_first_page)
        assert len(fake_ado.calls) < len(fake_ado.pages) + 1

    def test_cancel_before_start(self, fake_ado: FakeAdo) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(OperationCancelled):
            WikiCrawler(WikiService(client(), PROJECT)).crawl([WikiInfo("w", "W", "projectWiki")], token)

    def test_auth_error_aborts_crawl(self) -> None:
        class ExpiredService:
            project = PROJECT

            def get_page_tree(self, wiki):
                return [WikiPage(path="/A", wiki_name=wiki.name)]

            def get_page_content(self, wiki, page):
                raise AdoAuthenticationError("expired")

        with pytest.raises(AdoAuthenticationError):
            WikiCrawler(ExpiredService()).crawl([WikiInfo("w", "W", "projectWiki")])  # type: ignore[arg-type]

    def test_last_modified_failure_is_tolerated(self) -> None:
        class FlakyHistory:
            project = PROJECT

            def get_page_tree(self, wiki):
                return [WikiPage(path="/A", wiki_name=wiki.name)]

            def get_page_content(self, wiki, page):
                return "body"

            def get_last_modified(self, wiki, page):
                raise AdoPermissionError("no code read")

        result = WikiCrawler(FlakyHistory()).crawl([WikiInfo("w", "W", "projectWiki")])  # type: ignore[arg-type]
        page = next(result.all_pages())
        assert page.content == "body" and not page.error

    def test_auth_error_in_history_aborts(self) -> None:
        class ExpiredHistory:
            project = PROJECT

            def get_page_tree(self, wiki):
                return [WikiPage(path="/A", wiki_name=wiki.name)]

            def get_page_content(self, wiki, page):
                return "body"

            def get_last_modified(self, wiki, page):
                raise AdoAuthenticationError("expired")

        with pytest.raises(AdoAuthenticationError):
            WikiCrawler(ExpiredHistory()).crawl([WikiInfo("w", "W", "projectWiki")])  # type: ignore[arg-type]

    def test_large_estate_uses_bounded_window(self) -> None:
        in_flight, peak, lock = 0, 0, threading.Lock()

        class Big:
            project = PROJECT

            def get_page_tree(self, wiki):
                return [WikiPage(path=f"/P{i}", wiki_name=wiki.name) for i in range(2000)]

            def get_page_content(self, wiki, page):
                nonlocal in_flight, peak
                with lock:
                    in_flight += 1
                    peak = max(peak, in_flight)
                with lock:
                    in_flight -= 1
                return page.path

        result = WikiCrawler(Big(), thread_count=8, fetch_last_modified=False).crawl(  # type: ignore[arg-type]
            [WikiInfo("w", "W", "projectWiki")]
        )
        assert result.page_count == 2000 and peak <= 8
        assert all(p.content == p.path for p in result.all_pages())


def test_models_serialisation() -> None:
    child = WikiPage(path="/A/B", wiki_name="W", id=2, error="x")
    page = WikiPage(path="/A", wiki_name="W", id=1, content="c", children=[child])
    data = page.to_dict()
    assert data["id"] == "1" and data["title"] == "A" and data["children"][0]["error"] == "x"
    assert "content" not in page.to_dict(include_content=False)
    assert "children" not in page.to_dict(include_children=False)
    assert WikiPage(path="/", wiki_name="W").title == "W"
    assert child.depth == 2
    assert ConnectionResult(success=False, error="E").summary() == "✗ E"
    info = WikiInfo.from_api({"id": "1", "name": "n", "type": "codeWiki", "versions": [{"version": "main"}, {}]})
    assert info.to_dict()["publishedVersions"] == ["main"] and info.to_dict()["rootPath"] == "/"
