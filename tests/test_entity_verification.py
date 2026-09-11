"""Website/entity verification: verified website, or blank.

Every case here is a class of failure, not a named brand. The examples are
shaped like the records that went wrong -- a short generic brand name whose
domain is owned by a large unrelated company, an encyclopaedia subdomain, a
trade-press article, a similar-but-different brand -- because fixing those
particular names would fix nothing.
"""

from __future__ import annotations

import pytest

from src.enrich.entity_verification import (
    DomainClass,
    EntityContext,
    EntityVerifier,
    VerificationStatus,
)
from src.enrich.providers import SearchResult
from src.enrich.web import WebEnricher


@pytest.fixture
def verifier() -> EntityVerifier:
    return EntityVerifier()


def ctx(**overrides) -> EntityContext:  # type: ignore[no-untyped-def]
    base = {
        "brand_name": "CRUMBLEDGE",
        "applicant_name": "Crumbledge Foods Ltd",
        "company_name": "CRUMBLEDGE FOODS LTD",
        "company_number": "14000001",
        "post_town": "BRISTOL",
        "product_terms": ("oat bar", "cereal bars"),
    }
    base.update(overrides)
    return EntityContext(**base)  # type: ignore[arg-type]


class TestDomainClassification:
    def test_a_subdomain_cannot_smuggle_an_encyclopaedia_past_the_list(self, verifier):
        """The original bug: 'wikipedia.org' was matched exactly, so 'en.' slipped by."""
        assert verifier.classify_domain("en.wikipedia.org") == DomainClass.ENCYCLOPAEDIA
        assert verifier.classify_domain("wikipedia.org") == DomainClass.ENCYCLOPAEDIA

    def test_trade_publications_are_not_company_websites(self, verifier):
        assert verifier.classify_domain("just-food.com") == DomainClass.NEWS_OR_TRADE_PRESS
        assert verifier.classify_domain("thegrocer.co.uk") == DomainClass.NEWS_OR_TRADE_PRESS

    def test_social_profiles_are_not_company_websites(self, verifier):
        assert verifier.classify_domain("instagram.com") == DomainClass.SOCIAL

    def test_marketplaces_and_retailers_are_classified_separately(self, verifier):
        assert verifier.classify_domain("amazon.co.uk") == DomainClass.MARKETPLACE
        assert verifier.classify_domain("tesco.com") == DomainClass.MAJOR_RETAILER

    def test_an_ordinary_domain_is_a_candidate(self, verifier):
        assert verifier.classify_domain("crumbledge.co.uk") == DomainClass.CANDIDATE

    def test_a_deep_linked_page_is_somebody_elses_site(self, verifier):
        """Catches the publications no hand-written list will ever cover."""
        assert verifier.looks_like_an_article(
            "https://unknown-trade-title.test/news/brand-x-launch"
        )
        assert verifier.looks_like_an_article("https://someblog.test/2026/09/a-new-brand")
        assert not verifier.looks_like_an_article("https://crumbledge.co.uk/")
        assert not verifier.looks_like_an_article("https://crumbledge.co.uk/about")


class TestDistinctiveness:
    def test_a_short_common_word_proves_nothing(self, verifier):
        assert verifier.is_distinctive("HIVE") is False
        assert verifier.is_distinctive("NEST") is False

    def test_a_long_common_word_still_proves_nothing(self, verifier):
        assert verifier.is_distinctive("CHOCOLATE") is False

    def test_a_coined_word_is_distinctive(self, verifier):
        assert verifier.is_distinctive("CRUMBLEDGE") is True

    def test_a_multi_word_brand_is_distinctive(self, verifier):
        assert verifier.is_distinctive("HOT CLOUD") is True


class TestRefusingToGuess:
    def test_a_generic_brand_name_does_not_claim_a_big_companys_domain(self, verifier):
        """A short dictionary-word brand whose .com belongs to an unrelated company.

        The domain matches the brand exactly, which is precisely why the old
        code published it. It is still not this applicant's website.
        """
        results = [
            SearchResult(
                "Hive | Smart Thermostats and Home Heating Controls",
                "https://www.hive.com/",
                "Control your heating from your phone with Hive Active Heating.",
            ),
            SearchResult(
                "Hive Home Support",
                "https://www.hive.com/support",
                "Help with your smart thermostat and heating schedule.",
            ),
        ]
        out = verifier.verify(
            results,
            ctx(
                brand_name="HIVE",
                company_name="HIVE FOODS LTD",
                applicant_name="Hive Foods Ltd",
                company_number="15000002",
            ),
        )
        assert out.status != VerificationStatus.VERIFIED
        assert out.website is None
        assert out.candidate_website  # kept internally so the decision can be audited

    def test_an_encyclopaedia_entry_is_never_the_website(self, verifier):
        results = [
            SearchResult(
                "Nori - Wikipedia",
                "https://en.wikipedia.org/wiki/Nori",
                "Nori is a dried edible seaweed used in Japanese cuisine.",
            )
        ]
        out = verifier.verify(
            results, ctx(brand_name="NORI KITCHEN", company_name="NORI KITCHEN LTD")
        )
        assert out.website is None
        assert any("encyclopaedia" in r for r in out.rejected)

    def test_a_trade_publication_article_is_never_the_website(self, verifier):
        results = [
            SearchResult(
                "Brand X rolls out new range - Just Food",
                "https://www.just-food.com/news/brand-x-rolls-out-new-range/",
                "The company said the launch would extend its dairy alternatives range.",
            )
        ]
        out = verifier.verify(results, ctx(brand_name="WUNDA", company_name="WUNDA FOODS LTD"))
        assert out.website is None
        assert out.rejected

    def test_a_certification_body_sharing_one_word_is_not_the_brand(self, verifier):
        """A shared token is not an identity. The domain must be the brand."""
        results = [
            SearchResult(
                "Demeter International - Biodynamic Certification",
                "https://www.demeter.net/",
                "Demeter is the certification organisation for biodynamic agriculture.",
            )
        ]
        out = verifier.verify(
            results, ctx(brand_name="ST.DEMETER", company_name="ST DEMETER LTD", post_town="LEEDS")
        )
        assert out.status != VerificationStatus.VERIFIED
        assert out.website is None

    def test_a_similar_brand_on_a_similar_domain_is_rejected(self, verifier):
        """Two real food brands sharing a word: only the exact one is published."""
        results = [
            SearchResult(
                "Fatboy Ice Cream - Novelty Frozen Treats",
                "https://www.fatboyicecream.com/",
                "Fatboy ice cream sandwiches and frozen novelties.",
            ),
            SearchResult(
                "Fatboy's Cocoa | Small-batch drinking chocolate",
                "https://fatboyscocoa.co.uk/",
                "Fatboy's Cocoa Ltd, Sheffield. Small batch drinking chocolate. Stockists.",
            ),
        ]
        out = verifier.verify(
            results,
            ctx(
                brand_name="FATBOY'S COCOA",
                applicant_name="Fatboy's Cocoa Ltd",
                company_name="FATBOY'S COCOA LTD",
                post_town="SHEFFIELD",
                product_terms=("drinking chocolate", "cocoa"),
            ),
        )
        assert out.status == VerificationStatus.VERIFIED
        assert out.website == "https://fatboyscocoa.co.uk"

    def test_a_social_profile_is_not_an_official_website(self, verifier):
        results = [
            SearchResult(
                "Crumbledge (@crumbledge) · Instagram",
                "https://instagram.com/crumbledge",
                "New British oat bar brand from Crumbledge Foods Ltd, Bristol.",
            )
        ]
        out = verifier.verify(results, ctx())
        assert out.website is None
        assert any("social media" in r for r in out.rejected)

    def test_a_marketplace_listing_is_not_an_official_website(self, verifier):
        results = [
            SearchResult(
                "Crumbledge Oat Bars 12 pack",
                "https://www.amazon.co.uk/dp/B0EXAMPLE",
                "Crumbledge Foods Ltd oat bars, Bristol.",
            )
        ]
        out = verifier.verify(results, ctx())
        assert out.website is None

    def test_nothing_at_all_is_unverified_not_a_guess(self, verifier):
        out = verifier.verify([], ctx())
        assert out.status == VerificationStatus.UNVERIFIED
        assert out.website is None


class TestAcceptingRealEvidence:
    def test_the_brands_own_site_is_published_when_the_company_appears_on_it(self, verifier):
        results = [
            SearchResult(
                "Crumbledge — oat bars made in Bristol",
                "https://crumbledge.co.uk/",
                "Our story: Crumbledge Foods Ltd launched in 2025 with a single oat bar.",
            ),
            SearchResult(
                "Contact — Crumbledge", "https://crumbledge.co.uk/contact", "Trade enquiries."
            ),
        ]
        out = verifier.verify(results, ctx())
        assert out.status == VerificationStatus.VERIFIED
        assert out.website == "https://crumbledge.co.uk"

    def test_the_consumer_brand_need_not_resemble_the_legal_company_name(self, verifier):
        """The common, legitimate case: a holding company behind a consumer brand.

        Requiring the domain to match the registered name would throw most real
        opportunities away, so the tie is made on other evidence instead.
        """
        results = [
            SearchResult(
                "Hot Cloud — small batch chilli sauce",
                "https://hotcloud.co.uk/",
                "Made in Manchester. A brand of Pennine Ferments Ltd. Wholesale enquiries.",
            )
        ]
        out = verifier.verify(
            results,
            ctx(
                brand_name="HOT CLOUD",
                applicant_name="Pennine Ferments Ltd",
                company_name="PENNINE FERMENTS LTD",
                company_number="15111222",
                post_town="MANCHESTER",
                product_terms=("chilli sauce", "hot sauce"),
            ),
        )
        assert out.status == VerificationStatus.VERIFIED
        assert out.website == "https://hotcloud.co.uk"

    def test_a_company_number_on_the_page_settles_it(self, verifier):
        results = [
            SearchResult(
                "Zephyr Larder",
                "https://zephyrlarder.co.uk/",
                "Zephyr Larder. Registered in England, company number 15111222.",
            )
        ]
        out = verifier.verify(
            results,
            ctx(
                brand_name="ZEPHYR",
                applicant_name="Cold Store Brands Ltd",
                company_name="COLD STORE BRANDS LTD",
                company_number="15111222",
                post_town="DERBY",
            ),
        )
        assert out.status == VerificationStatus.VERIFIED

    def test_two_corroborated_domains_are_conflicting_rather_than_a_coin_toss(self, verifier):
        results = [
            SearchResult(
                "Crumbledge Foods Ltd — Bristol",
                "https://crumbledge.co.uk/",
                "Crumbledge Foods Ltd, Bristol. Oat bars.",
            ),
            SearchResult(
                "Crumbledge Foods Ltd",
                "https://crumbledgefoods.com/",
                "Crumbledge Foods Ltd of Bristol. Cereal bars and snacks.",
            ),
        ]
        out = verifier.verify(results, ctx())
        assert out.status == VerificationStatus.CONFLICTING
        assert out.website is None


class TestAttribution:
    def test_an_unrelated_companys_pages_are_not_evidence_about_this_brand(self, verifier):
        results = [
            SearchResult(
                "Hive | Smart Thermostats",
                "https://www.hive.com/",
                "Control your heating from your phone.",
            ),
            SearchResult(
                "Hive thermostat - Tesco",
                "https://www.tesco.com/groceries/en-GB/products/000",
                "Hive smart thermostat starter kit.",
            ),
        ]
        attributed = verifier.attribute(
            results, ctx(brand_name="HIVE", company_name="HIVE FOODS LTD"), None
        )
        assert attributed == []

    def test_a_retail_listing_for_this_brand_is_evidence(self, verifier):
        results = [
            SearchResult(
                "Crumbledge Oat Bars | Sainsbury's",
                "https://www.sainsburys.co.uk/gol-ui/product/crumbledge",
                "Crumbledge oat bars, made by Crumbledge Foods Ltd.",
            )
        ]
        assert verifier.attribute(results, ctx(), None)


class TestEnrichmentUsesOnlyAttributedEvidence:
    def test_an_unrelated_namesake_produces_no_maturity_signal_at_all(self):
        """The scoring bug behind the display bug.

        The old code read retail presence and launch signals off whatever the
        search returned, so an unrelated company's footprint moved this record's
        band. Now an unattributable search yields unknown, everywhere.
        """
        enrichment = WebEnricher.assess(
            "HIVE",
            [
                SearchResult(
                    "Hive | Smart Thermostats",
                    "https://www.hive.com/",
                    "Smart heating controls. Available nationwide. Shop now.",
                ),
                SearchResult(
                    "Hive thermostat | Tesco",
                    "https://www.tesco.com/groceries/en-GB/products/111",
                    "Hive smart thermostat.",
                ),
            ],
            provider="test",
            context=ctx(brand_name="HIVE", company_name="HIVE FOODS LTD", product_terms=()),
        )
        assert enrichment.website is None
        assert enrichment.entity_evidence_available is False
        assert enrichment.major_retailer_presence is None
        assert enrichment.website_maturity == "unknown"
        assert enrichment.launch_evidence == []

    def test_attributed_evidence_still_produces_a_normal_assessment(self):
        enrichment = WebEnricher.assess(
            "CRUMBLEDGE",
            [
                SearchResult(
                    "Crumbledge — oat bars made in Bristol",
                    "https://crumbledge.co.uk/",
                    "Our story: Crumbledge Foods Ltd, Bristol. Coming soon. Wholesale enquiries.",
                )
            ],
            provider="test",
            context=ctx(),
        )
        assert enrichment.website == "https://crumbledge.co.uk"
        assert enrichment.entity_evidence_available is True
        assert enrichment.major_retailer_presence is False
        assert "coming soon" in enrichment.launch_evidence
