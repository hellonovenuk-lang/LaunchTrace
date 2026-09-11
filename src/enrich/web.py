"""Web enrichment: is this a genuinely emerging brand, or an established one?

Only records that already survived the cheap filters reach this stage, and only
up to ``SEARCH_MAX_CANDIDATES_PER_RUN`` of them, because this is the one part of
the pipeline that costs money per record.

The rule throughout: evidence or 'unknown'.  If no website is found we record
that no website was found -- we never assert an early-stage brand on the basis
of a failed search, and we never assert a fact we did not see a URL for.

Two things follow from that, and they are the whole design of this module:

*Nothing is published without proof.*  ``src/enrich/entity_verification.py``
decides whether a domain is really the applicant's.  Until it says VERIFIED, the
customer-facing website stays blank and the guess stays internal.

*Nothing is measured over the wrong company.*  Retail presence, launch signals
and maturity are read only from the results attributed to this applicant.  A
search for a new food brand that returns a smart-heating company of the same
name now yields no maturity evidence at all -- which is the truth -- instead of
lending that company's retail footprint to this one's score.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.enrich.entity_verification import (
    DomainClass,
    EntityContext,
    EntityVerifier,
    VerificationStatus,
    get_entity_verifier,
)
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
from src.settings import Settings, get_settings, load_config

log = get_logger(__name__)

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
        self,
        provider: SearchProvider | None = None,
        settings: Settings | None = None,
        verifier: EntityVerifier | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or get_search_provider(self.settings)
        self.verifier = verifier or get_entity_verifier()
        self.calls = 0

    @property
    def available(self) -> bool:
        return self.provider.available

    def enrich(
        self,
        brand_name: str | None,
        company_name: str | None = None,
        context: EntityContext | None = None,
    ) -> WebEnrichment:
        if not self.available:
            return WebEnrichment(attempted=False, provider=self.provider.name)
        if self.calls >= self.settings.search_max_candidates_per_run:
            return WebEnrichment(
                attempted=False, provider=self.provider.name, error="search_budget_exhausted"
            )
        query_name = brand_name or company_name
        if context is not None:
            query_name = context.brand_name or query_name
        if not query_name:
            return WebEnrichment(
                attempted=False, provider=self.provider.name, error="no_brand_name"
            )
        ctx = context or EntityContext.for_brand(query_name, company_name)

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
        return self.assess(query_name, results, provider=self.provider.name, context=ctx)

    # -- assessment --------------------------------------------------------
    @staticmethod
    def assess(
        brand_name: str,
        results: list[SearchResult],
        provider: str,
        context: EntityContext | None = None,
    ) -> WebEnrichment:
        ctx = context or EntityContext.for_brand(brand_name)
        verifier = get_entity_verifier()
        enrichment = WebEnrichment(attempted=True, provider=provider, enriched_at=datetime.now(UTC))
        enrichment.evidence_urls = [r.url for r in results[:10] if r.url]

        verification = verifier.verify(results, ctx)
        enrichment.verification_status = verification.status.value
        enrichment.verification_evidence = verification.evidence
        enrichment.rejected_candidates = verification.rejected
        enrichment.candidate_website = verification.candidate_website
        enrichment.website = verification.website if verification.publishable else None

        # Only a verified domain is trusted wholesale. A merely probable one has
        # to earn each of its pages through the same evidence test as anyone
        # else's, or an unproven guess would quietly become the brand's history.
        accepted = (
            _domain_of(verification.website)
            if verification.status == VerificationStatus.VERIFIED
            else None
        )
        attributed = verifier.attribute(results, ctx, accepted)
        enrichment.attributed_urls = [r.url for r in attributed if r.url]

        if not results:
            enrichment.retail_presence = RetailPresence.NONE_FOUND
            enrichment.website_maturity = "unknown"
            return enrichment

        if not attributed:
            # Searched, found nothing provably about this applicant. Everything
            # stays unknown: 'we did not find it' must never read as 'it is new'.
            enrichment.website_maturity = "unknown"
            enrichment.retail_presence = RetailPresence.UNKNOWN
            return enrichment

        corpus = normalise_text(" ".join(f"{r.title} {r.snippet}" for r in attributed))
        raw_corpus = " ".join(f"{r.title} {r.snippet}" for r in attributed).lower()

        retailers = sorted(
            {
                r.domain
                for r in attributed
                if verifier.classify_domain(r.domain) == DomainClass.MAJOR_RETAILER
            }
        )
        marketplaces = {
            r.domain
            for r in attributed
            if verifier.classify_domain(r.domain) == DomainClass.MARKETPLACE
        }
        socials = {
            r.domain for r in attributed if verifier.classify_domain(r.domain) == DomainClass.SOCIAL
        }
        encyclopaedia = next(
            (
                r.url
                for r in attributed
                if verifier.classify_domain(r.domain) == DomainClass.ENCYCLOPAEDIA
            ),
            None,
        )
        press = {
            r.url
            for r in attributed
            if verifier.classify_domain(r.domain) == DomainClass.NEWS_OR_TRADE_PRESS
        }

        enrichment.distinct_retailers = retailers
        enrichment.encyclopaedia_entry = encyclopaedia
        enrichment.press_mentions = len(press)
        enrichment.major_retailer_presence = bool(retailers)
        enrichment.marketplace_presence = bool(marketplaces)
        enrichment.social_presence = bool(socials)

        if enrichment.website:
            enrichment.brand_description = _description_for(attributed, accepted)
            enrichment.contact_page = _contact_page(attributed, accepted)

        enrichment.products_on_sale = (
            any(p in raw_corpus for p in SHOP_PHRASES) if enrichment.website else None
        )
        enrichment.launch_evidence = sorted({p for p in LAUNCH_PHRASES if p in corpus})[:6]

        established_cfg = load_config("web_verification.json")["established_brand_evidence"]
        established_hits = [p for p in established_cfg["established_phrases"] if p in corpus]
        enrichment.established_evidence = established_hits[:6]

        if (
            enrichment.major_retailer_presence
            or len(established_hits) >= 2
            or enrichment.encyclopaedia_entry
        ):
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


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    return (urlparse(url).netloc or "").lower().removeprefix("www.") or None


def _description_for(results: list[SearchResult], domain: str | None) -> str | None:
    for r in results:
        if domain and r.domain == domain and r.snippet:
            return r.snippet.strip()[:400]
    return None


def _contact_page(results: list[SearchResult], domain: str | None) -> str | None:
    for r in results:
        if domain and r.domain == domain and any(p in r.url.lower() for p in CONTACT_PATHS):
            return r.url
    return None


def get_web_enricher(settings: Settings | None = None) -> WebEnricher:
    return WebEnricher(settings=settings)
