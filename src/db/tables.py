"""SQLAlchemy table definitions -- the schema source of truth.

Source data (``journals``, ``trademark_records``) is kept separate from derived
data (``company_matches``, ``web_enrichment``, ``opportunities``) so a scoring
change never requires re-fetching a journal.

It is also where LaunchTrace's own live sales state lives (``prospect_state``,
``prospect_suppressions``). The researched seed list is reusable research and
stays in git; anything that accumulates from contacting real people does not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Journal(Base):
    __tablename__ = "journals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    journal_number: Mapped[str] = mapped_column(String(32), index=True)
    source_name: Mapped[str] = mapped_column(String(64), default="ukipo_journal_xml")
    publication_date: Mapped[date] = mapped_column(Date, index=True)
    source_url: Mapped[str | None] = mapped_column(String(512))
    sha256: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_status: Mapped[str] = mapped_column(String(32), default="pending")

    __table_args__ = (
        UniqueConstraint("source_name", "journal_number", name="uq_journal_source_number"),
    )


class TrademarkRecordRow(Base):
    __tablename__ = "trademark_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    journal_id: Mapped[int | None] = mapped_column(ForeignKey("journals.id", ondelete="SET NULL"))
    journal_number: Mapped[str] = mapped_column(String(32), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    trademark_number: Mapped[str] = mapped_column(String(32), index=True)
    mark_text: Mapped[str | None] = mapped_column(String(512))
    mark_type: Mapped[str | None] = mapped_column(String(64))
    mark_category: Mapped[str | None] = mapped_column(String(64))
    filing_date: Mapped[date | None] = mapped_column(Date, index=True)
    publication_date: Mapped[date | None] = mapped_column(Date, index=True)
    applicant_name: Mapped[str | None] = mapped_column(String(512), index=True)
    applicant_country: Mapped[str | None] = mapped_column(String(128))
    applicant_region: Mapped[str | None] = mapped_column(String(128))
    applicant_postcode_area: Mapped[str | None] = mapped_column(String(16))
    nice_classes: Mapped[list] = mapped_column(JSON, default=list)
    goods_text: Mapped[str | None] = mapped_column(Text)
    goods_text_available: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str | None] = mapped_column(String(64))
    series_count: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[str | None] = mapped_column(String(512))
    source_name: Mapped[str] = mapped_column(String(64), default="ukipo_journal_xml")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("journal_number", "trademark_number", name="uq_tm_journal_number"),
        Index("ix_tm_applicant_journal", "applicant_name", "journal_number"),
    )


class CompanyMatchRow(Base):
    __tablename__ = "company_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    applicant_name: Mapped[str | None] = mapped_column(String(512), index=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    company_name: Mapped[str | None] = mapped_column(String(512))
    company_number: Mapped[str | None] = mapped_column(String(16), index=True)
    company_status: Mapped[str | None] = mapped_column(String(64))
    company_category: Mapped[str | None] = mapped_column(String(128))
    incorporation_date: Mapped[date | None] = mapped_column(Date)
    dissolution_date: Mapped[date | None] = mapped_column(Date)
    sic_codes: Mapped[list] = mapped_column(JSON, default=list)
    region: Mapped[str | None] = mapped_column(String(128))
    post_town: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(128))
    accounts_category: Mapped[str | None] = mapped_column(String(64))
    match_confidence: Mapped[int] = mapped_column(Integer, default=0)
    match_method: Mapped[str] = mapped_column(String(64), default="none")
    match_evidence: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(32), default="none")
    error: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WebEnrichmentRow(Base):
    __tablename__ = "web_enrichment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="none")
    attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    website: Mapped[str | None] = mapped_column(String(512))
    contact_page: Mapped[str | None] = mapped_column(String(512))
    brand_description: Mapped[str | None] = mapped_column(Text)
    website_maturity: Mapped[str | None] = mapped_column(String(32))
    products_on_sale: Mapped[bool | None] = mapped_column(Boolean)
    marketplace_presence: Mapped[bool | None] = mapped_column(Boolean)
    major_retailer_presence: Mapped[bool | None] = mapped_column(Boolean)
    social_presence: Mapped[bool | None] = mapped_column(Boolean)
    retail_presence: Mapped[str] = mapped_column(String(32), default="unknown")
    launch_evidence: Mapped[list] = mapped_column(JSON, default=list)
    evidence_urls: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(String(512))
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpportunityRow(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    journal_number: Mapped[str] = mapped_column(String(32), index=True)
    trademark_number: Mapped[str] = mapped_column(String(32), index=True)
    brand_name: Mapped[str | None] = mapped_column(String(512))
    filing_date: Mapped[date | None] = mapped_column(Date)
    publication_date: Mapped[date | None] = mapped_column(Date, index=True)
    goods_summary: Mapped[str | None] = mapped_column(Text)
    product_category: Mapped[str | None] = mapped_column(String(64), index=True)
    applicant_name: Mapped[str | None] = mapped_column(String(512))
    applicant_type: Mapped[str] = mapped_column(String(32), default="unknown")
    company_name: Mapped[str | None] = mapped_column(String(512))
    company_number: Mapped[str | None] = mapped_column(String(16), index=True)
    company_incorporation_date: Mapped[date | None] = mapped_column(Date)
    company_age_years_at_filing: Mapped[float | None] = mapped_column(Float)
    company_region: Mapped[str | None] = mapped_column(String(128))
    website: Mapped[str | None] = mapped_column(String(512))
    contact_page: Mapped[str | None] = mapped_column(String(512))
    launch_stage: Mapped[str] = mapped_column(String(32), default="unknown")
    retail_presence: Mapped[str] = mapped_column(String(32), default="unknown")
    launchtrace_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_band: Mapped[str] = mapped_column(String(16), default="SUPPRESS", index=True)
    score_reasons: Mapped[list] = mapped_column(JSON, default=list)
    buying_intent: Mapped[dict] = mapped_column(JSON, default=dict)
    nice_classes: Mapped[list] = mapped_column(JSON, default=list)
    source_url: Mapped[str | None] = mapped_column(String(512))
    evidence_urls: Mapped[list] = mapped_column(JSON, default=list)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    suppression_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("journal_number", "dedupe_key", name="uq_opportunity_journal_key"),
        Index("ix_opportunity_band_score", "score_band", "launchtrace_score"),
    )


class ScoreEvent(Base):
    __tablename__ = "score_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    band: Mapped[str] = mapped_column(String(16), default="SUPPRESS")
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    negative_reasons: Mapped[list] = mapped_column(JSON, default=list)
    scoring_config_version: Mapped[str] = mapped_column(String(16), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company: Mapped[str] = mapped_column(String(256))
    contact_name: Mapped[str | None] = mapped_column(String(256))
    supplier_type: Mapped[str | None] = mapped_column(String(64))
    plan_key: Mapped[str] = mapped_column(String(32), default="founding_monthly")
    subscription_status: Mapped[str] = mapped_column(String(32), default="trialing", index=True)
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    founding_customer: Mapped[bool] = mapped_column(Boolean, default=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the subscription first went past_due. Delivery continues through a
    # short grace period and then stops, so a failed card never becomes an
    # indefinite free subscription.
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The prospect this customer came from, so the funnel can be reconciled
    # end to end without a second system.
    prospect_id: Mapped[str | None] = mapped_column(String(16), index=True)

    preferences: Mapped[list[CustomerPreference]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class CustomerPreference(Base):
    __tablename__ = "customer_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    recipient_email: Mapped[str] = mapped_column(String(256), index=True)
    sector: Mapped[str] = mapped_column(String(32), default="food")
    min_score_band: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    regions: Mapped[list] = mapped_column(JSON, default=list)
    product_categories: Mapped[list] = mapped_column(JSON, default=list)
    # What this customer supplies, and which buying-intent categories matter to
    # them. Recorded from the first day so the feed can be tailored later
    # without going back to every customer to ask. The MVP still delivers the
    # full food feed to everyone: see src/delivery_service.py.
    supplier_category: Mapped[str | None] = mapped_column(String(64))
    buying_intent_categories: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    customer: Mapped[Customer] = relationship(back_populates="preferences")

    __table_args__ = (
        UniqueConstraint("customer_id", "recipient_email", name="uq_customer_recipient"),
    )


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    journal_number: Mapped[str] = mapped_column(String(32), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    recipient_email: Mapped[str] = mapped_column(String(256), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="weekly_feed")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_delivery_idempotency"),)


class SuppressionRule(Base):
    """Operator-set exclusions: bad opportunities, unwanted companies, opt-outs."""

    __tablename__ = "suppression_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_type: Mapped[str] = mapped_column(
        String(32), index=True
    )  # company | applicant | mark | email
    value: Mapped[str] = mapped_column(String(512), index=True)
    reason: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[str | None] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("rule_type", "value", name="uq_suppression_type_value"),)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(32), default="weekly")
    journal_number: Mapped[str | None] = mapped_column(String(32), index=True)
    source_name: Mapped[str] = mapped_column(String(64), default="ukipo_journal_xml")
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    blocked_reason: Mapped[str | None] = mapped_column(String(256))
    csv_path: Mapped[str | None] = mapped_column(String(512))
    email_html_path: Mapped[str | None] = mapped_column(String(512))
    qa_report_path: Mapped[str | None] = mapped_column(String(512))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    delivery_status: Mapped[str] = mapped_column(String(32), default="not_sent")


class ErrorLog(Base):
    __tablename__ = "errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="error")
    reference: Mapped[str | None] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SampleRequest(Base):
    """Inbound 'send me a sample' request from the landing page."""

    __tablename__ = "sample_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_email: Mapped[str] = mapped_column(String(256), index=True)
    company: Mapped[str] = mapped_column(String(256))
    contact_name: Mapped[str | None] = mapped_column(String(256))
    supplier_type: Mapped[str | None] = mapped_column(String(64))
    source_ip_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("work_email", name="uq_sample_request_email"),)


class LeadFeedback(Base):
    """What a customer said about one delivered opportunity.

    Recorded as evidence, deliberately not wired into scoring. Letting customer
    opinion move the weights automatically would make the score unexplainable
    and untestable; the point of collecting this is to have something real to
    tune against later, by hand, with the reasoning written down.
    """

    __tablename__ = "lead_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    dedupe_key: Mapped[str | None] = mapped_column(String(64), index=True)
    trademark_number: Mapped[str | None] = mapped_column(String(32), index=True)
    brand_name: Mapped[str | None] = mapped_column(String(512))
    journal_number: Mapped[str | None] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="operator")  # operator | form | csv
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("ix_feedback_customer_state", "customer_id", "state"),)


class ProspectStateRow(Base):
    """Live outreach state for one prospect on LaunchTrace's own sales list.

    The researched seed list — who these companies are and why they fit — stays
    in ``outreach/prospects_seed.csv`` and in git, because it is reusable
    research. Everything that changes as a result of contacting a real person
    lives here instead: verified addresses, named contacts, reply notes, the
    dates things were sent, opt-outs and the current funnel position.

    None of that belongs in a commit history. It is personal data about
    identifiable people at identifiable businesses, it has a retention period,
    an opt-out has to be honoured immediately and permanently, and git makes
    deletion effectively impossible.
    """

    __tablename__ = "prospect_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(String(16), index=True)

    # How to reach them, and where the address came from. Never a guess.
    generic_contact_email: Mapped[str] = mapped_column(String(256), default="")
    named_contact: Mapped[str] = mapped_column(String(256), default="")
    decision_maker_role: Mapped[str] = mapped_column(String(128), default="")
    email_source: Mapped[str] = mapped_column(String(32), default="none")

    # Where they stand.
    status: Mapped[str] = mapped_column(String(32), default="RESEARCHED", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="C", index=True)
    icp_score: Mapped[int] = mapped_column(Integer, default=0)

    # When things happened.
    date_added: Mapped[date | None] = mapped_column(Date)
    email_1_sent_date: Mapped[date | None] = mapped_column(Date)
    sample_requested_date: Mapped[date | None] = mapped_column(Date)
    sample_sent_date: Mapped[date | None] = mapped_column(Date)
    offer_sent_date: Mapped[date | None] = mapped_column(Date)
    converted_date: Mapped[date | None] = mapped_column(Date)
    follow_up_due_date: Mapped[date | None] = mapped_column(Date)

    # What came back.
    stripe_customer_id: Mapped[str] = mapped_column(String(64), default="")
    reply_state: Mapped[str] = mapped_column(String(16), default="none")
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    suppression_reason: Mapped[str] = mapped_column(String(512), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("prospect_id", name="uq_prospect_state_prospect_id"),)


class ProspectSuppression(Base):
    """Who must never be contacted, by any identity we can check.

    Separate from ``prospect_state`` so that deleting a prospect can never
    delete the record of their opt-out, and insert-only so that no code path
    can shorten the list. ``SuppressionRule`` is the equivalent for the product
    feed; this one is about LaunchTrace's own outreach.
    """

    __tablename__ = "prospect_suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # email | domain | company
    value: Mapped[str] = mapped_column(String(512), index=True)
    company_name: Mapped[str] = mapped_column(String(512), default="")
    date_added: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(String(512), default="")
    added_by: Mapped[str] = mapped_column(String(64), default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("kind", "value", name="uq_prospect_suppression_kind_value"),)


class WebhookEvent(Base):
    """Processed Stripe webhook events -- the idempotency guard."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="stripe")
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    payload_summary: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),)
