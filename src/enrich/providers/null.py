from __future__ import annotations

from src.enrich.providers.base import SearchProvider, SearchResult


class NullSearchProvider(SearchProvider):
    """No search configured. Returns nothing; callers must record 'unknown'."""

    name = "none"
    available = False

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        return []
