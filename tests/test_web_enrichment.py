"""Web enrichment: evidence or 'unknown', never inference from silence."""

from __future__ import annotations

from src.enrich.entity_verification import EntityContext
from src.enrich.providers import FixtureSearchProvider, NullSearchProvider, SearchResult
from src.enrich.web import WebEnricher, get_search_provider
from src.models import LaunchStage, RetailPresence
from src.score.maturity import assess_launch_stage
from tests.conftest import make_company, make_web


class TestProviderSelection:
    def test_no_key_means_no_provider(self, settings):
        assert (
            get_search_provider(settings.model_copy(update={"search_provider": "none"})).available
            is False
        )

    def test_provider_without_a_key_falls_back_to_null(self, settings):
        provider = get_search_provider(
            settings.model_copy(update={"search_provider": "tavily", "search_api_key": ""})
        )
        assert isinstance(provider, NullSearchProvider)

    def test_fixture_provider_needs_no_key(self, settings):
        assert get_search_provider(settings).name == "fixture"


class TestAssessment:
    def test_finds_the_official_website(self, web_enricher):
        result = web_enricher.enrich("CRUMBLEDGE")
        assert result.website == "https://crumbledge.co.uk"
        assert result.contact_page == "https://crumbledge.co.uk/contact"

    def test_detects_early_stage_signals(self, web_enricher):
        result = web_enricher.enrich("CRUMBLEDGE")
        assert result.website_maturity == "early_stage"
        assert "coming soon" in result.launch_evidence

    def test_detects_an_established_brand(self, web_enricher):
        result = web_enricher.enrich("GOLDCREST CRISPS")
        assert result.major_retailer_presence is True
        assert result.website_maturity == "established"
        assert result.retail_presence == RetailPresence.MULTIPLE_RETAIL

    def test_detects_marketplace_presence(self, web_enricher):
        result = web_enricher.enrich("HOT CLOUD")
        assert result.marketplace_presence is True
        assert result.retail_presence == RetailPresence.MARKETPLACE

    def test_ignores_directory_and_registry_domains_as_the_official_site(self):
        enrichment = WebEnricher.assess(
            "NIBBLY",
            [
                SearchResult(
                    "Nibbly Ltd — Companies House",
                    "https://find-and-update.company-information.service.gov.uk/company/1",
                    "",
                ),
                SearchResult(
                    "Nibbly", "https://nibbly.co.uk/", "Our story. Nibbly Ltd, Bristol snacks."
                ),
            ],
            provider="test",
            context=EntityContext(
                brand_name="NIBBLY",
                applicant_name="Nibbly Ltd",
                company_name="NIBBLY LTD",
                post_town="BRISTOL",
            ),
        )
        assert enrichment.website == "https://nibbly.co.uk"
        assert any("register listing" in r for r in enrichment.rejected_candidates)

    def test_keeps_evidence_urls(self, web_enricher):
        assert web_enricher.enrich("CRUMBLEDGE").evidence_urls

    def test_no_results_is_recorded_as_none_found_not_early_stage(self):
        enrichment = WebEnricher.assess("UNKNOWN BRAND", [], provider="test")
        assert enrichment.website is None
        assert enrichment.retail_presence == RetailPresence.NONE_FOUND
        assert enrichment.website_maturity == "unknown"


class TestBudgetAndFailure:
    def test_unavailable_provider_marks_the_record_as_not_attempted(self, settings):
        enricher = WebEnricher(provider=NullSearchProvider(), settings=settings)
        result = enricher.enrich("ANYTHING")
        assert result.attempted is False

    def test_budget_is_enforced(self, settings):
        limited = settings.model_copy(update={"search_max_candidates_per_run": 2})
        enricher = WebEnricher(provider=FixtureSearchProvider(), settings=limited)
        outcomes = [enricher.enrich(f"BRAND {i}") for i in range(4)]
        assert sum(1 for o in outcomes if o.attempted) == 2
        assert outcomes[-1].error == "search_budget_exhausted"

    def test_a_provider_exception_does_not_propagate(self, settings):
        class Broken(NullSearchProvider):
            available = True

            def search(self, query, limit=8):  # type: ignore[no-untyped-def]
                raise RuntimeError("provider down")

        result = WebEnricher(provider=Broken(), settings=settings).enrich("ANY")
        assert result.attempted is True
        assert result.error


class TestLaunchStage:
    def test_established_when_stocked_by_multiples(self):
        web = make_web(attempted=True, major_retailer_presence=True)
        assert assess_launch_stage(make_company(), web, 0.5) == LaunchStage.ESTABLISHED

    def test_pre_launch_when_a_site_exists_but_nothing_is_for_sale(self):
        web = make_web(
            attempted=True,
            website="https://x.test",
            website_maturity="early_stage",
            launch_evidence=["coming soon"],
            major_retailer_presence=False,
        )
        assert assess_launch_stage(make_company(), web, 0.5) == LaunchStage.PRE_LAUNCH

    def test_scaling_when_on_a_marketplace(self):
        web = make_web(
            attempted=True,
            website="https://x.test",
            website_maturity="early_stage",
            retail_presence=RetailPresence.MARKETPLACE,
            major_retailer_presence=False,
        )
        assert assess_launch_stage(make_company(), web, 1.0) == LaunchStage.SCALING

    def test_unknown_when_no_research_was_done(self):
        assert (
            assess_launch_stage(make_company(), make_web(attempted=False), 0.5)
            == LaunchStage.UNKNOWN
        )

    def test_company_age_alone_is_not_a_launch_stage(self):
        """A young company we never researched is 'unknown', not 'pre-launch'."""
        assert (
            assess_launch_stage(make_company(), make_web(attempted=False), 0.1)
            == LaunchStage.UNKNOWN
        )
