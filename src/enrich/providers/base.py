"""Search provider interface.

LaunchTrace does not scrape search engines.  It calls a search API that permits
programmatic use, or it runs without web enrichment and says so.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


class SearchBudgetExhaustedError(Exception):
    """The run's search budget cannot cover another billed attempt (not retried)."""


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
    #: Set by the caller for the duration of one ``search``: asked before every
    #: billed HTTP attempt (first try and each retry); False refuses it.
    attempt_guard: Callable[[], bool] | None = None

    @abstractmethod
    def search(self, query: str, limit: int = 8) -> list[SearchResult]: ...

    def before_attempt(self) -> None:
        """HTTP providers call this before every request they would be billed for."""
        guard = self.attempt_guard
        if guard is not None and not guard():
            raise SearchBudgetExhaustedError("search_budget_exhausted")
