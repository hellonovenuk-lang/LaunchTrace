"""Data retention: anonymise, clear or delete data past its retention period.

Periods and switches live in ``config/retention.json`` (every period there is a
placeholder pending the owner's decision). ``python -m src.pipeline retention``
reports what *would* change, per data class; ``--apply`` changes it.

Rules this module keeps whatever the config says:

* the suppression lists (``suppression_rules``, ``prospect_suppressions``) are
  never read for deletion, never updated, never deleted;
* ``customers`` and ``customer_preferences`` are never touched, and nothing
  linked to a customer whose subscription is not cancelled is touched;
* ``journals`` metadata (the duplicate-processing guard) is never touched;
* ``brands``, ``observations`` and ``stage_changes`` hold no personal data by
  design (the applicant appears only as a hash) and are not touched;
* each class runs in its own transaction, and a dry run and an apply count the
  same rows, so a second apply reports zero.

Retention is housekeeping. It never runs inside a pipeline run, so it cannot
move a score (the stability snapshot never calls it).
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.db.tables import (
    CompanyMatchRow,
    Customer,
    Delivery,
    ErrorLog,
    LeadFeedback,
    OpportunityRow,
    PipelineRun,
    ProspectStateRow,
    SampleRequest,
    TrademarkRecordRow,
    WebEnrichmentRow,
    WebhookEvent,
)
from src.logging_setup import get_logger
from src.privacy import is_individual_applicant, looks_corporate
from src.settings import load_config

log = get_logger(__name__)

# Tables retention must never modify. Checked by tests against every handler.
PROTECTED_TABLES = frozenset(
    {
        "suppression_rules",
        "prospect_suppressions",
        "customers",
        "customer_preferences",
        "journals",
        "brands",
        "observations",
        "stage_changes",
    }
)

_PROSPECT_DATE_FIELDS = (
    "email_1_sent_date",
    "sample_requested_date",
    "sample_sent_date",
    "offer_sent_date",
    "converted_date",
    "follow_up_due_date",
)


@dataclass
class ClassReport:
    """What retention found (dry run) or did (apply) for one data class."""

    key: str
    table: str
    action: str
    cutoff: datetime | None
    affected: int = 0
    applied: bool = False
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "table": self.table,
            "action": self.action,
            "cutoff": self.cutoff.isoformat() if self.cutoff else None,
            "affected": self.affected,
            "applied": self.applied,
            "skipped_reason": self.skipped_reason,
        }


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------


def _months_before(moment: datetime, months: int) -> datetime:
    year, month = moment.year, moment.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def cutoff_for(cfg: dict[str, Any], now: datetime) -> datetime:
    """The moment before which data in this class is past retention."""
    has_months, has_days = "months" in cfg, "days" in cfg
    if has_months == has_days:
        raise ValueError("a retention class needs exactly one of 'months' or 'days'")
    if has_months:
        months = int(cfg["months"])
        if months < 1:
            raise ValueError("retention 'months' must be at least 1")
        return _months_before(now, months)
    days = int(cfg["days"])
    if days < 1:
        raise ValueError("retention 'days' must be at least 1")
    return now - timedelta(days=days)


def _as_utc(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _older(value: datetime | date | None, cutoff: datetime) -> bool:
    moment = _as_utc(value)
    return moment is not None and moment < cutoff


# ---------------------------------------------------------------------------
# handlers: each returns the ids/rows it would change, and changes them if asked
# ---------------------------------------------------------------------------

Handler = Callable[[Session, datetime, dict[str, Any], bool], int]


def _anonymise(session: Session, model: Any, ids: list[int], apply: bool, **extra: Any) -> int:
    if apply and ids:
        values = {"applicant_name": None, **extra}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            session.execute(update(model).where(model.id.in_(chunk)).values(**values))
    return len(ids)


def _trademark_records(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
    rows = session.execute(
        select(
            TrademarkRecordRow.id,
            TrademarkRecordRow.applicant_name,
            TrademarkRecordRow.publication_date,
            TrademarkRecordRow.created_at,
        ).where(TrademarkRecordRow.applicant_name.is_not(None))
    ).all()
    ids = [
        r.id
        for r in rows
        if _older(r.publication_date or r.created_at, cutoff)
        and not looks_corporate(r.applicant_name)
    ]
    return _anonymise(session, TrademarkRecordRow, ids, apply)


def _opportunities(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
    rows = session.execute(
        select(
            OpportunityRow.id,
            OpportunityRow.applicant_name,
            OpportunityRow.applicant_type,
            OpportunityRow.publication_date,
            OpportunityRow.created_at,
        ).where(OpportunityRow.applicant_name.is_not(None))
    ).all()
    ids = [
        r.id
        for r in rows
        if _older(r.publication_date or r.created_at, cutoff)
        and is_individual_applicant(r.applicant_name, r.applicant_type)
    ]
    return _anonymise(session, OpportunityRow, ids, apply)


def _company_matches(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
    rows = session.execute(
        select(
            CompanyMatchRow.id, CompanyMatchRow.applicant_name, CompanyMatchRow.created_at
        ).where(CompanyMatchRow.applicant_name.is_not(None))
    ).all()
    ids = [
        r.id for r in rows if _older(r.created_at, cutoff) and not looks_corporate(r.applicant_name)
    ]
    # The match evidence quotes words of the applicant's name, and a provider
    # error can quote the request URL that carried it: both go with the name.
    return _anonymise(session, CompanyMatchRow, ids, apply, error=None, match_evidence=[])


def _delete_ids(session: Session, model: Any, ids: list[int], apply: bool) -> int:
    if apply and ids:
        for start in range(0, len(ids), 500):
            session.execute(delete(model).where(model.id.in_(ids[start : start + 500])))
    return len(ids)


def _delete_older(model: Any, column_name: str) -> Handler:
    def handler(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
        column = getattr(model, column_name)
        rows = session.execute(select(model.id, column).where(column.is_not(None))).all()
        ids = [r[0] for r in rows if _older(r[1], cutoff)]
        return _delete_ids(session, model, ids, apply)

    return handler


def _live_customer_ids(session: Session) -> set[int]:
    return {
        cid
        for (cid,) in session.execute(
            select(Customer.id).where(Customer.subscription_status != "cancelled")
        )
    }


def _live_prospect_ids(session: Session) -> set[str]:
    return {
        pid
        for (pid,) in session.execute(
            select(Customer.prospect_id).where(
                Customer.subscription_status != "cancelled", Customer.prospect_id.is_not(None)
            )
        )
        if pid
    }


def _deliveries(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
    live = _live_customer_ids(session)
    rows = session.execute(select(Delivery.id, Delivery.created_at, Delivery.customer_id)).all()
    ids = [r.id for r in rows if _older(r.created_at, cutoff) and r.customer_id not in live]
    return _delete_ids(session, Delivery, ids, apply)


def _sample_requests(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
    rows = session.execute(
        select(SampleRequest.id, SampleRequest.created_at, SampleRequest.sent_at)
    ).all()
    ids = []
    for r in rows:
        moments = [m for m in (_as_utc(r.created_at), _as_utc(r.sent_at)) if m is not None]
        if moments and max(moments) < cutoff:
            ids.append(r.id)
    return _delete_ids(session, SampleRequest, ids, apply)


def _has_content(values: Iterable[Any]) -> bool:
    return any(v not in (None, "") for v in values)


def _prospect_last_interaction(row: ProspectStateRow) -> datetime | None:
    moments = (_as_utc(getattr(row, name)) for name in _PROSPECT_DATE_FIELDS)
    dated = [d for d in moments if d is not None]
    if dated:
        return max(dated)
    return _as_utc(row.date_added) or _as_utc(row.created_at)


def _prospect_contacts(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
    fields = list(cfg.get("fields") or [])
    allowed = {"generic_contact_email", "named_contact", "decision_maker_role", "notes"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"prospect_contacts cannot clear {sorted(unknown)}")
    live = _live_prospect_ids(session)
    changed = 0
    for row in session.execute(select(ProspectStateRow)).scalars():
        if row.prospect_id in live:
            continue
        last = _prospect_last_interaction(row)
        if last is None or last >= cutoff:
            continue
        if not _has_content(getattr(row, f) for f in fields):
            continue
        changed += 1
        if apply:
            for f in fields:
                setattr(row, f, "")
    return changed


def _lead_feedback_notes(session: Session, cutoff: datetime, cfg: dict, apply: bool) -> int:
    rows = session.execute(
        select(LeadFeedback.id, LeadFeedback.created_at).where(
            LeadFeedback.note.is_not(None), LeadFeedback.note != ""
        )
    ).all()
    ids = [r.id for r in rows if _older(r.created_at, cutoff)]
    if apply and ids:
        session.execute(update(LeadFeedback).where(LeadFeedback.id.in_(ids)).values(note=None))
    return len(ids)


HANDLERS: dict[str, tuple[str, Handler]] = {
    "trademark_records_individual_names": ("trademark_records", _trademark_records),
    "opportunities_individual_names": ("opportunities", _opportunities),
    "company_matches_individual_names": ("company_matches", _company_matches),
    "web_enrichment": ("web_enrichment", _delete_older(WebEnrichmentRow, "enriched_at")),
    "pipeline_runs": ("pipeline_runs", _delete_older(PipelineRun, "started_at")),
    "errors": ("errors", _delete_older(ErrorLog, "created_at")),
    "deliveries": ("deliveries", _deliveries),
    "sample_requests": ("sample_requests", _sample_requests),
    "prospect_contacts": ("prospect_state", _prospect_contacts),
    "lead_feedback_notes": ("lead_feedback", _lead_feedback_notes),
    "webhook_events": ("webhook_events", _delete_older(WebhookEvent, "processed_at")),
}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run_retention(
    session_factory: Callable[[], Session],
    *,
    apply: bool = False,
    now: datetime | None = None,
    config: dict[str, Any] | None = None,
) -> list[ClassReport]:
    """Run every configured class, each in its own transaction.

    ``session_factory`` returns a fresh session; each class commits (apply) or
    rolls back (dry run) independently, so one failing class cannot leave
    another half done.
    """
    now = _as_utc(now) or datetime.now(UTC)
    config = config if config is not None else load_config("retention.json")
    classes = config.get("classes", {})
    unknown = set(classes) - set(HANDLERS)
    if unknown:
        raise ValueError(f"Unknown retention classes in config: {sorted(unknown)}")

    reports: list[ClassReport] = []
    for key, (table, handler) in HANDLERS.items():
        cfg = classes.get(key)
        if cfg is None or not cfg.get("enabled", True):
            reports.append(
                ClassReport(key, table, "none", None, skipped_reason="disabled or not configured")
            )
            continue
        if table in PROTECTED_TABLES:  # pragma: no cover - guarded by tests
            raise RuntimeError(f"retention must never touch {table}")
        cutoff = cutoff_for(cfg, now)
        report = ClassReport(key, table, str(cfg.get("action", "")), cutoff)
        session = session_factory()
        try:
            report.affected = handler(session, cutoff, cfg, apply)
            if apply:
                session.commit()
                report.applied = True
            else:
                session.rollback()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        log.info(
            "retention.class",
            key=key,
            table=table,
            action=report.action,
            cutoff=cutoff.isoformat(),
            affected=report.affected,
            applied=report.applied,
        )
        reports.append(report)
    log.info(
        "retention.done",
        applied=apply,
        total=sum(r.affected for r in reports),
    )
    return reports


def format_report(reports: list[ClassReport], applied: bool) -> str:
    title = "Retention — applied" if applied else "Retention — dry run (nothing changed)"
    lines = [title, ""]
    lines.append(f"{'class':<38} {'table':<20} {'action':<13} {'cutoff':<11} {'rows':>6}")
    for r in reports:
        cutoff = r.cutoff.date().isoformat() if r.cutoff else "-"
        rows = str(r.affected) if r.skipped_reason is None else "skip"
        lines.append(f"{r.key:<38} {r.table:<20} {r.action:<13} {cutoff:<11} {rows:>6}")
    lines.append("")
    lines.append(
        "Periods in config/retention.json are placeholders pending owner decision. "
        "Suppression lists, customers, journals and brands are never touched."
    )
    if not applied:
        lines.append("Run with --apply to make these changes.")
    return "\n".join(lines)


def cmd_retention(args: Any) -> int:
    """CLI: ``python -m src.pipeline retention [--apply] [--json]``. Dry run by default."""
    import json

    from src.db import get_session, init_db

    init_db()
    apply = bool(getattr(args, "apply", False))
    reports = run_retention(get_session, apply=apply)
    if getattr(args, "json", False):
        print(json.dumps([r.as_dict() for r in reports], indent=2))
    else:
        print(format_report(reports, applied=apply))
    return 0


__all__ = [
    "HANDLERS",
    "cmd_retention",
    "PROTECTED_TABLES",
    "ClassReport",
    "cutoff_for",
    "format_report",
    "run_retention",
]
