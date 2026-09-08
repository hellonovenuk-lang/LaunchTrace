"""Deterministic packaged-food product filter.

Runs before anything that costs money.  Being in Nice class 29 or 30 is not by
itself treated as a lead: the filter also looks for exclusions (service-only
filings, raw agricultural goods, pet food, alcohol), for a specific product
group, and for signals that the applicant is a major brand owner or filing a
portfolio rather than launching a product.

All thresholds and word lists live in ``config/food_taxonomy.json`` and
``config/exclusions.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models import ProductAssessment, Relevance, TrademarkRecord
from src.parse.normalise import normalise_text
from src.settings import load_config


@dataclass
class FilterOutcome:
    candidate: bool
    assessment: ProductAssessment
    rejection_reason: str | None = None
    rejection_detail: str | None = None
    warnings: list[str] = field(default_factory=list)


class FoodFilter:
    def __init__(self, taxonomy: dict | None = None, exclusions: dict | None = None) -> None:
        self.taxonomy = taxonomy or load_config("food_taxonomy.json")
        self.exclusions = exclusions or load_config("exclusions.json")
        self.primary = set(self.taxonomy["primary_nice_classes"])
        self.supporting = set(self.taxonomy["supporting_nice_classes"])
        self.context_only = set(self.taxonomy["context_only_nice_classes"])
        self.groups = self.taxonomy["product_groups"]
        self.major_brands = [b.lower() for b in self.exclusions["major_brand_owners"]]
        self.goods_exclusions = [
            k.lower() for k in self.exclusions["goods_text_exclusion_keywords"]
        ]
        self.uk_values = {v.lower() for v in self.exclusions["uk_country_values"]}
        self.person_cfg = self.exclusions["natural_person_applicant"]

    # -- helpers -----------------------------------------------------------
    def is_major_brand_owner(self, applicant_name: str | None) -> str | None:
        n = normalise_text(applicant_name)
        if not n:
            return None
        for brand in self.major_brands:
            b = normalise_text(brand)
            if not b:
                continue
            if n == b or n.startswith(b + " ") or f" {b} " in f" {n} ":
                return brand
        return None

    def is_uk_applicant(self, record: TrademarkRecord) -> bool:
        country = (record.applicant_country or "").strip().lower()
        if not country:
            return True  # unknown country is not a rejection on its own
        return country in self.uk_values

    def looks_corporate(self, applicant_name: str | None) -> bool:
        n = normalise_text(applicant_name)
        if not n:
            return False
        tokens = set(n.split())
        return bool(tokens & set(self.person_cfg["corporate_suffixes"]))

    def match_product_group(self, text: str) -> tuple[str | None, str | None, list[str]]:
        """Best-matching product group for some goods/brand text."""
        best_key: str | None = None
        best_label: str | None = None
        best_hits: list[str] = []
        for group in self.groups:
            hits = [k for k in group["keywords"] if k in text]
            if len(hits) > len(best_hits):
                best_key, best_label, best_hits = group["key"], group["label"], hits
        return best_key, best_label, best_hits

    def excluded_goods_keyword(self, text: str) -> str | None:
        for keyword in self.goods_exclusions:
            if keyword in text:
                return keyword
        return None

    # -- main --------------------------------------------------------------
    def assess(self, record: TrademarkRecord) -> FilterOutcome:
        classes = set(record.nice_classes)
        assessment = ProductAssessment(classifier="rules")

        # Class-only exclusions run first so the rejection reason is specific:
        # "restaurant services" is more useful evidence than "no food class".
        for rule in self.exclusions["excluded_nice_class_only"]:
            rule_classes = set(rule["classes"])
            if classes and classes <= rule_classes:
                assessment.rejection_reasons.append(rule["reason"])
                return FilterOutcome(False, assessment, rule["reason"], rule.get("note"))

        if not classes & (self.primary | self.supporting):
            assessment.rejection_reasons.append("no_food_class")
            return FilterOutcome(False, assessment, "no_food_class", f"classes={sorted(classes)}")

        # Supporting classes alone are not enough without a primary class.
        if not classes & self.primary:
            assessment.rejection_reasons.append("supporting_class_only")
            return FilterOutcome(
                False, assessment, "supporting_class_only", f"classes={sorted(classes)}"
            )

        goods_text = normalise_text(record.goods_text) if record.goods_text else ""
        mark_text = normalise_text(record.mark_text)
        search_text = f"{goods_text} {mark_text}".strip()

        if goods_text:
            excluded = self.excluded_goods_keyword(goods_text)
            if excluded:
                assessment.rejection_reasons.append("excluded_goods_keyword")
                return FilterOutcome(False, assessment, "excluded_goods_keyword", excluded)

        major = self.is_major_brand_owner(record.applicant_name)
        if major and self.exclusions.get("major_brand_action") == "suppress":
            assessment.rejection_reasons.append("major_brand_owner")
            return FilterOutcome(False, assessment, "major_brand_owner", major)

        if self.exclusions.get(
            "require_uk_or_unknown_applicant_country"
        ) and not self.is_uk_applicant(record):
            assessment.rejection_reasons.append("non_uk_applicant")
            return FilterOutcome(False, assessment, "non_uk_applicant", record.applicant_country)

        group_key, group_label, hits = self.match_product_group(search_text)

        # Broad class profiles are portfolio-shaped, not launch-shaped.
        warnings: list[str] = []
        if len(classes) >= 8:
            warnings.append(f"broad_class_profile:{len(classes)}")

        packaged_prob = self._packaged_probability(record, classes, hits, group_key)

        assessment.is_food_candidate = True
        assessment.consumer_product = True
        assessment.physical_product = True
        assessment.food_vertical = True
        assessment.product_category = group_key
        assessment.product_category_label = group_label
        assessment.packaged_product_probability = packaged_prob
        assessment.matched_keywords = hits[:12]
        assessment.packaging_relevance = (
            Relevance.HIGH
            if packaged_prob >= 0.7
            else Relevance.MEDIUM
            if packaged_prob >= 0.4
            else Relevance.LOW
        )
        assessment.contract_manufacturing_relevance = assessment.packaging_relevance
        assessment.distribution_relevance = (
            Relevance.HIGH if packaged_prob >= 0.6 else Relevance.MEDIUM
        )
        assessment.reasoning_summary = self._summary(record, classes, group_label, hits)
        return FilterOutcome(True, assessment, warnings=warnings)

    def _packaged_probability(
        self,
        record: TrademarkRecord,
        classes: set[int],
        hits: list[str],
        group_key: str | None,
    ) -> float:
        """A calibrated-ish confidence that this is a packaged retail food product.

        Deliberately conservative when goods text is missing.
        """
        prob = 0.0
        if classes & self.primary:
            prob += 0.35
        if len(classes & self.primary) == len(classes):
            prob += 0.1  # purely food classes
        if group_key:
            prob += 0.2
        prob += min(len(hits), 4) * 0.05
        if record.goods_text_available:
            prob += 0.15
        else:
            prob -= 0.05
        if len(classes) >= 8:
            prob -= 0.15
        if (record.mark_type or "").lower().startswith("word"):
            prob += 0.05
        return round(max(0.0, min(1.0, prob)), 2)

    def _summary(
        self, record: TrademarkRecord, classes: set[int], label: str | None, hits: list[str]
    ) -> str:
        bits = [f"Nice class {', '.join(str(c) for c in sorted(classes))}"]
        if label:
            bits.append(f"product group {label.lower()}")
        if hits:
            bits.append("matched " + ", ".join(hits[:3]))
        if not record.goods_text_available:
            bits.append("goods text unavailable in source")
        return "; ".join(bits)
