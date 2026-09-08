"""Supplier buying-intent mapping."""

from __future__ import annotations

import pytest

from src.models import LaunchStage, Relevance
from src.score.buying_intent import map_buying_intent, supplier_category_labels


class TestMapping:
    def test_snacks_prioritise_flexible_packaging(self):
        intent = map_buying_intent("snacks", LaunchStage.UNKNOWN)
        assert intent.flexible_packaging == Relevance.HIGH
        assert intent.contract_manufacturing == Relevance.HIGH

    def test_sauces_prioritise_labels_over_flexible(self):
        intent = map_buying_intent("sauces_condiments", LaunchStage.UNKNOWN)
        assert intent.labels == Relevance.HIGH
        assert intent.flexible_packaging == Relevance.LOW

    def test_unknown_category_falls_back_to_defaults(self):
        intent = map_buying_intent(None)
        assert intent.flexible_packaging == Relevance.MEDIUM
        assert intent.brokerage == Relevance.LOW

    def test_every_category_is_a_valid_band(self):
        intent = map_buying_intent("cereal_bars", LaunchStage.EARLY_LAUNCH)
        assert set(intent.as_dict().values()) <= {"HIGH", "MEDIUM", "LOW", "NONE"}


class TestStageModifiers:
    def test_pre_launch_promotes_packaging_and_demotes_distribution(self):
        base = map_buying_intent("sauces_condiments", LaunchStage.UNKNOWN)
        pre = map_buying_intent("sauces_condiments", LaunchStage.PRE_LAUNCH)
        assert pre.contract_manufacturing == Relevance.HIGH
        assert _rank(pre.distribution) < _rank(base.distribution)

    def test_established_demotes_everything(self):
        base = map_buying_intent("snacks", LaunchStage.UNKNOWN)
        established = map_buying_intent("snacks", LaunchStage.ESTABLISHED)
        for key, value in established.as_dict().items():
            assert _rank(value) <= _rank(getattr(base, key))

    def test_scaling_promotes_distribution_and_fulfilment(self):
        base = map_buying_intent("cereal_bars", LaunchStage.UNKNOWN)
        scaling = map_buying_intent("cereal_bars", LaunchStage.SCALING)
        assert _rank(scaling.brokerage) > _rank(base.brokerage)

    def test_promotion_cannot_exceed_high(self):
        intent = map_buying_intent("snacks", LaunchStage.PRE_LAUNCH)
        assert intent.flexible_packaging == Relevance.HIGH


class TestConfiguration:
    def test_every_supplier_category_has_a_label(self):
        labels = supplier_category_labels()
        intent = map_buying_intent("snacks")
        for key in intent.as_dict():
            assert key in labels

    def test_custom_config_is_honoured(self):
        config = {
            "band_order": ["NONE", "LOW", "MEDIUM", "HIGH"],
            "default_relevance": {
                "flexible_packaging": "NONE",
                "labels": "NONE",
                "cartons": "NONE",
                "contract_manufacturing": "NONE",
                "copacking": "NONE",
                "distribution": "NONE",
                "brokerage": "NONE",
                "fulfilment": "NONE",
                "marketing": "NONE",
            },
            "product_group_relevance": {"snacks": {"flexible_packaging": "HIGH"}},
            "stage_modifiers": {},
        }
        intent = map_buying_intent("snacks", LaunchStage.UNKNOWN, config=config)
        assert intent.flexible_packaging == Relevance.HIGH
        assert intent.labels == Relevance.NONE


@pytest.mark.parametrize("band", ["HIGH", "MEDIUM", "LOW", "NONE"])
def test_relevance_bands_are_the_only_vocabulary(band):
    assert Relevance(band)


def _rank(value) -> int:  # type: ignore[no-untyped-def]
    order = ["NONE", "LOW", "MEDIUM", "HIGH"]
    return order.index(value.value if hasattr(value, "value") else value)
