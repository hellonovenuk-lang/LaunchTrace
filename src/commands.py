"""CLI command implementations.

Kept separate from ``src/pipeline.py`` so argument parsing stays readable and the
commands can be exercised directly from tests.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

from src.db import init_db, session_scope
from src.db.repository import (
    add_suppression,
    journal_already_processed,
    known_applicant_names,
    recent_run_counts,
    save_opportunities,
    save_run,
    save_trademark_records,
    upsert_journal,
)
from src.db.tables import Customer, CustomerPreference, ErrorLog, OpportunityRow, PipelineRun
from src.deliver.csv_export import write_opportunities_csv
from src.deliver.qa_report import format_qa_report
from src.delivery_service import deliver_weekly, send_failure_alert
from src.ingest.base import get_source
from src.ingest.discovery import previous_journal_dates
from src.logging_setup import get_logger
from src.models import PipelineResult, RunStatus
from src.pipeline_core import Pipeline
from src.settings import DATA_DIR, REPORTS_DIR, get_settings

log = get_logger(__name__)


def _fresh_settings(args) -> object:  # type: ignore[no-untyped-def]
    settings = get_settings()
    if getattr(args, "source", None):
        settings = settings.model_copy(update={"journal_source": args.source})
    return settings


def _run_one(
    args,  # type: ignore[no-untyped-def]
    publication_date: date | None = None,
    journal_number: str | None = None,
    write_db: bool = True,
    history: list[dict] | None = None,
) -> PipelineResult:
    settings = _fresh_settings(args)
    source = get_source(settings=settings)  # type: ignore[arg-type]
    pipeline = Pipeline(settings=settings, source=source)  # type: ignore[arg-type]

    known: set[str] = set()
    if write_db:
        init_db()
        with session_scope() as session:
            known = known_applicant_names(session)
            if history is None:
                history = recent_run_counts(session)

    result = pipeline.run(
        journal_number=journal_number,
        publication_date=publication_date,
        max_records=getattr(args, "max_records", None),
        known_applicants=known,
        history=history,
    )

    if write_db:
        with session_scope() as session:
            if result.status == RunStatus.COMPLETED:
                try:
                    # Reuse what the run already downloaded and parsed. Re-reading
                    # a 150 MB journal purely to persist it would double the cost
                    # of every run.
                    artifact = pipeline.last_artifact
                    if artifact is not None:
                        journal_row = upsert_journal(session, artifact, result.counts.raw_records)
                        save_trademark_records(session, pipeline.last_records, journal_row.id)
                except Exception as exc:  # persistence must not lose the run record
                    log.warning("commands.persist_source_failed", error=str(exc)[:200])
                save_opportunities(session, result)
            save_run(session, result, mode=getattr(args, "command", "weekly"))
    return result


def _print_result(result: PipelineResult) -> None:
    qa_path = Path(result.qa_report_path) if result.qa_report_path else None
    if qa_path and qa_path.exists():
        print(format_qa_report(json.loads(qa_path.read_text(encoding="utf-8"))))
    print()
    print(f"CSV:   {result.csv_path or '-'}")
    print(f"Email: {result.email_html_path or '-'}")
    print(f"QA:    {result.qa_report_path or '-'}")
    if result.status != RunStatus.COMPLETED:
        print(f"\nSTATUS: {result.status.value.upper()} — {result.blocked_reason}")


# ---------------------------------------------------------------------------
# pipeline commands
# ---------------------------------------------------------------------------


def cmd_weekly(args) -> int:  # type: ignore[no-untyped-def]
    from src.pipeline import parse_date_arg

    result = _run_one(
        args,
        publication_date=parse_date_arg(getattr(args, "date", None)),
        journal_number=getattr(args, "journal", None),
        write_db=not getattr(args, "no_db", False),
    )
    _print_result(result)

    if result.status != RunStatus.COMPLETED:
        send_failure_alert(result, detail="\n".join(result.errors) or "no detail")
        return 2

    if getattr(args, "send", False):
        with session_scope() as session:
            summary = deliver_weekly(session, result, csv_path=result.csv_path)
        print()
        if summary.blocked_reason:
            print(f"Delivery not performed: {summary.blocked_reason}")
        else:
            print(
                f"Delivery: attempted {summary.attempted}, sent {summary.sent}, "
                f"written to outbox {summary.rendered_not_sent}, failed {summary.failed}"
            )
            for detail in summary.details:
                print(f"  {detail}")
    return 0


def cmd_backfill(args) -> int:  # type: ignore[no-untyped-def]
    settings = _fresh_settings(args)
    source = get_source(settings=settings)  # type: ignore[arg-type]
    weeks = int(getattr(args, "weeks", 4))

    if source.name == "ipo_open_data":
        refs = list(reversed(source.available_refs(weeks)))
        dates = [r.publication_date for r in refs][-weeks:]
    else:
        dates = previous_journal_dates(weeks)

    if not dates:
        print(
            "No journals available to backfill. Try: python -m src.pipeline fetch-open-data --weeks 4"
        )
        return 1

    history: list[dict] = []
    failures = 0
    for publication_date in dates:
        print(f"\n=== Journal week {publication_date.isoformat()} ===")
        with session_scope() as session:
            ref = source.ref_for(publication_date=publication_date)
            if journal_already_processed(
                session, ref.source_name, ref.journal_number
            ) and not getattr(args, "no_db", False):
                print(f"Already processed ({ref.journal_number}) — skipping.")
                continue
        result = _run_one(
            args,
            publication_date=publication_date,
            write_db=not getattr(args, "no_db", False),
            history=history,
        )
        _print_result(result)
        history.append(result.counts.model_dump())
        if result.status != RunStatus.COMPLETED:
            failures += 1
    return 1 if failures else 0


def cmd_smoke_test(args) -> int:  # type: ignore[no-untyped-def]
    """End-to-end run on fixture data: no network, no credentials, no database."""
    from src.classify.pipeline import ProductClassifier
    from src.enrich.companies_house import FixtureCompanyRegistry
    from src.enrich.providers import FixtureSearchProvider
    from src.enrich.web import WebEnricher
    from src.ingest.fixture import FixtureJournalSource

    out = Path(getattr(args, "out", None) or (REPORTS_DIR / "smoke"))
    settings = get_settings().model_copy(
        update={"journal_source": "fixture", "send_mode": "review"}
    )
    source = FixtureJournalSource(settings)
    pipeline = Pipeline(
        settings=settings,
        source=source,
        registry=FixtureCompanyRegistry(),
        classifier=ProductClassifier(settings, llm_provider=None),
        web=WebEnricher(provider=FixtureSearchProvider(), settings=settings),
        output_dir=out,
    )
    result = pipeline.run(write_outputs=True)
    _print_result(result)

    validation: dict[str, object] = {
        "status": result.status.value,
        "raw_records": result.counts.raw_records,
        "opportunities": len(result.opportunities),
        "deliverable": len(result.deliverable),
        "high": result.counts.high,
        "medium": result.counts.medium,
        "suppressed": result.counts.suppressed,
        "csv_written": bool(result.csv_path and Path(result.csv_path).exists()),
        "email_written": bool(result.email_html_path and Path(result.email_html_path).exists()),
        "qa_written": bool(result.qa_report_path and Path(result.qa_report_path).exists()),
    }
    summary_path = out / result.journal.journal_number / "smoke_validation.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(validation, indent=2), encoding="utf-8")
    print(f"\nSmoke validation summary: {summary_path}")

    deliverable_count = len(result.deliverable)
    ok = (
        result.status == RunStatus.COMPLETED
        and bool(validation["csv_written"])
        and bool(validation["email_written"])
        and deliverable_count > 0
    )
    print("SMOKE TEST: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def cmd_validate(args) -> int:  # type: ignore[no-untyped-def]
    from src.validation import run_validation

    return run_validation(
        weeks=int(getattr(args, "weeks", 4)), source_name=getattr(args, "source", "open_data")
    )


# ---------------------------------------------------------------------------
# operator commands
# ---------------------------------------------------------------------------


def cmd_status(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        runs = list(
            session.execute(
                select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(20)
            ).scalars()
        )
    if not runs:
        print("No pipeline runs recorded yet.")
        return 0
    print(
        f"{'run_id':<32} {'journal':<10} {'status':<10} {'high':>5} {'med':>5} {'approved':<10} delivery"
    )
    for r in runs:
        counts = r.counts or {}
        print(
            f"{r.run_id:<32} {(r.journal_number or '-'):<10} {r.status:<10} "
            f"{counts.get('high', 0):>5} {counts.get('medium', 0):>5} "
            f"{('yes' if r.approved_at else 'no'):<10} {r.delivery_status}"
        )
    return 0


def cmd_approve(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        run = session.execute(
            select(PipelineRun).where(PipelineRun.run_id == args.run_id)
        ).scalar_one_or_none()
        if run is None:
            print(f"No run found with id {args.run_id}")
            return 1
        if run.status != "completed":
            print(f"Run {args.run_id} is {run.status} and cannot be approved.")
            return 1
        run.approved_at = datetime.now(UTC)
        run.approved_by = args.by
    print(
        f"Approved run {args.run_id}. Send it with: python -m src.pipeline send --run-id {args.run_id}"
    )
    return 0


def cmd_send(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        run = session.execute(
            select(PipelineRun).where(PipelineRun.run_id == args.run_id)
        ).scalar_one_or_none()
        if run is None:
            print(f"No run found with id {args.run_id}")
            return 1
        result = _rehydrate_result(session, run)
        summary = deliver_weekly(
            session, result, csv_path=run.csv_path, force=bool(getattr(args, "force", False))
        )
    if summary.blocked_reason:
        print(f"Delivery not performed: {summary.blocked_reason}")
        return 1
    print(
        f"Delivery: attempted {summary.attempted}, sent {summary.sent}, "
        f"written to outbox {summary.rendered_not_sent}, failed {summary.failed}"
    )
    for detail in summary.details:
        print(f"  {detail}")
    return 0 if summary.failed == 0 else 1


def _rehydrate_result(session, run: PipelineRun) -> PipelineResult:  # type: ignore[no-untyped-def]
    """Rebuild enough of a PipelineResult from the database to re-send a run."""
    from src.ingest.discovery import date_for_journal_number
    from src.models import (
        BuyingIntent,
        CompanyMatch,
        FunnelCounts,
        JournalRef,
        LaunchStage,
        Opportunity,
        Relevance,
        RetailPresence,
        Score,
        ScoreBand,
        ScoreReason,
        WebEnrichment,
    )

    rows = list(
        session.execute(
            select(OpportunityRow).where(OpportunityRow.journal_number == run.journal_number)
        ).scalars()
    )
    opportunities: list[Opportunity] = []
    for row in rows:
        intent = row.buying_intent or {}
        opportunities.append(
            Opportunity(
                dedupe_key=row.dedupe_key,
                trademark_number=row.trademark_number,
                brand_name=row.brand_name,
                filing_date=row.filing_date,
                publication_date=row.publication_date,
                journal_number=row.journal_number,
                goods_summary=row.goods_summary,
                product_category=row.product_category,
                applicant_name=row.applicant_name,
                nice_classes=row.nice_classes or [],
                company=CompanyMatch(
                    matched=bool(row.company_number),
                    company_name=row.company_name,
                    company_number=row.company_number,
                    incorporation_date=row.company_incorporation_date,
                    region=row.company_region,
                ),
                web=WebEnrichment(website=row.website, contact_page=row.contact_page),
                score=Score(
                    value=row.launchtrace_score,
                    band=ScoreBand(row.score_band),
                    reasons=[
                        ScoreReason(key="stored", text=t, weight=0)
                        for t in (row.score_reasons or [])
                    ],
                ),
                buying_intent=BuyingIntent(
                    **{k: Relevance(v) for k, v in intent.items() if k in BuyingIntent.model_fields}
                ),
                launch_stage=LaunchStage(row.launch_stage),
                retail_presence=RetailPresence(row.retail_presence),
                company_age_years_at_filing=row.company_age_years_at_filing,
                source_url=row.source_url,
                evidence_urls=row.evidence_urls or [],
                suppressed=row.suppressed,
            )
        )
    return PipelineResult(
        run_id=run.run_id,
        journal=JournalRef(
            journal_number=run.journal_number or "",
            publication_date=date_for_journal_number(run.journal_number)
            if run.journal_number
            else date.today(),
            source_name=run.source_name,
        ),
        status=RunStatus(run.status),
        counts=FunnelCounts(**(run.counts or {})),
        opportunities=opportunities,
        csv_path=run.csv_path,
    )


def cmd_regenerate_csv(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        run = (
            session.execute(
                select(PipelineRun)
                .where(PipelineRun.journal_number == args.journal)
                .order_by(PipelineRun.started_at.desc())
            )
            .scalars()
            .first()
        )
        if run is None:
            print(f"No run found for journal {args.journal}")
            return 1
        result = _rehydrate_result(session, run)
    path = REPORTS_DIR / "runs" / args.journal / "opportunities.csv"
    write_opportunities_csv(
        sorted(result.deliverable, key=lambda o: o.score.value, reverse=True), path
    )
    print(f"Wrote {path}")
    return 0


def cmd_opportunities(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        stmt = select(OpportunityRow).order_by(OpportunityRow.launchtrace_score.desc())
        if getattr(args, "journal", None):
            stmt = stmt.where(OpportunityRow.journal_number == args.journal)
        if getattr(args, "band", None):
            stmt = stmt.where(OpportunityRow.score_band == args.band)
        rows = list(session.execute(stmt.limit(args.limit)).scalars())
    if not rows:
        print("No opportunities found.")
        return 0
    print(f"{'score':>5} {'band':<9} {'brand':<28} {'company':<32} {'category':<20} tm")
    for r in rows:
        print(
            f"{r.launchtrace_score:>5} {r.score_band:<9} {(r.brand_name or '')[:27]:<28} "
            f"{(r.company_name or r.applicant_name or '')[:31]:<32} {(r.product_category or '')[:19]:<20} "
            f"{r.trademark_number}"
        )
    return 0


def cmd_customers(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        customers = list(session.execute(select(Customer)).scalars())
        prefs: dict[int, list[str]] = {}
        for p in session.execute(select(CustomerPreference)).scalars():
            prefs.setdefault(p.customer_id, []).append(p.recipient_email)
    if not customers:
        print(
            "No customers configured. Add one with: python -m src.pipeline add-customer --company 'X' --email a@b.com"
        )
        return 0
    print(f"{'id':>4} {'company':<32} {'plan':<18} {'status':<12} {'delivery':<9} recipients")
    for c in customers:
        print(
            f"{c.id:>4} {c.company[:31]:<32} {c.plan_key:<18} {c.subscription_status:<12} "
            f"{('on' if c.delivery_enabled else 'off'):<9} {', '.join(prefs.get(c.id, []))}"
        )
    return 0


def cmd_add_customer(args) -> int:  # type: ignore[no-untyped-def]
    from src.settings import load_config

    plans = {p["key"]: p for p in load_config("customer_plans.json")["plans"]}
    plan = plans.get(args.plan)
    if plan is None:
        print(f"Unknown plan {args.plan}. Available: {', '.join(plans)}")
        return 1
    emails = list(dict.fromkeys(args.email))
    if len(emails) > plan["max_recipients"]:
        print(f"Plan {args.plan} allows {plan['max_recipients']} recipients; {len(emails)} given.")
        return 1
    init_db()
    with session_scope() as session:
        customer = Customer(
            company=args.company,
            supplier_type=getattr(args, "supplier_type", None),
            plan_key=args.plan,
            subscription_status=args.status,
            founding_customer=args.plan == "founding_monthly",
        )
        session.add(customer)
        session.flush()
        for email in emails:
            session.add(CustomerPreference(customer_id=customer.id, recipient_email=email))
    print(f"Added customer '{args.company}' with {len(emails)} recipient(s) on plan {args.plan}.")
    return 0


def cmd_suppress(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        add_suppression(session, args.type, args.value, getattr(args, "reason", None), "cli")
    print(f"Suppressed {args.type}: {args.value}")
    return 0


def cmd_errors(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        rows = list(
            session.execute(
                select(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(30)
            ).scalars()
        )
    if not rows:
        print("No errors recorded.")
        return 0
    for r in rows:
        print(
            f"{r.created_at:%Y-%m-%d %H:%M} [{r.severity}] {r.stage} {r.run_id or ''}: {r.message[:160]}"
        )
    return 0


# ---------------------------------------------------------------------------
# data commands
# ---------------------------------------------------------------------------


def cmd_fetch_open_data(args) -> int:  # type: ignore[no-untyped-def]
    from src.ingest.open_data import IpoOpenDataSource

    source = IpoOpenDataSource()
    weeks = int(getattr(args, "weeks", 4))
    print("Downloading the official IPO Open Data release (about 63 MB)…")
    snapshot = source.download_snapshot()
    print(f"Snapshot: {snapshot}")
    dates = source.discover_recent_publication_dates(weeks, snapshot)
    if not dates:
        print("Could not find complete publication weeks in the snapshot.")
        return 1
    print(f"Most recent complete journal weeks: {', '.join(d.isoformat() for d in dates)}")
    paths = source.extract_weeks(dates, snapshot)
    for p in paths:
        print(f"  wrote {p}")
    return 0


def cmd_build_company_index(args) -> int:  # type: ignore[no-untyped-def]
    from src.enrich.companies_house import build_bulk_index, latest_bulk_download_url
    from src.ingest.http_client import HttpClient

    settings = get_settings()
    source = getattr(args, "source", None)
    if not source and getattr(args, "download", False):
        client = HttpClient(user_agent=settings.ukipo_user_agent, timeout=600, max_retries=3)
        url = latest_bulk_download_url(client.get_text)
        if not url:
            print("Could not find a Companies House snapshot on the download page.")
            return 1
        dest = DATA_DIR / "companies_house" / "BasicCompanyData.zip"
        print(f"Downloading {url} (about 500 MB)…")
        client.download(url, dest)
        source = dest
    if not source:
        print(
            "Give --source /path/to/BasicCompanyDataAsOneFile-*.zip, or use --download to fetch it.\n"
            "The file is free and needs no account: https://download.companieshouse.gov.uk/en_output.html"
        )
        return 1
    rows = build_bulk_index(source, settings.companies_house_bulk_index)
    print(f"Indexed {rows:,} companies into {settings.companies_house_bulk_index}")
    return 0


def cmd_init_db(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    print(f"Database ready: {get_settings().database_url.split('@')[-1]}")
    return 0


def cmd_check_config(args) -> int:  # type: ignore[no-untyped-def]
    from src.classify.llm import get_llm_provider
    from src.enrich.companies_house import get_company_registry
    from src.enrich.web import get_search_provider

    settings = get_settings()
    registry = get_company_registry(settings)
    rows = [
        ("Environment", settings.environment),
        (
            "Send mode",
            settings.send_mode
            + (" (nothing sends without approval)" if settings.send_mode == "review" else ""),
        ),
        (
            "Database",
            "PostgreSQL"
            if not settings.is_sqlite
            else f"local SQLite ({settings.database_url.split('///')[-1]})",
        ),
        ("Journal source", settings.journal_source),
        ("Company registry", registry.name),
        ("LLM classifier", get_llm_provider(settings).name),
        ("Web enrichment", get_search_provider(settings).name),
        (
            "Email",
            "Resend (live)"
            if settings.email_enabled
            else "not configured — emails render to reports/outbox/",
        ),
        ("Stripe", "configured" if settings.stripe_enabled else "not configured"),
    ]
    width = max(len(k) for k, _ in rows)
    print("LaunchTrace configuration\n")
    for key, value in rows:
        print(f"  {key:<{width}}  {value}")
    missing = settings.missing_credentials()
    if missing:
        print("\nNot yet connected:")
        for key, what in missing.items():
            print(f"  {key:<28} {what}")
        print("\nSee HANDOFF.md for exactly how to obtain each one.")
    else:
        print("\nAll integrations are connected.")
    return 0


def cmd_probe_journal(args) -> int:  # type: ignore[no-untyped-def]
    from src.parse.journal_xml import probe_structure

    info = probe_structure(args.path)
    print(f"Distinct element names: {info['distinct']}\n")
    for name, count in info["top_elements"]:
        print(f"  {count:>8}  {name}")
    return 0
