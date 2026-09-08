"""Web enrichment: is this a genuinely emerging brand, or an established one?

Only records that already survived the cheap filters reach this stage, and only
up to ``SEARCH_MAX_CANDIDATES_PER_RUN`` of them, because this is the one part of
the pipeline that costs money per record.

The rule throughout: evidence or 'unknown'.  If no website is found we record
that no website was found -- we never assert an early-stage brand on the basis
of a failed search, and we never assert a fact we did not see a URL for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from src.enrich.providers import (
    BraveSearchProvider,
    FixtureSearchProvider,
    NullSearchProvider,
    SearchProvider,
    SearchResult,
    SerperProvider,
    TavilyProvider,
)
from src.logging_setup import get_logger
from src.models import RetailPresence, WebEnrichment
from src.parse.normalise import normalise_text
from src.settings import Settings, get_settings

log = get_logger(__name__)

MAJOR_RETAILER_DOMAINS = {
    "tesco.com",
    "sainsburys.co.uk",
    "asda.com",
    "morrisons.com",
    "waitrose.com",
    "ocado.com",
    "aldi.co.uk",
    "lidl.co.uk",
    "coop.co.uk",
    "iceland.co.uk",
    "marksandspencer.com",
    "boots.com",
    "hollandandbarrett.com",
    "wholefoodsmarket.com",
    "costco.co.uk",
    "bmstores.co.uk",
    "poundland.co.uk",
    "spar.co.uk",
    "budgens.co.uk",
}
MARKETPLACE_DOMAINS = {
    "amazon.co.uk",
    "amazon.com",
    "ebay.co.uk",
    "etsy.com",
    "notonthehighstreet.com",
    "ocadoretail.com",
    "thewhiskyexchange.com",
    "faire.com",
    "ankorstore.com",
}
SOCIAL_DOMAINS = {
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "youtube.com",
}
DIRECTORY_DOMAINS = {
    "companieshouse.gov.uk",
    "find-and-update.company-information.service.gov.uk",
    "endole.co.uk",
    "companycheck.co.uk",
    "opencorporates.com",
    "bizdb.co.uk",
    "ipo.gov.uk",
    "trademarks.ipo.gov.uk",
    "tmdn.org",
    "wipo.int",
    "crunchbase.com",
    "bloomberg.com",
    "dnb.com",
    "yell.com",
    "192.com",
    "wikipedia.org",
}
LAUNCH_PHRASES = (
    "coming soon",
    "launching soon",
    "pre-order",
    "preorder",
    "new brand",
    "we launched",
    "founded in",
    "our story",
    "crowdfunding",
    "crowdfunder",
    "kickstarter",
    "seedrs",
    "stockists",
    "wholesale enquiries",
    "trade enquiries",
    "sample pack",
)
ESTABLISHED_PHRASES = (
    "since 18",
    "since 19",
    "nationwide",
    "available in over",
    "our factories",
    "global brand",
    "worldwide",
    "annual revenue",
    "plc",
    "distributors in",
)
SHOP_PHRASES = ("add to basket", "add to cart", "shop now", "buy now", "our shop", "£")
CONTACT_PATHS = ("/contact", "/contact-us", "/get-in-touch", "/wholesale", "/trade", "/stockists")


def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    settings = settings or get_settings()
    provider = settings.search_provider
    if provider == "fixture":
        return FixtureSearchProvider()
    if not settings.search_api_key:
        return NullSearchProvider()
    if provider == "tavily":
        return TavilyProvider(settings.search_api_key, settings.search_timeout_seconds)
    if provider == "serper":
        return SerperProvider(settings.search_api_key, settings.search_timeout_seconds)
    if provider == "brave":
        return BraveSearchProvider(settings.search_api_key, settings.search_timeout_seconds)
    return NullSearchProvider()


class WebEnricher:
    def __init__(
        self, provider: SearchProvider | None = None, settings: Settings | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or get_search_provider(self.settings)
        self.calls = 0

    @property
    def available(self) -> bool:
        return self.provider.available

    def enrich(self, brand_name: str | None, company_name: str | None = None) -> WebEnrichment:
        if not self.available:
            return WebEnrichment(attempted=False, provider=self.provider.name)
        if self.calls >= self.settings.search_max_candidates_per_run:
            return WebEnrichment(
                attempted=False, provider=self.provider.name, error="search_budget_exhausted"
            )
        query_name = brand_name or company_name
        if not query_name:
            return WebEnrichment(
                attempted=False, provider=self.provider.name, error="no_brand_name"
            )

        self.calls += 1
        try:
            results = self.provider.search(f"{query_name} UK food brand", limit=8)
            if company_name and company_name.lower() != (brand_name or "").lower():
                results += self.provider.search(f'"{company_name}" products', limit=5)
        except Exception as exc:
            log.warning("web.search_failed", brand=query_name[:80], error=str(exc)[:200])
            return WebEnrichment(
                attempted=True,
                provider=self.provider.name,
                error=str(exc)[:400],
                enriched_at=datetime.now(UTC),
            )
        return self.assess(query_name, results, provider=self.provider.name)

    # -- assessment --------------------------------------------------------
    @staticmethod
    def assess(brand_name: str, results: list[SearchResult], provider: str) -> WebEnrichment:
        enrichment = WebEnrichment(attempted=True, provider=provider, enriched_at=datetime.now(UTC))
        if not results:
            enrichment.retail_presence = RetailPresence.NONE_FOUND
            enrichment.website_maturity = "unknown"
            enrichment.launch_evidence = []
            return enrichment

        brand_tokens = set(normalise_text(brand_name).split())
        corpus = " ".join(f"{r.title} {r.snippet}" for r in results).lower()
        enrichment.evidence_urls = [r.url for r in results[:10] if r.url]

        official: SearchResult | None = None
        for r in results:
            domain = r.domain
            if not domain or domain in DIRECTORY_DOMAINS or domain in SOCIAL_DOMAINS:
                continue
            if domain in MAJOR_RETAILER_DOMAINS or domain in MARKETPLACE_DOMAINS:
                continue
            stem = domain.split(".")[0]
            if brand_tokens and any(t in stem or stem in t for t in brand_tokens if len(t) > 2):
                official = r
                break
        if official is None:
            for r in results:
                d = r.domain
                if (
                    d
                    and d not in DIRECTORY_DOMAINS
                    and d not in SOCIAL_DOMAINS
                    and d not in MAJOR_RETAILER_DOMAINS
                    and d not in MARKETPLACE_DOMAINS
                ):
                    official = r
                    break

        if official:
            parsed = urlparse(official.url)
            enrichment.website = (
                f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else official.url
            )
            enrichment.brand_description = (official.snippet or "").strip()[:400] or None
            for r in results:
                if r.domain == official.domain and any(p in r.url.lower() for p in CONTACT_PATHS):
                    enrichment.contact_page = r.url
                    break

        enrichment.major_retailer_presence = any(
            r.domain in MAJOR_RETAILER_DOMAINS for r in results
        )
        enrichment.marketplace_presence = any(r.domain in MARKETPLACE_DOMAINS for r in results)
        enrichment.social_presence = any(r.domain in SOCIAL_DOMAINS for r in results)
        enrichment.products_on_sale = (
            any(p in corpus for p in SHOP_PHRASES) if enrichment.website else None
        )
        enrichment.launch_evidence = sorted({p for p in LAUNCH_PHRASES if p in corpus})[:6]

        established_hits = [p for p in ESTABLISHED_PHRASES if p in corpus]
        if enrichment.major_retailer_presence or len(established_hits) >= 2:
            enrichment.website_maturity = "established"
        elif enrichment.website and (enrichment.launch_evidence or not established_hits):
            enrichment.website_maturity = "early_stage"
        else:
            enrichment.website_maturity = "unknown"

        if enrichment.major_retailer_presence:
            enrichment.retail_presence = RetailPresence.MULTIPLE_RETAIL
        elif enrichment.marketplace_presence:
            enrichment.retail_presence = RetailPresence.MARKETPLACE
        elif enrichment.products_on_sale:
            enrichment.retail_presence = RetailPresence.DIRECT_ONLY
        elif enrichment.website:
            enrichment.retail_presence = RetailPresence.NONE_FOUND
        else:
            enrichment.retail_presence = RetailPresence.UNKNOWN
        return enrichment


def get_web_enricher(settings: Settings | None = None) -> WebEnricher:
    return WebEnricher(settings=settings)
