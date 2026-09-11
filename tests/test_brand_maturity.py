"""A new company is not a new brand.

The failure being regression-tested: a company incorporated months ago is scored
as an emerging brand, when the brand it fronts has been on supermarket shelves
for years. Incorporation date answers a question about the legal entity;
whether a supplier can still usefully approach it is a question about the brand.

The reverse mistake matters just as much, so a brand with a website and some
press is not suppressed for having any web presence at all.
"""

from __future__ import annotations

from src.models import BrandMaturity
from src.score.maturity import assess_brand_maturity
from tests.conftest import make_web


class TestNewCompanyGenuinelyNewBrand:
    def test_a_young_company_with_launch_signals_reads_as_emerging(self):
        web = make_web(
            attempted=True,
            website="https://newbrand.test",
            launch_evidence=["coming soon", "wholesale enquiries"],
            major_retailer_presence=False,
        )
        maturity, evidence = assess_brand_maturity(web)
        assert maturity == BrandMaturity.EMERGING
        assert evidence


class TestNewCompanyEstablishedBrand:
    def test_national_distribution_makes_the_brand_established_however_new_the_company(self):
        """Two different multiples stocking it is not an emerging brand."""
        web = make_web(
            attempted=True,
            website="https://oldbrand.test",
            major_retailer_presence=True,
            distinct_retailers=["tesco.com", "sainsburys.co.uk"],
        )
        maturity, evidence = assess_brand_maturity(web)
        assert maturity == BrandMaturity.ESTABLISHED
        assert any("retail distribution" in e for e in evidence)

    def test_established_self_description_is_enough_on_its_own(self):
        web = make_web(
            attempted=True,
            website="https://oldbrand.test",
            established_evidence=["since 19", "nationwide"],
        )
        maturity, _ = assess_brand_maturity(web)
        assert maturity == BrandMaturity.ESTABLISHED

    def test_one_retailer_plus_established_language_is_enough(self):
        web = make_web(
            attempted=True,
            website="https://oldbrand.test",
            major_retailer_presence=True,
            distinct_retailers=["waitrose.com"],
            established_evidence=["best-selling"],
        )
        maturity, _ = assess_brand_maturity(web)
        assert maturity == BrandMaturity.ESTABLISHED


class TestOlderCompanyNewBrand:
    def test_an_older_company_launching_something_new_is_still_emerging(self):
        """Age cuts both ways: a five-year-old company can launch its first brand."""
        web = make_web(
            attempted=True,
            website="https://firstproduct.test",
            launch_evidence=["pre-order", "stockists"],
            major_retailer_presence=False,
        )
        maturity, _ = assess_brand_maturity(web)
        assert maturity == BrandMaturity.EMERGING


class TestAmbiguousEvidence:
    def test_a_single_retailer_listing_is_not_yet_established(self):
        web = make_web(
            attempted=True,
            website="https://somewhere.test",
            major_retailer_presence=True,
            distinct_retailers=["waitrose.com"],
        )
        maturity, evidence = assess_brand_maturity(web)
        assert maturity != BrandMaturity.ESTABLISHED
        assert any("waitrose" in e for e in evidence)

    def test_nothing_attributable_is_unknown_rather_than_emerging(self):
        """A search that found only a namesake has told us nothing about this brand."""
        web = make_web(attempted=True, attributed_urls=[], website=None)
        maturity, evidence = assess_brand_maturity(web)
        assert maturity == BrandMaturity.UNKNOWN
        assert any("attributed" in e for e in evidence)

    def test_research_that_never_ran_is_unknown(self):
        maturity, _ = assess_brand_maturity(make_web(attempted=False))
        assert maturity == BrandMaturity.UNKNOWN

    def test_some_web_presence_alone_does_not_suppress_a_brand(self):
        """Explicitly not the rule: 'has a website' must not mean 'too late'."""
        web = make_web(attempted=True, website="https://quietbrand.test")
        maturity, _ = assess_brand_maturity(web)
        assert maturity == BrandMaturity.EMERGING
