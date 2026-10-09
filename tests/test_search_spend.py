"""Search spend: every billed attempt counts (D-704) and spend is durable mid-run (D-703).

Network-free: the HTTP providers' ``httpx.Client`` is replaced by one on an
``httpx.MockTransport``, and tenacity's back-off sleep is a no-op.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.enrich.budget import SearchBudget
from src.enrich.providers import http_providers
from src.enrich.providers.base import SearchBudgetExhaustedError
from src.enrich.providers.http_providers import BraveSearchProvider, TavilyProvider
from src.enrich.web import WebEnricher


class Upstream:
    """Answers with the queued statuses in order, then 200."""

    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(
            200,
            json={
                "results": [{"title": "Brand", "url": "https://brand.example", "content": ""}],
                "web": {"results": []},
            },
        )


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def install(statuses: list[int]) -> Upstream:
        server = Upstream(statuses)
        real_client = httpx.Client

        def client(*args: Any, **kwargs: Any) -> httpx.Client:
            return real_client(*args, transport=httpx.MockTransport(server), **kwargs)

        monkeypatch.setattr(http_providers.httpx, "Client", client)
        monkeypatch.setattr("time.sleep", lambda _s: None)  # tenacity back-off
        return server

    return install


def _enricher(provider: Any, budget: SearchBudget, settings) -> WebEnricher:  # type: ignore[no-untyped-def]
    web = WebEnricher(provider=provider, settings=settings)
    web.budget = budget
    return web


class TestRetriesAreCounted:
    def test_each_retry_is_a_billed_call(self, upstream, settings):
        server = upstream([429, 503])
        budget = SearchBudget(10)
        web = _enricher(TavilyProvider("k"), budget, settings)
        results = web._search("brand UK food brand", limit=8)
        assert results and server.requests == 3
        assert budget.calls == 3

    def test_get_providers_count_retries_too(self, upstream, settings):
        server = upstream([500])
        budget = SearchBudget(10)
        _enricher(BraveSearchProvider("k"), budget, settings)._search("q", limit=5)
        assert server.requests == 2 and budget.calls == 2

    def test_no_retry_beyond_the_budget(self, upstream, settings):
        server = upstream([429, 429, 429])
        budget = SearchBudget(2)
        web = _enricher(TavilyProvider("k"), budget, settings)
        with pytest.raises(SearchBudgetExhaustedError):
            web._search("q", limit=8)
        assert server.requests == 2, "the third attempt was not made"
        assert budget.calls == 2 and budget.exhausted

    def test_the_cap_is_a_ceiling_across_an_enrichment(self, upstream, settings):
        upstream([429, 429, 429, 429])
        budget = SearchBudget(3)
        web = _enricher(TavilyProvider("k"), budget, settings)
        web.enrich("Brand", "Brand Foods Ltd")
        web.enrich("Other", None)
        assert budget.calls <= 3

    def test_a_refused_first_attempt_is_not_counted(self, upstream, settings):
        server = upstream([])
        budget = SearchBudget(0)
        web = _enricher(TavilyProvider("k"), budget, settings)
        with pytest.raises(SearchBudgetExhaustedError):
            web._search("q", limit=8)
        assert server.requests == 0 and budget.calls == 0

    def test_a_provider_without_http_still_counts_one(self, settings):
        from src.enrich.providers.fixture import FixtureSearchProvider

        budget = SearchBudget(10)
        _enricher(FixtureSearchProvider(), budget, settings)._search("q", limit=8)
        assert budget.calls == 1


class TestOnCall:
    def test_on_call_sees_every_count(self):
        seen: list[int] = []
        budget = SearchBudget(5, on_call=seen.append)
        budget.count()
        assert budget.take_attempt()
        assert seen == [1, 2]

    def test_a_failing_on_call_does_not_break_the_run(self):
        def boom(_n: int) -> None:
            raise RuntimeError("db locked")

        budget = SearchBudget(5, on_call=boom)
        budget.count()
        assert budget.calls == 1
