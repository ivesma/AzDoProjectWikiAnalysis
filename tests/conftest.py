from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from wiki_indexer.ado.models import CrawlResult, WikiInfo, WikiPage, WikiTree
from wiki_indexer.config.keywords import KeywordStore
from wiki_indexer.config.paths import AppPaths
from wiki_indexer.config.settings import SettingsStore
from wiki_indexer.services import AppServices
from wiki_indexer.utils.credentials import InMemoryCredentialStore

ORG = "https://dev.azure.com/myorg"
PROJECT = "MyProject"
PAT = "test-pat-123"

PAGES = {
    "/Home": "# Home\nWelcome to the platform wiki.",
    "/Home/Architecture": "# Architecture\nWe deploy **AKS** clusters with Terraform.",
    "/Home/Architecture/Components": "Components run on Databricks and AKS.",
    "/Home/Architecture/Security": "Security uses Azure DevOps service connections.",
    "/Home/Operations": "Operations runbooks are written in PowerShell.",
    "/Home/Runbooks": "```powershell\nInvoke-Build\n```\nThe build pipeline runs Terraform plan.",
}


def page_tree(paths: list[str]) -> dict[str, Any]:
    """Build the nested ``subPages`` structure the wiki pages API returns."""
    root: dict[str, Any] = {"path": "/", "order": 0, "gitItemPath": "/.order", "subPages": []}
    nodes = {"/": root}
    for order, path in enumerate(sorted(paths, key=lambda p: p.count("/"))):
        parent = path.rsplit("/", 1)[0] or "/"
        node = {"path": path, "id": order + 1, "order": order, "gitItemPath": f"{path}.md", "subPages": []}
        nodes[parent]["subPages"].append(node)
        nodes[path] = node
    return root


@dataclass
class FakeAdo:
    """Registers Azure DevOps REST endpoints on a ``responses`` mock."""

    mock: responses.RequestsMock
    pages: dict[str, str] = field(default_factory=lambda: dict(PAGES))
    wikis: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "id": "wiki-1",
                "name": "MyProject.wiki",
                "type": "projectWiki",
                "repositoryId": "repo-1",
                "mappedPath": "/",
                "versions": [{"version": "wikiMaster"}],
            }
        ]
    )
    failing_paths: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def install(self) -> "FakeAdo":
        m = self.mock
        m.add(
            responses.GET,
            f"{ORG}/_apis/connectionData",
            json={"authenticatedUser": {"providerDisplayName": "Test User"}},
        )
        m.add(responses.GET, f"{ORG}/_apis/projects/{PROJECT}", json={"name": PROJECT, "id": "p1"})
        m.add(responses.GET, f"{ORG}/{PROJECT}/_apis/wiki/wikis", json={"value": self.wikis, "count": len(self.wikis)})
        m.add(
            responses.GET,
            re.compile(rf"{re.escape(ORG)}/{PROJECT}/_apis/git/repositories/[\w-]+(\?.*)?$"),
            json={"name": "MyProject.wiki"},
        )
        m.add_callback(
            responses.GET,
            re.compile(rf"{re.escape(ORG)}/{PROJECT}/_apis/git/repositories/[\w-]+/commits.*"),
            callback=self._commits,
        )
        m.add_callback(
            responses.GET,
            re.compile(rf"{re.escape(ORG)}/{PROJECT}/_apis/wiki/wikis/[\w-]+/pages.*"),
            callback=self._pages,
        )
        return self

    def _pages(self, request: Any) -> tuple[int, dict[str, str], str]:
        query = parse_qs(urlparse(request.url).query)
        path = query["path"][0]
        self.calls.append(path)
        if query.get("recursionLevel") == ["full"]:
            return 200, {"Content-Type": "application/json"}, json.dumps(page_tree(list(self.pages)))
        if path in self.failing_paths:
            return 500, {"Content-Type": "application/json"}, json.dumps({"message": "boom"})
        body = {"path": path, "content": self.pages.get(path, ""), "gitItemPath": f"{path}.md"}
        return 200, {"Content-Type": "application/json"}, json.dumps(body)

    def _commits(self, request: Any) -> tuple[int, dict[str, str], str]:
        body = {"value": [{"author": {"name": "Jane Doe", "date": "2026-01-02T03:04:05Z"}}]}
        return 200, {"Content-Type": "application/json"}, json.dumps(body)


@pytest.fixture
def mocked() -> Any:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def fake_ado(mocked: responses.RequestsMock) -> FakeAdo:
    return FakeAdo(mocked).install()


def make_page(path: str, text: str = "", content: str | None = None, wiki: str = "Wiki", children=None) -> WikiPage:
    return WikiPage(
        path=path,
        wiki_id=wiki.lower(),
        wiki_name=wiki,
        content=text if content is None else content,
        text=text,
        children=list(children or []),
    )


@pytest.fixture
def sample_crawl() -> CrawlResult:
    iac = make_page("/Architecture/IaC", "Terraform modules provision AKS clusters. Terraform state is remote.")
    sec = make_page("/Architecture/Security", "Security reviews for Azure DevOps pipelines.")
    arch = make_page("/Architecture", "Architecture overview of the platform.", children=[iac, sec])
    build = make_page("/Runbooks/BuildPipeline", "Run terraform plan, then deploy.")
    runbooks = make_page("/Runbooks", "Operational runbooks written in PowerShell.", children=[build])
    data = make_page("/DataPlatform", "Databricks workspaces and clusters.")
    wiki = WikiInfo(id="wiki", name="Wiki", type="projectWiki", repository_id="r1", versions=["wikiMaster"])
    return CrawlResult(trees=[WikiTree(wiki=wiki, pages=[arch, runbooks, data])])


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.for_home(tmp_path / "home", resources_dir=tmp_path / "res")


@pytest.fixture
def services(app_paths: AppPaths) -> AppServices:
    return AppServices(
        paths=app_paths,
        settings_store=SettingsStore(app_paths.settings_file),
        keyword_store=KeywordStore(app_paths.keywords_file),
        credential_store=InMemoryCredentialStore(),
    )
