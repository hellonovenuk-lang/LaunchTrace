"""The configuration files are business rules, so their shape is tested."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models import Relevance, ScoreBand
from src.settings import CONFIG_DIR, Settings, load_config

CONFIG_FILES = [p.name for p in sorted(CONFIG_DIR.glob("*.json"))]


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_every_config_file_is_valid_json(name):
    assert isinstance(load_config(name), dict)


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_every_config_file_is_versioned(name):
    assert "$schema_version" in load_config(name)


class TestFoodTaxonomy:
    def test_primary_classes_are_food_classes(self):
        assert set(load_config("food_taxonomy.json")["primary_nice_classes"]) == {29, 30}

    def test_product_groups_have_keys_labels_and_keywords(self):
        for group in load_config("food_taxonomy.json")["product_groups"]:
            assert group["key"] and group["label"] and group["keywords"]

    def test_product_group_keys_are_unique(self):
        keys = [g["key"] for g in load_config("food_taxonomy.json")["product_groups"]]
        assert len(keys) == len(set(keys))

    def test_sic_mappings_point_at_real_product_groups(self):
        taxonomy = load_config("food_taxonomy.json")
        valid = {g["key"] for g in taxonomy["product_groups"]}
        for code, group in taxonomy["sic_code_product_groups"].items():
            if code.startswith("_") or group is None:
                continue
            assert group in valid, f"SIC {code} maps to unknown group {group}"

    def test_brand_hint_groups_are_real_product_groups(self):
        taxonomy = load_config("food_taxonomy.json")
        valid = {g["key"] for g in taxonomy["product_groups"]}
        for group in taxonomy["brand_name_category_hints"]:
            if group.startswith("_"):
                continue
            assert group in valid


class TestScoring:
    def test_bands_cover_zero_to_one_hundred_without_gaps(self):
        bands = sorted(load_config("scoring.json")["bands"], key=lambda b: b["min"])
        assert bands[0]["min"] == 0
        assert bands[-1]["max"] == 100
        for lower, upper in zip(bands, bands[1:], strict=False):
            assert upper["min"] == lower["max"] + 1

    def test_band_keys_match_the_model(self):
        for band in load_config("scoring.json")["bands"]:
            assert ScoreBand(band["key"])

    def test_positive_weights_are_positive_and_negative_are_negative(self):
        config = load_config("scoring.json")
        assert all(i["weight"] > 0 for i in config["positive_indicators"])
        assert all(i["weight"] < 0 for i in config["negative_indicators"])

    def test_indicator_keys_are_unique(self):
        config = load_config("scoring.json")
        keys = [i["key"] for i in config["positive_indicators"] + config["negative_indicators"]]
        assert len(keys) == len(set(keys))

    def test_every_indicator_has_a_reason_template(self):
        config = load_config("scoring.json")
        for indicator in config["positive_indicators"] + config["negative_indicators"]:
            assert indicator["reason_template"].strip()

    def test_a_perfect_record_can_reach_but_not_wildly_exceed_the_maximum(self):
        """Weights should be calibrated, not saturating at 100 for everything."""
        from src.score.launchtrace_score import LaunchTraceScorer

        scorer = LaunchTraceScorer()
        best = scorer._achievable_weight(set())
        total = int(load_config("scoring.json")["base_score"]) + best
        assert 95 <= total <= 115, f"best achievable score is {total}"

    def test_evidence_groups_reference_real_indicators(self):
        config = load_config("scoring.json")
        keys = {i["key"] for i in config["positive_indicators"]}
        for group, members in config["evidence_groups"].items():
            if group.startswith("_"):
                continue
            assert set(members) <= keys


class TestBuyingIntent:
    def test_bands_are_the_documented_vocabulary(self):
        config = load_config("buying_intent.json")
        assert config["relevance_bands"] == ["HIGH", "MEDIUM", "LOW", "NONE"]
        for band in config["relevance_bands"]:
            assert Relevance(band)

    def test_every_product_group_mapping_uses_known_categories(self):
        config = load_config("buying_intent.json")
        categories = {c["key"] for c in config["supplier_categories"]}
        for group, mapping in config["product_group_relevance"].items():
            assert set(mapping) <= categories, group
            assert set(mapping.values()) <= set(config["relevance_bands"])

    def test_defaults_cover_every_supplier_category(self):
        config = load_config("buying_intent.json")
        categories = {c["key"] for c in config["supplier_categories"]}
        assert set(config["default_relevance"]) == categories

    def test_phrasing_note_forbids_claiming_current_purchasing(self):
        assert "Never render" in load_config("buying_intent.json")["phrasing_note"]

    def test_every_food_product_group_has_a_mapping(self):
        taxonomy = load_config("food_taxonomy.json")
        mapping = load_config("buying_intent.json")["product_group_relevance"]
        for group in taxonomy["product_groups"]:
            assert group["key"] in mapping, f"no buying-intent mapping for {group['key']}"


class TestPlans:
    def test_prices_are_in_pence(self):
        for plan in load_config("customer_plans.json")["plans"]:
            assert plan["price_pence"] >= 1000

    def test_price_id_environment_variables_are_named(self):
        for plan in load_config("customer_plans.json")["plans"]:
            assert plan["stripe_price_id_env"].startswith("STRIPE_")


class TestValidationBands:
    def test_bands_are_ordered_and_contiguous(self):
        bands = load_config("validation_bands.json")["bands"]
        assert bands[0]["key"] == "GREEN"
        assert bands[-1]["key"] == "FAIL_RETHINK"

    def test_guardrails_are_sane(self):
        guard = load_config("validation_bands.json")["volume_guardrails"]
        assert guard["min_expected_records_per_journal"] < guard["max_expected_records_per_journal"]


class TestEnvExample:
    def test_every_setting_appears_in_env_example(self):
        example = Path(".env.example").read_text(encoding="utf-8")
        for field in Settings.model_fields.values():
            alias = field.alias
            if not alias:
                continue
            assert alias in example, f"{alias} is missing from .env.example"

    def test_env_example_contains_no_real_secret(self):
        text = Path(".env.example").read_text(encoding="utf-8")
        for marker in ("sk_live_", "rk_live_", "whsec_1", "re_live"):
            assert marker not in text


class TestNoSecretsInRepo:
    def test_no_env_file_is_committed(self):
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=False
        ).stdout.splitlines()
        assert ".env" not in tracked

    def test_config_files_contain_no_credentials(self):
        for path in CONFIG_DIR.glob("*.json"):
            text = path.read_text(encoding="utf-8").lower()
            for marker in ('api_key":', 'secret":', "password", "sk_live", "sk_test"):
                assert marker not in text, f"{path.name} may contain a credential"
