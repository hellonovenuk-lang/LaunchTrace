"""Domain models.

These are the objects that flow through the pipeline.  They are deliberately
separate from the database tables in ``src/db/tables.py`` so that source data
and derived data stay distinguishable.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class LaunchStage(str, Enum):
    PRE_LAUNCH = "pre_launch"
    EARLY_LAUNCH = "early_launch"
    SCALING = "scaling"
    ESTABLISHED = "established"
    UNKNOWN = "unknown"


class RetailPresence(str, Enum):
    NONE_FOUND = "none_found"
    DIRECT_ONLY = "direct_only"
    MARKETPLACE = "marketplace"
    INDEPENDENT_RETAIL = "independent_retail"
    MULTIPLE_RETAIL = "multiple_retail"
    UNKNOWN = "unknown"


class BrandMaturity(str, Enum):
    """How established the *consumer brand* is, which is not how old its company is.

    A brand can be decades old inside a company incorporated last month, and a
    company can be five years old and launching its first product this week.
    Treating incorporation date as brand age is the mistake this separates out.
    """

    EMERGING = "emerging"
    ESTABLISHED = "established"
    UNKNOWN = "unknown"


class Relevance(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class ScoreBand(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    SUPPRESS = "SUPPRESS"


class ApplicantType(str, Enum):
    CORPORATE = "corporate"
    NATURAL_PERSON = "natural_person"
    UNKNOWN = "unknown"


class ReviewState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPPRESSED = "suppressed"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Source layer
# ---------------------------------------------------------------------------


class JournalRef(BaseModel):
    """Identifies one weekly UKIPO Trade Marks Journal."""

    model_config = ConfigDict(frozen=True)

    journal_number: str = Field(description="UKIPO journal identifier, e.g. '2018-004'.")
    publication_date: date
    source_name: str = Field(default="ukipo_journal_xml")
    source_url: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source_name}:{self.journal_number}"


class JournalArtifact(BaseModel):
    """A retrieved journal file plus integrity metadata."""

    ref: JournalRef
    local_path: str
    content_type: str = "application/xml"
    byte_size: int = 0
    sha256: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)
    from_cache: bool = False


class TrademarkRecord(BaseModel):
    """A single trade mark record as published, before any LaunchTrace judgement."""

    trademark_number: str
    mark_text: str | None = None
    mark_type: str | None = None
    mark_category: str | None = None
    filing_date: date | None = None
    publication_date: date | None = None
    applicant_name: str | None = None
    applicant_country: str | None = None
    applicant_region: str | None = None
    applicant_postcode_area: str | None = None
    nice_classes: list[int] = Field(default_factory=list)
    goods_text: str | None = None
    goods_text_available: bool = False
    series_count: int = 0
    status: str | None = None
    journal_number: str = ""
    source_url: str | None = None
    source_name: str = "ukipo_journal_xml"
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        """Stable identity for a record across re-runs and across journals."""
        basis = "|".join(
            [
                (self.trademark_number or "").strip().upper(),
                (self.mark_text or "").strip().lower(),
                (self.applicant_name or "").strip().lower(),
            ]
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Classification layer
# ---------------------------------------------------------------------------


class ProductAssessment(BaseModel):
    """Result of the food/product filter (deterministic rules + optional LLM)."""

    is_food_candidate: bool = False
    consumer_product: bool = False
    physical_product: bool = False
    food_vertical: bool = False
    product_category: str | None = None
    product_category_label: str | None = None
    packaged_product_probability: float = 0.0
    packaging_relevance: Relevance = Relevance.NONE
    contract_manufacturing_relevance: Relevance = Relevance.NONE
    distribution_relevance: Relevance = Relevance.NONE
    reasoning_summary: str = ""
    matched_keywords: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    classifier: str = "rules"
    llm_used: bool = False


class CompanyMatch(BaseModel):
    """A Companies House match for a trade mark applicant."""

    matched: bool = False
    company_name: str | None = None
    company_number: str | None = None
    company_status: str | None = None
    company_category: str | None = None
    incorporation_date: date | None = None
    dissolution_date: date | None = None
    sic_codes: list[str] = Field(default_factory=list)
    region: str | None = None
    post_town: str | None = None
    country: str | None = None
    accounts_category: str | None = None
    match_confidence: int = 0
    match_method: str = "none"
    match_evidence: list[str] = Field(default_factory=list)
    candidates_considered: int = 0
    provider: str = "none"
    source_url: str | None = None
    error: str | None = None

    def age_years_at(self, reference: date | None) -> float | None:
        if not self.incorporation_date or not reference:
            return None
        return round((reference - self.incorporation_date).days / 365.25, 2)


class WebEnrichment(BaseModel):
    """What public web evidence says about the brand.  Absence stays absence.

    ``website`` is the customer-facing field and is populated only when the
    domain has been verified as the applicant's.  ``candidate_website`` keeps
    the best unproven guess for internal review, so a failed verification can be
    audited rather than merely disappearing.
    """

    attempted: bool = False
    provider: str = "none"
    website: str | None = None
    contact_page: str | None = None
    brand_description: str | None = None
    website_maturity: str | None = None  # early_stage | established | unknown
    products_on_sale: bool | None = None
    marketplace_presence: bool | None = None
    major_retailer_presence: bool | None = None
    social_presence: bool | None = None
    launch_evidence: list[str] = Field(default_factory=list)
    retail_presence: RetailPresence = RetailPresence.UNKNOWN
    evidence_urls: list[str] = Field(default_factory=list)
    error: str | None = None
    enriched_at: datetime | None = None

    # -- entity verification (internal evidence, never customer-facing) -----
    verification_status: str = "not_attempted"
    verification_evidence: list[str] = Field(default_factory=list)
    rejected_candidates: list[str] = Field(default_factory=list)
    candidate_website: str | None = None
    attributed_urls: list[str] = Field(default_factory=list)
    distinct_retailers: list[str] = Field(default_factory=list)
    established_evidence: list[str] = Field(default_factory=list)

    @property
    def entity_evidence_available(self) -> bool:
        """Whether anything we found is provably about this applicant.

        When nothing is, every maturity signal stays unknown -- a search that
        found only an unrelated company has told us nothing about this one.
        """
        return self.attempted and bool(self.attributed_urls)


class ScoreReason(BaseModel):
    key: str
    text: str
    weight: int


class Score(BaseModel):
    """A LaunchTrace Score with the full reasoning behind it.

    ``reasons`` holds every indicator that fired, so the stored record is a
    complete audit trail. Customer-facing output shows ``top_reasons``.
    """

    value: int = 0
    band: ScoreBand = ScoreBand.SUPPRESS
    band_label: str = ""
    reasons: list[ScoreReason] = Field(default_factory=list)
    negative_reasons: list[ScoreReason] = Field(default_factory=list)
    capped: bool = False
    cap_reason: str | None = None
    max_reasons_shown: int = 6

    @property
    def top_reasons(self) -> list[ScoreReason]:
        return self.reasons[: self.max_reasons_shown]

    @property
    def reason_texts(self) -> list[str]:
        return [r.text for r in self.top_reasons]

    @property
    def all_reason_texts(self) -> list[str]:
        return [r.text for r in self.reasons]


class BuyingIntent(BaseModel):
    """Inferred supplier relevance. Never a claim of current purchasing."""

    flexible_packaging: Relevance = Relevance.NONE
    labels: Relevance = Relevance.NONE
    cartons: Relevance = Relevance.NONE
    contract_manufacturing: Relevance = Relevance.NONE
    copacking: Relevance = Relevance.NONE
    distribution: Relevance = Relevance.NONE
    brokerage: Relevance = Relevance.NONE
    fulfilment: Relevance = Relevance.NONE
    marketing: Relevance = Relevance.NONE

    def as_dict(self) -> dict[str, str]:
        return {k: v.value for k, v in self.model_dump().items()}


class RelatedMark(BaseModel):
    """Another mark filed by the same company, carried under one opportunity.

    A supplier buys from a company once, not once per trade mark, so the marks
    are consolidated -- but none of the source detail is thrown away.
    """

    trademark_number: str
    brand_name: str | None = None
    product_category: str | None = None
    product_category_label: str | None = None
    nice_classes: list[int] = Field(default_factory=list)
    filing_date: date | None = None
    goods_summary: str | None = None
    source_url: str | None = None


class Opportunity(BaseModel):
    """The commercial object delivered to customers."""

    dedupe_key: str
    trademark_number: str
    brand_name: str | None = None
    filing_date: date | None = None
    publication_date: date | None = None
    journal_number: str = ""
    goods_summary: str | None = None
    product_category: str | None = None
    product_category_label: str | None = None
    applicant_name: str | None = None
    applicant_type: ApplicantType = ApplicantType.UNKNOWN
    nice_classes: list[int] = Field(default_factory=list)

    company: CompanyMatch = Field(default_factory=CompanyMatch)
    web: WebEnrichment = Field(default_factory=WebEnrichment)
    product: ProductAssessment = Field(default_factory=ProductAssessment)
    score: Score = Field(default_factory=Score)
    buying_intent: BuyingIntent = Field(default_factory=BuyingIntent)

    launch_stage: LaunchStage = LaunchStage.UNKNOWN
    retail_presence: RetailPresence = RetailPresence.UNKNOWN
    brand_maturity: BrandMaturity = BrandMaturity.UNKNOWN
    brand_maturity_evidence: list[str] = Field(default_factory=list)
    company_age_years_at_filing: float | None = None

    related_marks: list[RelatedMark] = Field(default_factory=list)

    source_url: str | None = None
    evidence_urls: list[str] = Field(default_factory=list)
    enriched_at: datetime | None = None
    review_state: ReviewState = ReviewState.PENDING
    delivered: bool = False
    suppressed: bool = False
    suppression_reason: str | None = None

    @property
    def company_mark_count(self) -> int:
        """Marks this one customer-facing opportunity represents."""
        return 1 + len(self.related_marks)

    @property
    def company_key(self) -> str:
        """Identity used to collapse several marks into one company opportunity."""
        from src.parse.normalise import company_name_key

        if self.company.matched and self.company.company_number:
            return f"ch:{self.company.company_number.strip().upper()}"
        return f"name:{company_name_key(self.applicant_name)}"


class RejectedRecord(BaseModel):
    """A record that did not survive the funnel, kept for validation evidence."""

    trademark_number: str
    mark_text: str | None = None
    applicant_name: str | None = None
    stage: str
    reason: str
    detail: str | None = None


class FunnelCounts(BaseModel):
    """The audit trail a human needs to judge whether the signal is real."""

    raw_records: int = 0
    food_class_candidates: int = 0
    packaged_food_candidates: int = 0
    uk_corporate_applicants: int = 0
    company_matched: int = 0
    emerging_candidates: int = 0
    web_enriched: int = 0
    scored: int = 0
    high: int = 0
    medium: int = 0
    suppressed: int = 0
    duplicates_dropped: int = 0
    enrichment_failures: int = 0
    llm_failures: int = 0

    # -- customer-facing quality -------------------------------------------
    verified_websites: int = 0
    unverified_websites: int = 0
    out_of_scope_products: int = 0
    established_brands_suppressed: int = 0
    companies_consolidated: int = 0
    marks_consolidated: int = 0
    customer_facing_companies: int = 0

    rejection_reasons: dict[str, int] = Field(default_factory=dict)

    def add_rejection(self, reason: str, n: int = 1) -> None:
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + n


class PipelineResult(BaseModel):
    """Everything one journal run produced."""

    run_id: str
    journal: JournalRef
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    counts: FunnelCounts = Field(default_factory=FunnelCounts)
    opportunities: list[Opportunity] = Field(default_factory=list)
    rejected: list[RejectedRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    csv_path: str | None = None
    email_html_path: str | None = None
    qa_report_path: str | None = None

    @property
    def deliverable(self) -> list[Opportunity]:
        return [
            o
            for o in self.opportunities
            if not o.suppressed and o.score.band in (ScoreBand.HIGH, ScoreBand.MEDIUM)
        ]
