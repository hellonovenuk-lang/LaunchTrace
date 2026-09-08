"""The LaunchTrace Score.

An explainable 0-100 internal signal of how early-stage and how commercially
relevant a trade mark filing looks.  It is not a probability that the company
will buy anything from a subscriber, and the copy that reaches customers says so.

Every indicator that fires produces a human-readable reason.  Weights live in
``config/scoring.json`` so the model can be re-tuned without touching code.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import (
    CompanyMatch,
    LaunchStage,
    ProductAssessment,
    RetailPresence,
    Score,
    ScoreBand,
    ScoreReason,
    TrademarkRecord,
    WebEnrichment,
)
from src.parse.normalise import normalise_text
from src.settings import load_config


@dataclass
class ScoringContext:
    """Everything the scorer needs beyond the record itself."""

    record: TrademarkRecord
    product: ProductAssessment
    company: CompanyMatch
    web: WebEnrichment
    launch_stage: LaunchStage = LaunchStage.UNKNOWN
    applicant_journal_mark_count: int = 1
    first_trademark_for_applicant: bool = True
    is_major_brand_owner: bool = False
    is_natural_person: bool = False
    is_uk_applicant: bool = True
    company_age_years: float | None = None
    food_sic_summary: str | None = None
    service_business_sic_only: str | None = None


class LaunchTraceScorer:
    def __init__(self, config: dict | None = None, exclusions: dict | None = None) -> None:
        self.cfg = config or load_config("scoring.json")
        self.exclusions = exclusions or load_config("exclusions.json")
        self.positive = {i["key"]: i for i in self.cfg["positive_indicators"]}
        self.negative = {i["key"]: i for i in self.cfg["negative_indicators"]}

    # -- indicator detection ------------------------------------------------
    def _fired(self, ctx: ScoringContext) -> tuple[list[str], list[str], dict[str, object]]:
        positives: list[str] = []
        negatives: list[str] = []
        facts: dict[str, object] = {}

        age = ctx.company_age_years
        if age is not None:
            facts["company_age_years"] = round(age, 1)
            facts["company_age_months"] = int(round(age * 12))
            if age <= 1.0:
                positives.append("company_incorporated_within_12m")
            elif age <= 2.0:
                positives.append("company_incorporated_within_24m")
            elif age <= 4.0:
                positives.append("company_incorporated_within_48m")
            if age > 10.0:
                negatives.append("company_older_than_10y")
            elif age > 6.0:
                negatives.append("company_older_than_6y")

        if ctx.first_trademark_for_applicant:
            positives.append("first_trademark_for_applicant")

        facts["applicant_journal_mark_count"] = ctx.applicant_journal_mark_count
        thresholds = self.exclusions["portfolio_filing_thresholds"]
        if ctx.applicant_journal_mark_count == 1:
            positives.append("single_mark_this_journal")
        elif ctx.applicant_journal_mark_count >= thresholds["applicant_marks_in_single_journal_warn"]:
            negatives.append("portfolio_filing")

        class_count = len(ctx.record.nice_classes)
        facts["class_count"] = class_count
        if ctx.product.product_category:
            positives.append("specific_packaged_food_category")
            facts["product_category"] = (
                ctx.product.product_category_label or ctx.product.product_category
            )
        if 0 < class_count <= 3:
            positives.append("narrow_class_profile")
        elif class_count >= 8:
            negatives.append("many_classes")

        if ctx.food_sic_summary:
            positives.append("food_sic_code_match")
            facts["sic_summary"] = ctx.food_sic_summary
        if ctx.service_business_sic_only:
            negatives.append("service_business_sic_only")
            facts["sic_summary"] = ctx.service_business_sic_only

        if ctx.company.matched:
            facts["match_confidence"] = ctx.company.match_confidence
            if ctx.company.match_confidence >= 85:
                positives.append("strong_company_match")
            elif ctx.company.match_confidence < 70:
                negatives.append("weak_company_match")
            if (ctx.company.accounts_category or "").upper() in {
                "MICRO ENTITY", "TOTAL EXEMPTION SMALL", "SMALL", "ACCOUNTS TYPE NOT AVAILABLE",
                "TOTAL EXEMPTION FULL",
            }:
                positives.append("small_company_accounts")
            if (ctx.company.accounts_category or "").upper() == "DORMANT":
                negatives.append("company_dormant")
        else:
            negatives.append("no_company_match")

        if ctx.web.attempted:
            if ctx.web.website_maturity == "early_stage":
                positives.append("early_stage_website")
                facts["website"] = ctx.web.website or ""
            if ctx.web.major_retailer_presence is False:
                positives.append("no_major_retail_listings")
            if ctx.web.launch_evidence:
                positives.append("active_launch_signals")
                facts["launch_evidence"] = ", ".join(ctx.web.launch_evidence[:3])
            if ctx.web.website_maturity == "established":
                negatives.append("mature_brand_footprint")
            if ctx.web.retail_presence == RetailPresence.MULTIPLE_RETAIL:
                negatives.append("widely_distributed")

        if self._distinctive_brand_name(ctx.record):
            positives.append("distinctive_consumer_brand_name")
        if (ctx.record.mark_type or "").lower().startswith("word"):
            positives.append("word_mark")
        elif (ctx.record.mark_type or "").lower().startswith("fig"):
            negatives.append("figurative_only_mark")

        if not ctx.record.goods_text_available:
            negatives.append("goods_text_unavailable")
        if ctx.is_major_brand_owner:
            negatives.append("major_brand_owner")
        if ctx.is_natural_person:
            negatives.append("natural_person_applicant")
        if not ctx.is_uk_applicant:
            negatives.append("non_uk_applicant")
        if ctx.product.product_category is None and not ctx.product.food_vertical:
            negatives.append("service_only_profile")

        return positives, negatives, facts

    @staticmethod
    def _distinctive_brand_name(record: TrademarkRecord) -> bool:
        """A short, non-generic word mark reads like a new consumer product name."""
        text = normalise_text(record.mark_text)
        if not text:
            return False
        words = text.split()
        if not (1 <= len(words) <= 4):
            return False
        if len(text) > 30:
            return False
        generic = {"the", "company", "limited", "ltd", "group", "services", "solutions", "uk"}
        return not set(words) <= generic

    # -- scoring ------------------------------------------------------------
    def score(self, ctx: ScoringContext) -> Score:
        positives, negatives, facts = self._fired(ctx)
        value = int(self.cfg["base_score"])
        reasons: list[ScoreReason] = []
        negative_reasons: list[ScoreReason] = []

        for key in positives:
            ind = self.positive.get(key)
            if not ind:
                continue
            value += int(ind["weight"])
            reasons.append(
                ScoreReason(key=key, text=self._render(ind["reason_template"], facts), weight=int(ind["weight"]))
            )
        for key in negatives:
            ind = self.negative.get(key)
            if not ind:
                continue
            value += int(ind["weight"])
            negative_reasons.append(
                ScoreReason(key=key, text=self._render(ind["reason_template"], facts), weight=int(ind["weight"]))
            )

        value = max(int(self.cfg["min_score"]), min(int(self.cfg["max_score"]), value))

        capped = False
        cap_reason: str | None = None
        req = self.cfg["confidence_requirements"]
        cap = int(self.cfg["high_band_cap_without_company_match"])
        if req.get("high_band_requires_company_match"):
            confident = ctx.company.matched and ctx.company.match_confidence >= int(
                req.get("high_band_min_company_match_confidence", 70)
            )
            if not confident and value > cap:
                value = cap
                capped = True
                cap_reason = (
                    "Capped below the HIGH band: no Companies House match we are confident enough in"
                )
        if req.get("high_band_requires_product_category") and not ctx.product.product_category:
            if value > cap:
                value = cap
                capped = True
                cap_reason = (
                    "Capped below the HIGH band: the source did not give enough detail to "
                    "identify a specific product category"
                )

        web_cap = self.cfg.get("score_cap_without_web_evidence")
        if web_cap is not None and not ctx.web.attempted and value > int(web_cap):
            value = int(web_cap)
            capped = True
            cap_reason = (
                "Capped: no web evidence was gathered, so we have not verified whether this brand "
                "is already established"
            )

        band, label = self._band(value)
        reasons.sort(key=lambda r: r.weight, reverse=True)
        return Score(
            value=value,
            band=band,
            band_label=label,
            reasons=reasons[: int(self.cfg["max_reasons_shown"])],
            negative_reasons=negative_reasons,
            capped=capped,
            cap_reason=cap_reason,
        )

    def _band(self, value: int) -> tuple[ScoreBand, str]:
        for band in self.cfg["bands"]:
            if int(band["min"]) <= value <= int(band["max"]):
                return ScoreBand(band["key"]), str(band["label"])
        return ScoreBand.SUPPRESS, "Below delivery threshold"

    @staticmethod
    def _render(template: str, facts: dict[str, object]) -> str:
        try:
            return template.format(**facts)
        except (KeyError, IndexError):
            # A missing fact must never produce a broken or invented reason.
            import re

            return re.sub(r"\s*\([^)]*\{[^)]*\)", "", re.sub(r"\{[^}]*\}", "", template)).strip()
