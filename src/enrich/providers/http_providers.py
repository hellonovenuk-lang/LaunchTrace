"""Search API providers.

All three are commercial APIs with free tiers adequate for a weekly run of a few
hundred lookups.  Swap between them with ``SEARCH_PROVIDER``; only one key is
ever needed.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.enrich.providers.base import SearchProvider, SearchResult
from src.errors import ProviderError, RateLimitedError
from src.logging_setup import get_logger

log = get_logger(__name__)


def _retrying_post(url: str, timeout: int, **kwargs):  # type: ignore[no-untyped-def]
    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        retry=retry_if_exception_type((RateLimitedError, httpx.TransportError, ProviderError)),
    )
    def _do():  # type: ignore[no-untyped-def]
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, **kwargs)
        if resp.status_code == 429:
            raise RateLimitedError(f"429 from {url}")
        if resp.status_code >= 500:
            raise ProviderError(f"{resp.status_code} from {url}")
        resp.raise_for_status()
        return resp.json()

    return _do()


def _retrying_get(url: str, timeout: int, **kwargs):  # type: ignore[no-untyped-def]
    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        retry=retry_if_exception_type((RateLimitedError, httpx.TransportError, ProviderError)),
    )
    def _do():  # type: ignore[no-untyped-def]
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, **kwargs)
        if resp.status_code == 429:
            raise RateLimitedError(f"429 from {url}")
        if resp.status_code >= 500:
            raise ProviderError(f"{resp.status_code} from {url}")
        resp.raise_for_status()
        return resp.json()

    return _do()


class TavilyProvider(SearchProvider):
    name = "tavily"
    available = True

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        data = _retrying_post(
            "https://api.tavily.com/search",
            self.timeout,
            json={"api_key": self.api_key, "query": query, "max_results": limit},
        )
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", "")[:400])
            for r in data.get("results", [])
        ]


class SerperProvider(SearchProvider):
    name = "serper"
    available = True

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        data = _retrying_post(
            "https://google.serper.dev/search",
            self.timeout,
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"q": query, "num": limit, "gl": "uk"},
        )
        return [
            SearchResult(r.get("title", ""), r.get("link", ""), r.get("snippet", ""))
            for r in data.get("organic", [])
        ]


class BraveSearchProvider(SearchProvider):
    name = "brave"
    available = True

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        data = _retrying_get(
            "https://api.search.brave.com/res/v1/web/search",
            self.timeout,
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            params={"q": query, "count": limit, "country": "GB"},
        )
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", ""))
            for r in (data.get("web", {}) or {}).get("results", [])
        ]
