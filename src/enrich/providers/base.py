"""Search provider interface.

LaunchTrace does not scrape search engines.  It calls a search API that permits
programmatic use, or it runs without web enrichment and says so.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""

    @property
    def domain(self) -> str:
        from urllib.parse import urlparse

        return (urlparse(self.url).netloc or "").lower().removeprefix("www.")


class SearchProvider(ABC):
    name = "none"
    available = False

    @abstractmethod
    def search(self, query: str, limit: int = 8) -> list[SearchResult]: ...
