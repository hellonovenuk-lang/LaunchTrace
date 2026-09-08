"""Persistence for pipeline output.

Writes are idempotent: re-running a journal updates rather than duplicates, so a
scheduled job is always safe to re-run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.tables import (
    CompanyMatchRow,
    Delivery,
    ErrorLog,
    Journal,
    OpportunityRow,
    PipelineRun,
    ScoreEvent,
    SuppressionRule,
    TrademarkRecordRow,
    WebEnrichmentRow,
)
from src.logging_setup import get_logger
from src.models import JournalArtifact, Opportunity, PipelineResult, TrademarkRecord

log = get_logger(__name__)


# -- journals --------------------------------------------------------------


def journal_already_processed(session: Session, source_name: str, journal_number: str) -> bool:
    row = session.execute(
        select(Journal).where(
            Journal.source_name == source_name, Journal.journal_number == journal_number
        )
    ).scalar_one_or_none()
    return bool(row and row.processing_status == "processed")


def upsert_journal(
    session: Session, artifact: JournalArtifact, record_count: int, status: str = "processed"
) -> Journal:
    ref = artifact.ref
    row = session.execute(
        select(Journal).where(
            Journal.source_name == ref.source_name, Journal.journal_number == ref.journal_number
        )
    ).scalar_one_or_none()
    if row is None:
        row = Journal(source_name=ref.source_name, journal_number=ref.journal_number)
        session.add(row)
    row.publication_date = ref.publication_date
    row.source_url = ref.source_url
    row.sha256 = artifact.sha256
    row.byte_size = artifact.byte_size
    row.record_count = record_count
    row.retrieved_at = artifact.retrieved_at
    row.processed_at = datetime.now(UTC)
    row.processing_status = status
    session.flush()
    return row


def save_trademark_records(
    session: Session, records: list[TrademarkRecord], journal_id: int | None = None
) -> int:
    """Insert source records, skipping any already stored for this journal."""
    if not records:
        return 0
    journal_number = records[0].journal_number
    existing = {
        r
        for (r,) in session.execute(
            select(TrademarkRecordRow.trademark_number).where(
                TrademarkRecordRow.journal_number == journal_number
            )
        )
    }
    added = 0
    for record in records:
        if record.trademark_number in existing:
            continue
        session.add(
            TrademarkRecordRow(
                journal_id=journal_id,
                journal_number=record.journal_number,
                dedupe_key=record.dedupe_key,
                trademark_number=record.trademark_number,
                mark_text=record.mark_text,
                mark_type=record.mark_type,
                mark_category=record.mark_category,
                filing_date=record.filing_date,
                publication_date=record.publication_date,
                applicant_name=record.applicant_name,
                applicant_country=record.applicant_country,
                applicant_region=record.applicant_region,
                applicant_postcode_area=record.applicant_postcode_area,
                nice_classes=record.nice_classes,
                goods_text=record.goods_text,
                goods_text_available=record.goods_text_available,
                status=record.status,
                series_count=record.series_count,
                source_url=record.source_url,
                source_name=record.source_name,
            )
        )
        existing.add(record.trademark_number)
        added += 1
    session.flush()
    return added


# -- derived data ----------------------------------------------------------


def save_opportunities(session: Session, result: PipelineResult) -> int:
    saved = 0
    for opp in result.opportunities:
        row = session.execute(
            select(OpportunityRow).where(
                OpportunityRow.journal_number == opp.journal_number,
                OpportunityRow.dedupe_key == opp.dedupe_key,
            )
        ).scalar_one_or_none()
        if row is None:
            row = OpportunityRow(journal_number=opp.journal_number, dedupe_key=opp.dedupe_key)
            session.add(row)
        _apply_opportunity(row, opp, result.run_id)
        session.add(
            ScoreEvent(
                dedupe_key=opp.dedupe_key,
                run_id=result.run_id,
                score=opp.score.value,
                band=opp.score.band.value,
                reasons=[r.model_dump() for r in opp.score.reasons],
                negative_reasons=[r.model_dump() for r in opp.score.negative_reasons],
            )
        )
        _save_company_match(session, opp)
        _save_web_enrichment(session, opp)
        saved += 1
    session.flush()
    return saved


def _apply_opportunity(row: OpportunityRow, opp: Opportunity, run_id: str) -> None:
    row.run_id = run_id
    row.trademark_number = opp.trademark_number
    row.brand_name = opp.brand_name
    row.filing_date = opp.filing_date
    row.publication_date = opp.publication_date
    row.goods_summary = opp.goods_summary
    row.product_category = opp.product_category
    row.applicant_name = opp.applicant_name
    row.applicant_type = opp.applicant_type.value
    row.company_name = opp.company.company_name
    row.company_number = opp.company.company_number
    row.company_incorporation_date = opp.company.incorporation_date
    row.company_age_years_at_filing = opp.company_age_years_at_filing
    row.company_region = opp.company.region
    row.website = opp.web.website
    row.contact_page = opp.web.contact_page
    row.launch_stage = opp.launch_stage.value
    row.retail_presence = opp.retail_presence.value
    row.launchtrace_score = opp.score.value
    row.score_band = opp.score.band.value
    row.score_reasons = opp.score.reason_texts
    row.buying_intent = opp.buying_intent.as_dict()
    row.nice_classes = opp.nice_classes
    row.source_url = opp.source_url
    row.evidence_urls = opp.evidence_urls
    row.enriched_at = opp.enriched_at
    row.review_state = opp.review_state.value
    row.delivered = opp.delivered
    row.suppressed = opp.suppressed
    row.suppression_reason = opp.suppression_reason


def _save_company_match(session: Session, opp: Opportunity) -> None:
    m = opp.company
    row = session.execute(
        select(CompanyMatchRow).where(CompanyMatchRow.dedupe_key == opp.dedupe_key)
    ).scalar_one_or_none()
    if row is None:
        row = CompanyMatchRow(dedupe_key=opp.dedupe_key)
        session.add(row)
    row.applicant_name = opp.applicant_name
    row.matched = m.matched
    row.company_name = m.company_name
    row.company_number = m.company_number
    row.company_status = m.company_status
    row.company_category = m.company_category
    row.incorporation_date = m.incorporation_date
    row.dissolution_date = m.dissolution_date
    row.sic_codes = m.sic_codes
    row.region = m.region
    row.post_town = m.post_town
    row.country = m.country
    row.accounts_category = m.accounts_category
    row.match_confidence = m.match_confidence
    row.match_method = m.match_method
    row.match_evidence = m.match_evidence
    row.provider = m.provider
    row.error = m.error


def _save_web_enrichment(session: Session, opp: Opportunity) -> None:
    w = opp.web
    row = session.execute(
        select(WebEnrichmentRow).where(WebEnrichmentRow.dedupe_key == opp.dedupe_key)
    ).scalar_one_or_none()
    if row is None:
        row = WebEnrichmentRow(dedupe_key=opp.dedupe_key)
        session.add(row)
    row.provider = w.provider
    row.attempted = w.attempted
    row.website = w.website
    row.contact_page = w.contact_page
    row.brand_description = w.brand_description
    row.website_maturity = w.website_maturity
    row.products_on_sale = w.products_on_sale
    row.marketplace_presence = w.marketplace_presence
    row.major_retailer_presence = w.major_retailer_presence
    row.social_presence = w.social_presence
    row.retail_presence = w.retail_presence.value
    row.launch_evidence = w.launch_evidence
    row.evidence_urls = w.evidence_urls
    row.error = w.error
    row.enriched_at = w.enriched_at


# -- runs ------------------------------------------------------------------


def save_run(session: Session, result: PipelineResult, mode: str = "weekly") -> PipelineRun:
    row = session.execute(
        select(PipelineRun).where(PipelineRun.run_id == result.run_id)
    ).scalar_one_or_none()
    if row is None:
        row = PipelineRun(run_id=result.run_id)
        session.add(row)
    row.mode = mode
    row.journal_number = result.journal.journal_number
    row.source_name = result.journal.source_name
    row.status = result.status.value
    row.started_at = result.started_at
    row.finished_at = result.finished_at
    row.counts = result.counts.model_dump()
    row.warnings = result.warnings
    row.blocked_reason = result.blocked_reason
    row.csv_path = result.csv_path
    row.email_html_path = result.email_html_path
    row.qa_report_path = result.qa_report_path
    for message in result.errors:
        session.add(
            ErrorLog(run_id=result.run_id, stage="pipeline", severity="error", message=message)
        )
    session.flush()
    return row


def recent_run_counts(session: Session, limit: int = 6) -> list[dict[str, Any]]:
    rows = session.execute(
        select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)
    ).scalars()
    return [r.counts or {} for r in rows]


def known_applicant_names(session: Session, before_journal: str | None = None) -> set[str]:
    """Applicants we have already seen filing, for the 'first trade mark' signal."""
    stmt = select(func.lower(TrademarkRecordRow.applicant_name)).where(
        TrademarkRecordRow.applicant_name.is_not(None)
    )
    if before_journal:
        stmt = stmt.where(TrademarkRecordRow.journal_number < before_journal)
    return {name for (name,) in session.execute(stmt.distinct()) if name}


# -- suppression -----------------------------------------------------------


def active_suppressions(session: Session) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in session.execute(
        select(SuppressionRule).where(SuppressionRule.active.is_(True))
    ).scalars():
        out.setdefault(row.rule_type, set()).add(row.value.strip().lower())
    return out


def add_suppression(
    session: Session, rule_type: str, value: str, reason: str | None, created_by: str | None = None
) -> SuppressionRule:
    row = session.execute(
        select(SuppressionRule).where(
            SuppressionRule.rule_type == rule_type, SuppressionRule.value == value
        )
    ).scalar_one_or_none()
    if row is None:
        row = SuppressionRule(rule_type=rule_type, value=value)
        session.add(row)
    row.reason = reason
    row.created_by = created_by
    row.active = True
    session.flush()
    return row


# -- deliveries ------------------------------------------------------------


def record_delivery(
    session: Session,
    run_id: str,
    journal_number: str,
    recipient_email: str,
    idempotency_key: str,
    customer_id: int | None = None,
    kind: str = "weekly_feed",
    opportunity_count: int = 0,
) -> tuple[Delivery, bool]:
    """Returns (delivery, created). An existing key means: already delivered."""
    row = session.execute(
        select(Delivery).where(Delivery.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if row is not None:
        return row, False
    row = Delivery(
        run_id=run_id,
        journal_number=journal_number,
        customer_id=customer_id,
        recipient_email=recipient_email,
        kind=kind,
        opportunity_count=opportunity_count,
        idempotency_key=idempotency_key,
    )
    session.add(row)
    session.flush()
    return row, True
