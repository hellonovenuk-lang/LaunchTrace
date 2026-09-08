"""The packaged-food product filter and its exclusions."""

from __future__ import annotations

import pytest

from tests.conftest import make_record


class TestAcceptance:
    def test_accepts_packaged_snack_in_class_30(self, food_filter):
        outcome = food_filter.assess(make_record())
        assert outcome.candidate is True
        assert outcome.assessment.product_category == "cereal_bars"
        assert outcome.assessment.packaged_product_probability > 0.6

    def test_accepts_multi_class_food_filing(self, food_filter):
        outcome = food_filter.assess(
            make_record(nice_classes=[29, 30], goods_text="Pickles; chutney; relish; sauces.")
        )
        assert outcome.candidate is True

    def test_records_matched_keywords_as_evidence(self, food_filter):
        outcome = food_filter.assess(make_record())
        assert outcome.assessment.matched_keywords


class TestRejection:
    def test_rejects_non_food_classes(self, food_filter):
        outcome = food_filter.assess(make_record(nice_classes=[9, 42], goods_text="Software."))
        assert outcome.candidate is False
        assert outcome.rejection_reason == "no_food_class"

    def test_rejects_restaurant_services_only(self, food_filter):
        outcome = food_filter.assess(
            make_record(nice_classes=[43], goods_text="Restaurant services; cafe services.")
        )
        assert outcome.rejection_reason == "service_only_hospitality"

    def test_rejects_raw_agricultural_only(self, food_filter):
        outcome = food_filter.assess(
            make_record(nice_classes=[31], goods_text="Raw and unprocessed agricultural produce.")
        )
        assert outcome.rejection_reason == "raw_agricultural_only"

    def test_rejects_retail_services_only(self, food_filter):
        outcome = food_filter.assess(
            make_record(nice_classes=[35], goods_text="Retail services connected with foodstuffs.")
        )
        assert outcome.rejection_reason == "service_only_retail"

    def test_rejects_excluded_goods_keyword(self, food_filter):
        outcome = food_filter.assess(
            make_record(nice_classes=[30], goods_text="Pet food; animal feed; fodder.")
        )
        assert outcome.rejection_reason == "excluded_goods_keyword"

    def test_rejects_major_brand_owner(self, food_filter):
        outcome = food_filter.assess(make_record(applicant_name="Nestle UK Ltd"))
        assert outcome.rejection_reason == "major_brand_owner"

    def test_rejects_non_uk_applicant(self, food_filter):
        outcome = food_filter.assess(make_record(applicant_country="United States"))
        assert outcome.rejection_reason == "non_uk_applicant"

    def test_supporting_class_alone_is_not_enough(self, food_filter):
        outcome = food_filter.assess(
            make_record(nice_classes=[32], goods_text="Soft drinks; mineral water.")
        )
        assert outcome.rejection_reason == "supporting_class_only"

    def test_unknown_country_is_not_a_rejection(self, food_filter):
        outcome = food_filter.assess(make_record(applicant_country=None))
        assert outcome.candidate is True


class TestMajorBrandDetection:
    @pytest.mark.parametrize(
        "name",
        [
            "Nestle UK Ltd",
            "PepsiCo Inc",
            "Tesco Stores Limited",
            "KP Snacks Limited",
            "Mondelez UK",
        ],
    )
    def test_detects_known_major_brands(self, food_filter, name):
        assert food_filter.is_major_brand_owner(name) is not None

    @pytest.mark.parametrize("name", ["Crumbledge Foods Ltd", "Moorfoot Provisions Limited"])
    def test_does_not_flag_small_companies(self, food_filter, name):
        assert food_filter.is_major_brand_owner(name) is None

    def test_does_not_match_a_substring_inside_a_word(self, food_filter):
        # "mars" must not match "Marsden".
        assert food_filter.is_major_brand_owner("Marsden Bakery Ltd") is None


class TestApplicantShape:
    @pytest.mark.parametrize(
        "name",
        ["Crumbledge Foods Ltd", "Moorfoot Provisions Limited", "Some Co LLP", "A Brand PLC"],
    )
    def test_recognises_corporate_applicants(self, food_filter, name):
        assert food_filter.looks_corporate(name) is True

    @pytest.mark.parametrize("name", ["Jane Smith", "Mr Kwan Hun Au", "Jumana Kapadia"])
    def test_recognises_natural_persons(self, food_filter, name):
        assert food_filter.looks_corporate(name) is False


class TestPackagedProbability:
    def test_missing_goods_text_lowers_confidence(self, food_filter):
        with_text = food_filter.assess(make_record()).assessment.packaged_product_probability
        without = food_filter.assess(
            make_record(goods_text=None, goods_text_available=False)
        ).assessment.packaged_product_probability
        assert without < with_text

    def test_broad_class_profile_lowers_confidence(self, food_filter):
        narrow = food_filter.assess(make_record()).assessment.packaged_product_probability
        broad = food_filter.assess(
            make_record(nice_classes=[29, 30, 31, 32, 33, 35, 39, 41, 43])
        ).assessment.packaged_product_probability
        assert broad < narrow
