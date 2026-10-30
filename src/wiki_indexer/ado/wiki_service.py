"""Wiki discovery, page tree retrieval and multithreaded content crawling."""

from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any

from wiki_indexer.ado.client import AdoClient
from wiki_indexer.ado.errors import AdoAuthenticationError, AdoError
from wiki_indexer.ado.models import CrawlResult, WikiInfo, WikiPage, WikiTree
from wiki_indexer.utils.cancellation import CancellationToken
from wiki_indexer.utils.progress import Progress, ProgressCallback, null_progress

log = logging.getLogger(__name__)


class WikiService:
    """Read-only operations against the Wiki and Git REST APIs for a single project."""

    def __init__(self, client: AdoClient, project: str) -> None:
        self._client = client
        self.project = project
        self._project_url = client.project_url(project)

    def list_wikis(self) -> list[WikiInfo]:
        data = self._client.get_json(f"{self._project_url}/_apis/wiki/wikis")
        wikis = [WikiInfo.from_api(item) for item in data.get("value", [])]
        for wiki in wikis:
            wiki.repository_name = self._repository_name(wiki.repository_id)
        log.info("Wikis discovered", extra={"project": self.project, "count": len(wikis)})
        return wikis

    def _repository_name(self, repository_id: str) -> str:
        if not repository_id:
            return ""
        try:
            data = self._client.get_json(f"{self._project_url}/_apis/git/repositories/{repository_id}")
            return str(data.get("name", ""))
        except AdoError:
            # Not fatal: the PAT might lack Code (Read) scope; fall back to the id.
            log.debug("Could not resolve repository name", extra={"repositoryId": repository_id})
            return repository_id

    def _version_params(self, wiki: WikiInfo) -> dict[str, str]:
        if wiki.is_code_wiki and wiki.default_version:
            return {"versionDescriptor.version": wiki.default_version, "versionDescriptor.versionType": "branch"}
        return {}

    def get_page_tree(self, wiki: WikiInfo) -> list[WikiPage]:
        """Return the top-level pages of ``wiki`` with their full hierarchy (no content)."""
        params = {"path": "/", "recursionLevel": "full", "includeContent": "false", **self._version_params(wiki)}
        data = self._client.get_json(f"{self._project_url}/_apis/wiki/wikis/{wiki.id}/pages", params)
        root = self._build_page(wiki, data)
        if root.path in ("", "/"):
            return root.children
        return [root]

    def _build_page(self, wiki: WikiInfo, data: dict[str, Any]) -> WikiPage:
        page = WikiPage(
            path=str(data.get("path", "/")),
            wiki_id=wiki.id,
            wiki_name=wiki.name,
            id=data.get("id"),
            order=int(data.get("order") or 0),
            git_item_path=str(data.get("gitItemPath", "")),
        )
        children = [self._build_page(wiki, sub) for sub in data.get("subPages") or []]
        page.children = sorted(children, key=lambda p: (p.order, p.title.casefold()))
        return page

    def get_page_content(self, wiki: WikiInfo, page: WikiPage) -> str:
        params = {"path": page.path, "includeContent": "true", **self._version_params(wiki)}
        data = self._client.get_json(f"{self._project_url}/_apis/wiki/wikis/{wiki.id}/pages", params)
        return str(data.get("content") or "")

    def get_last_modified(self, wiki: WikiInfo, page: WikiPage) -> tuple[str, str]:
        """Return ``(author, ISO date)`` of the latest commit touching the page's markdown file."""
        if not (wiki.repository_id and page.git_item_path):
            return "", ""
        candidates = [page.git_item_path]
        mapped = wiki.mapped_path.rstrip("/")
        if mapped and not page.git_item_path.startswith(mapped + "/"):
            candidates.append(f"{mapped}{page.git_item_path}")
        for item_path in candidates:
            params: dict[str, str] = {"searchCriteria.itemPath": item_path, "searchCriteria.$top": "1"}
            if wiki.default_version:
                params["searchCriteria.itemVersion.version"] = wiki.default_version
            data = self._client.get_json(
                f"{self._project_url}/_apis/git/repositories/{wiki.repository_id}/commits", params
            )
            commits = data.get("value") or []
            if commits:
                author = commits[0].get("author") or {}
                return str(author.get("name", "")), str(author.get("date", ""))
        return "", ""


class WikiCrawler:
    """Fetches page trees, then page content in parallel using a thread pool."""

    def __init__(self, service: WikiService, *, thread_count: int = 10, fetch_last_modified: bool = True) -> None:
        self._service = service
        self._threads = max(1, thread_count)
        self._fetch_last_modified = fetch_last_modified

    def crawl(
        self,
        wikis: list[WikiInfo],
        token: CancellationToken | None = None,
        progress: ProgressCallback = null_progress,
    ) -> CrawlResult:
        token = token or CancellationToken()
        result = CrawlResult()
        for index, wiki in enumerate(wikis, start=1):
            token.raise_if_cancelled()
            progress(Progress("Reading page tree", index - 1, len(wikis), wiki.name))
            pages = self._service.get_page_tree(wiki)
            tree = WikiTree(wiki=wiki, pages=pages)
            result.trees.append(tree)
            log.info("Page tree loaded", extra={"wiki": wiki.name, "pages": sum(1 for _ in tree.walk())})

        work = [(tree.wiki, page) for tree in result.trees for page in tree.walk()]
        self._fetch_all(work, token, progress)
        log.info(
            "Crawl complete",
            extra={"pages": len(work), "failed": len(result.failed_pages), "wikis": len(result.trees)},
        )
        return result

    def _fetch_one(self, wiki: WikiInfo, page: WikiPage, token: CancellationToken) -> None:
        token.raise_if_cancelled()
        try:
            page.content = self._service.get_page_content(wiki, page)
            if self._fetch_last_modified:
                try:
                    page.last_modified_by, page.last_modified_date = self._service.get_last_modified(wiki, page)
                except AdoAuthenticationError:
                    raise
                except AdoError as exc:
                    log.debug("Last-modified lookup failed", extra={"path": page.path, "error": str(exc)})
        except AdoAuthenticationError:
            raise
        except AdoError as exc:
            page.error = str(exc)
            log.warning("Failed to read page", extra={"wiki": wiki.name, "path": page.path, "error": str(exc)})

    def _fetch_all(
        self, work: list[tuple[WikiInfo, WikiPage]], token: CancellationToken, progress: ProgressCallback
    ) -> None:
        total = len(work)
        done = 0
        progress(Progress("Reading pages", 0, total))
        # Bound the number of in-flight futures so 10k+ page estates don't queue everything up front.
        window = self._threads * 4
        items = iter(work)
        with ThreadPoolExecutor(max_workers=self._threads, thread_name_prefix="wiki-crawler") as pool:
            pending: set[Future[None]] = set()
            try:
                while True:
                    while len(pending) < window and not token.is_cancelled:
                        item = next(items, None)
                        if item is None:
                            break
                        pending.add(pool.submit(self._fetch_one, item[0], item[1], token))
                    if not pending:
                        break
                    finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in finished:
                        future.result()  # re-raise auth errors / cancellation
                        done += 1
                    progress(Progress("Reading pages", done, total))
                    token.raise_if_cancelled()
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
