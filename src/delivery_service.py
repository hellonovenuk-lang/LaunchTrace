"""Sending the weekly feed to customers.

Two gates stand between a completed run and a customer's inbox:

1. ``SEND_MODE`` — ``review`` (the default) never sends automatically; a run
   must be approved by an operator first.
2. Run status — a blocked or failed run is never sent, in either mode.

Delivery is idempotent per (run, recipient): re-running the send command cannot
double-send.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.repository import record_delivery
from src.db.tables import Customer, CustomerPreference, PipelineRun
from src.deliver.email_render import RenderedEmail, render_alert_email, render_weekly_email
from src.deliver.resend_client import EmailSender
from src.errors import DeliveryBlockedError
from src.logging_setup import get_logger
from src.models import PipelineResult, RunStatus, ScoreBand
from src.settings import Settings, get_settings

log = get_logger(__name__)

BAND_RANK = {"HIGH": 2, "MEDIUM": 1, "SUPPRESS": 0}


@dataclass
class DeliverySummary:
    attempted: int = 0
    sent: int = 0
    skipped_already_sent: int = 0
    rendered_not_sent: int = 0
    failed: int = 0
    blocked_reason: str | None = None
    details: list[str] = field(default_factory=list)


def active_customers(session: Session) -> list[Customer]:
    return list(
        session.execute(
            select(Customer).where(
                Customer.delivery_enabled.is_(True),
                Customer.subscription_status.in_(["active", "trialing", "past_due"]),
            )
        ).scalars()
    )


def recipients_for(session: Session, customer: Customer) -> list[CustomerPreference]:
    return list(
        session.execute(
            select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
        ).scalars()
    )


def _filter_for_preference(result: PipelineResult, pref: CustomerPreference):  # type: ignore[no-untyped-def]
    minimum = BAND_RANK.get(pref.min_score_band, 1)
    out = []
    for opp in result.deliverable:
        if BAND_RANK.get(opp.score.band.value, 0) < minimum:
            continue
        if pref.product_categories and opp.product_category not in pref.product_categories:
            continue
        if pref.regions and (opp.company.region or "") not in pref.regions:
            continue
        out.append(opp)
    return sorted(out, key=lambda o: o.score.value, reverse=True)


def deliver_weekly(
    session: Session,
    result: PipelineResult,
    csv_path: str | Path | None = None,
    settings: Settings | None = None,
    force: bool = False,
    sender: EmailSender | None = None,
) -> DeliverySummary:
    settings = settings or get_settings()
    summary = DeliverySummary()
    sender = sender or EmailSender(settings)

    if result.status != RunStatus.COMPLETED:
        summary.blocked_reason = f"Run status is {result.status.value}" + (
            f" ({result.blocked_reason})" if result.blocked_reason else ""
        )
        log.warning("delivery.blocked", reason=summary.blocked_reason)
        return summary

    run_row = session.execute(
        select(PipelineRun).where(PipelineRun.run_id == result.run_id)
    ).scalar_one_or_none()
    approved = bool(run_row and run_row.approved_at)

    if settings.send_mode == "review" and not (approved or force):
        summary.blocked_reason = (
            "SEND_MODE=review and this run has not been approved. "
            f"Approve it with: python -m src.pipeline approve --run-id {result.run_id}"
        )
        log.info("delivery.awaiting_approval", run_id=result.run_id)
        return summary

    attachments = [Path(csv_path)] if csv_path and Path(csv_path).exists() else []

    for customer in active_customers(session):
        for pref in recipients_for(session, customer):
            summary.attempted += 1
            opportunities = _filter_for_preference(result, pref)
            filtered = result.model_copy(deep=True)
            filtered.opportunities = opportunities
            rendered: RenderedEmail = render_weekly_email(filtered, settings=settings)
            key = f"{result.run_id}:{pref.recipient_email}:weekly_feed"
            delivery, created = record_delivery(
                session,
                run_id=result.run_id,
                journal_number=result.journal.journal_number,
                recipient_email=pref.recipient_email,
                idempotency_key=key,
                customer_id=customer.id,
                opportunity_count=len(opportunities),
            )
            if not created and delivery.status in {"sent", "rendered_not_sent"}:
                summary.skipped_already_sent += 1
                summary.details.append(f"{pref.recipient_email}: already delivered for this run")
                continue

            send_result = sender.send([pref.recipient_email], rendered, attachments, key)
            delivery.status = send_result.status
            delivery.provider_message_id = send_result.message_id
            delivery.error = send_result.error
            if send_result.status == "sent":
                delivery.sent_at = datetime.now(UTC)
                summary.sent += 1
            elif send_result.status == "rendered_not_sent":
                summary.rendered_not_sent += 1
                summary.details.append(f"{pref.recipient_email}: written to {send_result.path}")
            else:
                summary.failed += 1
                summary.details.append(f"{pref.recipient_email}: {send_result.error}")

    for opp in result.opportunities:
        if not opp.suppressed and opp.score.band in (ScoreBand.HIGH, ScoreBand.MEDIUM):
            opp.delivered = summary.sent > 0 or summary.rendered_not_sent > 0

    if run_row is not None:
        run_row.delivery_status = (
            "sent"
            if summary.sent
            else "rendered_not_sent"
            if summary.rendered_not_sent
            else "not_sent"
        )
    session.flush()
    log.info(
        "delivery.finished",
        run_id=result.run_id,
        sent=summary.sent,
        rendered=summary.rendered_not_sent,
        failed=summary.failed,
    )
    return summary


def send_failure_alert(
    result: PipelineResult, detail: str, settings: Settings | None = None
) -> None:
    """Tell the operator a run failed. Never blocks the pipeline itself."""
    settings = settings or get_settings()
    if not settings.admin_email:
        log.warning("alert.no_admin_email", run_id=result.run_id)
        return
    rendered = render_alert_email(
        title=result.blocked_reason or "Pipeline run did not complete",
        run_id=result.run_id,
        journal_number=result.journal.journal_number,
        status=result.status.value,
        detail=detail,
    )
    EmailSender(settings).send([settings.admin_email], rendered)


def require_deliverable(result: PipelineResult) -> None:
    if result.status != RunStatus.COMPLETED:
        raise DeliveryBlockedError(
            f"Run {result.run_id} is {result.status.value}: {result.blocked_reason}"
        )
