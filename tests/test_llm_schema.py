"""LLM output must validate before it can influence a commercial claim."""

from __future__ import annotations

import pytest

from src.classify.llm import NullLLMProvider, build_user_prompt, get_llm_provider
from src.classify.pipeline import ProductClassifier
from src.classify.schema import LLMProductClassification, parse_llm_response
from src.errors import LLMSchemaError, ProviderError
from src.models import Relevance
from tests.conftest import make_assessment, make_record

CATEGORIES = {"snacks", "cereal_bars", "biscuits_bakery", "confectionery"}


class TestParsing:
    def test_accepts_valid_json(self, llm_responses):
        parsed = parse_llm_response(llm_responses["valid"])
        assert parsed.product_category == "cereal_bars"
        assert parsed.packaging_relevance == "HIGH"

    def test_extracts_json_from_surrounding_prose(self, llm_responses):
        parsed = parse_llm_response(llm_responses["valid_with_prose"])
        assert parsed.product_category == "snacks"

    @pytest.mark.parametrize(
        "key", ["invalid_relevance", "missing_field", "out_of_range", "not_json", "empty"]
    )
    def test_rejects_anything_that_does_not_validate(self, llm_responses, key):
        with pytest.raises(LLMSchemaError):
            parse_llm_response(llm_responses[key])

    def test_rejects_a_json_array(self):
        with pytest.raises(LLMSchemaError):
            parse_llm_response("[1, 2, 3]")

    def test_normalises_category_spelling(self):
        parsed = LLMProductClassification(
            consumer_product=True,
            physical_product=True,
            food_vertical=True,
            product_category="Cereal Bars",
            packaged_product_probability=0.5,
            packaging_relevance="high",
            contract_manufacturing_relevance="High",
            distribution_relevance="HIGH",
            reasoning_summary="x",
        )
        assert parsed.product_category == "cereal_bars"
        assert parsed.packaging_relevance == "HIGH"


class TestApplication:
    def test_merges_into_the_deterministic_assessment(self, llm_responses):
        parsed = parse_llm_response(llm_responses["valid"])
        merged = parsed.apply_to(make_assessment(), CATEGORIES)
        assert merged.llm_used is True
        assert merged.classifier == "rules+llm"
        assert merged.packaging_relevance == Relevance.HIGH

    def test_rejects_a_category_outside_the_taxonomy(self):
        parsed = LLMProductClassification(
            consumer_product=True,
            physical_product=True,
            food_vertical=True,
            product_category="invented_category",
            packaged_product_probability=0.5,
            packaging_relevance="HIGH",
            contract_manufacturing_relevance="HIGH",
            distribution_relevance="HIGH",
            reasoning_summary="x",
        )
        merged = parsed.apply_to(make_assessment(product_category="snacks"), CATEGORIES)
        assert merged.product_category == "snacks"

    def test_service_only_verdict_disqualifies_the_record(self, llm_responses):
        parsed = parse_llm_response(llm_responses["service_only"])
        merged = parsed.apply_to(make_assessment(), CATEGORIES)
        assert merged.is_food_candidate is False
        assert "llm_not_packaged_food" in merged.rejection_reasons

    def test_probability_is_averaged_with_the_rules(self, llm_responses):
        parsed = parse_llm_response(llm_responses["valid"])  # 0.88
        merged = parsed.apply_to(make_assessment(packaged_product_probability=0.5), CATEGORIES)
        assert merged.packaged_product_probability == pytest.approx(0.69, abs=0.01)


class StubProvider:
    name = "stub"

    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls = 0

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TestClassifierFallback:
    def test_malformed_response_falls_back_to_rules(self, settings, llm_responses):
        provider = StubProvider(llm_responses["not_json"])
        classifier = ProductClassifier(settings, llm_provider=provider)
        outcome = classifier.classify(make_record())
        assert outcome.candidate is True
        assert outcome.assessment.llm_used is False
        assert classifier.llm_failures == 1
        assert "llm_failed" in outcome.warnings

    def test_provider_error_does_not_break_the_run(self, settings):
        classifier = ProductClassifier(settings, llm_provider=StubProvider(ProviderError("down")))
        outcome = classifier.classify(make_record())
        assert outcome.candidate is True
        assert classifier.llm_failures == 1

    def test_unexpected_exception_does_not_break_the_run(self, settings):
        classifier = ProductClassifier(settings, llm_provider=StubProvider(RuntimeError("boom")))
        outcome = classifier.classify(make_record())
        assert outcome.candidate is True

    def test_budget_limits_calls(self, settings, llm_responses):
        provider = StubProvider(llm_responses["valid"])
        limited = settings.model_copy(update={"llm_max_candidates_per_run": 2})
        classifier = ProductClassifier(limited, llm_provider=provider)
        for _ in range(5):
            classifier.classify(make_record())
        assert provider.calls == 2

    def test_no_provider_means_rules_only(self, settings):
        classifier = ProductClassifier(settings, llm_provider=NullLLMProvider())
        assert classifier.llm_available is False
        outcome = classifier.classify(make_record())
        assert outcome.assessment.classifier == "rules"


class TestProviderSelection:
    def test_missing_key_yields_the_null_provider(self, settings):
        assert get_llm_provider(settings).name == "none"

    def test_null_provider_raises_rather_than_inventing(self):
        with pytest.raises(ProviderError):
            NullLLMProvider().complete("s", "u")

    def test_prompt_states_when_goods_text_is_unavailable(self):
        prompt = build_user_prompt(
            make_record(goods_text=None, goods_text_available=False), sorted(CATEGORIES)
        )
        assert "not available in source" in prompt
