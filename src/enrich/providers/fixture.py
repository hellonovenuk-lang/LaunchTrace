"""Fixture search provider.

Lets the whole enrichment path -- including the maturity and retail-presence
heuristics -- be exercised in tests and in the smoke test with no API key and no
network.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.enrich.providers.base import SearchProvider, SearchResult
from src.parse.normalise import normalise_text
from src.settings import FIXTURES_DIR


class FixtureSearchProvider(SearchProvider):
    name = "fixture"
    available = True

    def __init__(self, path: Path | None = None, data: dict[str, list[dict]] | None = None) -> None:
        if data is None:
            path = path or FIXTURES_DIR / "web" / "search_results.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.data = {normalise_text(k): v for k, v in data.items()}

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        q = normalise_text(query)
        hits: list[dict] = []
        for key, results in self.data.items():
            if key and (key in q or q in key):
                hits = results
                break
        return [
            SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("snippet", ""))
            for r in hits[:limit]
        ]
