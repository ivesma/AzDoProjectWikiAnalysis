"""Domain models for wikis, pages and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterator


class WikiType(StrEnum):
    PROJECT = "projectWiki"
    CODE = "codeWiki"


@dataclass(slots=True)
class WikiInfo:
    id: str
    name: str
    type: str
    repository_id: str = ""
    repository_name: str = ""
    mapped_path: str = "/"
    remote_url: str = ""
    versions: list[str] = field(default_factory=list)

    @property
    def is_code_wiki(self) -> bool:
        return self.type == WikiType.CODE

    @property
    def display_type(self) -> str:
        # A code wiki is created by "publishing" a repository folder; its versions are the published branches.
        return "Code Wiki (Published)" if self.is_code_wiki else "Project Wiki"

    @property
    def default_version(self) -> str | None:
        return self.versions[0] if self.versions else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "displayType": self.display_type,
            "repositoryId": self.repository_id,
            "repository": self.repository_name,
            "rootPath": self.mapped_path,
            "remoteUrl": self.remote_url,
            "publishedVersions": list(self.versions),
        }

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "WikiInfo":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            type=str(data.get("type", WikiType.PROJECT)),
            repository_id=str(data.get("repositoryId", "")),
            mapped_path=str(data.get("mappedPath") or "/"),
            remote_url=str(data.get("remoteUrl", "")),
            versions=[str(v.get("version")) for v in data.get("versions") or [] if v.get("version")],
        )


@dataclass(slots=True)
class WikiPage:
    path: str
    wiki_id: str = ""
    wiki_name: str = ""
    id: int | None = None
    order: int = 0
    git_item_path: str = ""
    content: str = ""
    text: str = ""
    last_modified_by: str = ""
    last_modified_date: str = ""
    error: str = ""
    children: list["WikiPage"] = field(default_factory=list)

    @property
    def title(self) -> str:
        name = self.path.rstrip("/").rsplit("/", 1)[-1]
        return name or self.wiki_name or "/"

    @property
    def depth(self) -> int:
        return len([p for p in self.path.split("/") if p])

    def walk(self) -> Iterator["WikiPage"]:
        """Depth-first, pre-order traversal including ``self``."""
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self, *, include_content: bool = True, include_children: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": "" if self.id is None else str(self.id),
            "path": self.path,
            "title": self.title,
            "wiki": self.wiki_name,
            "lastModifiedBy": self.last_modified_by,
            "lastModifiedDate": self.last_modified_date,
        }
        if include_content:
            data["content"] = self.content
        if self.error:
            data["error"] = self.error
        if include_children:
            data["children"] = [
                c.to_dict(include_content=include_content, include_children=True) for c in self.children
            ]
        return data


@dataclass(slots=True)
class WikiTree:
    wiki: WikiInfo
    pages: list[WikiPage] = field(default_factory=list)  # top-level pages

    def walk(self) -> Iterator[WikiPage]:
        for page in self.pages:
            yield from page.walk()


@dataclass(slots=True)
class CrawlResult:
    trees: list[WikiTree] = field(default_factory=list)

    def all_pages(self) -> Iterator[WikiPage]:
        for tree in self.trees:
            yield from tree.walk()

    @property
    def page_count(self) -> int:
        return sum(1 for _ in self.all_pages())

    @property
    def failed_pages(self) -> list[WikiPage]:
        return [p for p in self.all_pages() if p.error]

    @property
    def is_multi_wiki(self) -> bool:
        return len(self.trees) > 1

    def location(self, page: WikiPage) -> str:
        """Stable page reference used by the index: ``/path`` or ``Wiki:/path`` when several wikis exist."""
        return f"{page.wiki_name}:{page.path}" if self.is_multi_wiki else page.path


@dataclass(slots=True)
class ConnectionResult:
    success: bool
    organization: str = ""
    project: str = ""
    wiki_count: int = 0
    user: str = ""
    error: str = ""
    detail: str = ""
    wikis: list[WikiInfo] = field(default_factory=list)

    def summary(self) -> str:
        if self.success:
            lines = [
                "✓ Connected",
                f"Organisation: {self.organization}",
                f"Project: {self.project}",
                f"Wiki Count: {self.wiki_count}",
            ]
            if self.user:
                lines.append(f"User: {self.user}")
            return "\n".join(lines)
        lines = [f"✗ {self.error}"]
        if self.detail:
            lines.append(self.detail)
        return "\n".join(lines)
