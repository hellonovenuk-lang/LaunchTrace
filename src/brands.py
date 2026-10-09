"""Brands across weeks: identity, append-only observations and stage changes.

A weekly run produces opportunities, one per trade mark. This module ties them
to a persistent ``Brand`` — the real-world brand or company — so later work
(backtests, rescans, the public feed) can follow one brand over time.

Dedupe identity (``brand_key``), looked up in this order:

1. a confident Companies House match: ``ch:<company number>``;
2. otherwise ``tm:<sha256(normalised mark text | normalised applicant)[:24]>``.

A brand first seen as ``tm:`` that later gains a company number keeps its id,
uid and key and has ``company_number`` filled in, so the next lookup by that
company number finds it. Several marks from one company are one brand.

Observations are append-only facts. There is deliberately no function to
update or delete one: what we saw, and when, is the record. Re-processing a
journal appends a fresh set with a new ``observed_at``. See
docs/ARCHITECTURE.md for the full model and its point-in-time rules.

The applicant's name is never stored on a brand — only a hash of it — because
an applicant can be a private individual.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from functools import cache
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.db.tables import Brand, Observation, OpportunityRow, ScoreEvent, StageChange
from src.logging_setup import get_logger
from src.models import Opportunity, PipelineResult
from src.parse.normalise import normalise_company_name, normalise_text
from src.settings import load_config

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def applicant_key_hash(applicant_name: str | None) -> str | None:
    """sha256 of the normalised applicant name; None when there is no name."""
    normalised = normalise_company_name(applicant_name)
    return _sha256(normalised) if normalised else None


def _company_number(opp: Opportunity) -> str | None:
    if opp.company.matched and opp.company.company_number:
        return opp.company.company_number.strip().upper() or None
    return None


def _tm_key(opp: Opportunity) -> str:
    basis = f"{normalise_text(opp.brand_name)}|{normalise_company_name(opp.applicant_name)}"
    return f"tm:{_sha256(basis)[:24]}"


def brand_key_for(opp: Opportunity) -> str:
    """The dedupe identity a brand for this opportunity would be created with."""
    number = _company_number(opp)
    return f"ch:{number}" if number else _tm_key(opp)


def brand_uid_for(brand_key: str) -> str:
    """Public stable id, deterministic from the identity the brand was first created with."""
    return f"b_{_sha256(brand_key)[:16]}"


def journal_sort_key(journal_number: str | None) -> tuple[int, int, str]:
    """Chronological order for journal numbers ("YYYY-NNN"), whatever their padding."""
    text = (journal_number or "").strip()
    year, _, ordinal = text.partition("-")
    try:
        return int(year), int(ordinal), text
    except ValueError:
        return 0, 0, text


# ---------------------------------------------------------------------------
# signals registry
# ---------------------------------------------------------------------------


@cache
def signal_registry() -> dict[str, dict[str, Any]]:
    """Every registered signal, flattened across the source groups of config/signals.json."""
    out: dict[str, dict[str, Any]] = {}
    for group, signals in load_config("signals.json").items():
        if group.startswith(("_", "$")) or not isinstance(signals, dict):
            continue
        for name, spec in signals.items():
            if name.startswith("_"):
                continue
            out[name] = spec
    return out


# ---------------------------------------------------------------------------
# brands
# ---------------------------------------------------------------------------


def _find_brand(session: Session, opp: Opportunity) -> Brand | None:
    number = _company_number(opp)
    if number:
        found = (
            session.execute(select(Brand).where(Brand.company_number == number).order_by(Brand.id))
            .scalars()
            .first()
        )
        if found is not None:
            return found
        found = session.execute(
            select(Brand).where(Brand.brand_key == f"ch:{number}")
        ).scalar_one_or_none()
        if found is not None:
            return found
    return session.execute(
        select(Brand).where(Brand.brand_key == _tm_key(opp))
    ).scalar_one_or_none()


def upsert_brand_from_opportunity(session: Session, opp: Opportunity, run_id: str) -> Brand:
    """Find or create the brand for one opportunity and bring it up to date.

    ``first_seen_*`` only ever moves earlier (a backfill of an older journal),
    never later. ``current_*`` and ``last_seen_journal`` follow the newest
    journal seen, so back-filling an old week does not roll the brand back.
    """
    now = datetime.now(UTC)
    journal = opp.journal_number
    brand = _find_brand(session, opp)
    created = brand is None
    if brand is None:
        key = brand_key_for(opp)
        brand = Brand(
            brand_key=key,
            brand_uid=brand_uid_for(key),
            brand_name=opp.brand_name or "",
            first_seen_journal=journal,
            first_seen_at=now,
            first_filing_date=opp.filing_date,
            last_seen_journal=journal,
            current_stage=opp.launch_stage.value,
            current_score=opp.score.value,
            current_band=opp.score.band.value,
            created_at=now,
            updated_at=now,
        )
        session.add(brand)

    number = _company_number(opp)
    if number and not brand.company_number:
        brand.company_number = number
    if number and opp.company.company_name:
        brand.company_name = opp.company.company_name
    if not brand.brand_name and opp.brand_name:
        brand.brand_name = opp.brand_name
    brand.applicant_type = opp.applicant_type.value
    brand.applicant_key_hash = applicant_key_hash(opp.applicant_name) or brand.applicant_key_hash
    if opp.product_category:
        brand.product_category = opp.product_category
    if number and opp.company.region:
        brand.region = opp.company.region
    if opp.web.website:
        brand.website = opp.web.website

    if not created:
        if journal_sort_key(journal) < journal_sort_key(brand.first_seen_journal):
            brand.first_seen_journal = journal
        if opp.filing_date and (
            brand.first_filing_date is None or opp.filing_date < brand.first_filing_date
        ):
            brand.first_filing_date = opp.filing_date
        order = journal_sort_key(journal)
        last = journal_sort_key(brand.last_seen_journal)
        if order >= last:
            previous_stage = brand.current_stage
            brand.last_seen_journal = journal
            brand.current_score = opp.score.value
            brand.current_band = opp.score.band.value
            brand.current_stage = opp.launch_stage.value
            # Only a later week is a transition. Two marks of one company in the
            # same journal can carry different stages; that is not a change.
            if order > last and previous_stage != brand.current_stage:
                session.flush()
                record_stage_change(
                    session,
                    brand.id,
                    previous_stage,
                    brand.current_stage,
                    evidence={
                        "journal_number": journal,
                        "trademark_number": opp.trademark_number,
                        "score": opp.score.value,
                        "detected_by": "weekly",
                    },
                    run_id=run_id,
                )

    checked = opp.web.enriched_at if opp.web.attempted else None
    if checked is not None and (
        brand.last_checked_at is None or _aware(checked) > _aware(brand.last_checked_at)
    ):
        brand.last_checked_at = checked
    brand.updated_at = now
    session.flush()
    return brand


def _aware(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything here is UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def sync_brands(session: Session, result: PipelineResult) -> int:
    """Link every scored opportunity of a run to its brand, and record what we saw.

    Called by the weekly command after ``save_opportunities``. Every scored
    opportunity gets a brand, suppressed ones included: they are the
    population a backtest and a rescan need. Returns the number of distinct
    brands touched.
    """
    # Lowest first, so the opportunity that best represents a company in this
    # run (deliverable, then highest score) is the last to set current_*.
    ordered = sorted(
        result.opportunities,
        key=lambda o: (not o.suppressed, o.score.value, o.trademark_number),
    )
    observed_at = datetime.now(UTC)
    seen_observations: set[tuple[int, str, str]] = set()
    brand_ids: set[int] = set()
    for opp in ordered:
        brand = upsert_brand_from_opportunity(session, opp, result.run_id)
        brand_ids.add(brand.id)
        session.execute(
            update(OpportunityRow)
            .where(
                OpportunityRow.journal_number == opp.journal_number,
                OpportunityRow.dedupe_key == opp.dedupe_key,
            )
            .values(brand_id=brand.id)
        )
        session.execute(
            update(ScoreEvent)
            .where(ScoreEvent.run_id == result.run_id, ScoreEvent.dedupe_key == opp.dedupe_key)
            .values(brand_id=brand.id)
        )
        for signal, value, source_date in _facts(opp, result):
            marker = (brand.id, signal, repr(value))
            if marker in seen_observations:
                continue
            seen_observations.add(marker)
            record_observation(
                session,
                brand.id,
                _source_of(signal),
                signal,
                value,
                observed_at=observed_at,
                source_date=source_date,
                run_id=result.run_id,
                journal_number=opp.journal_number,
            )
    session.flush()
    log.info("brands.synced", run_id=result.run_id, brands=len(brand_ids))
    return len(brand_ids)


def _source_of(signal: str) -> str:
    return str(signal_registry()[signal]["source"])


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _facts(opp: Opportunity, result: PipelineResult) -> list[tuple[str, Any, date | None]]:
    """(signal, value, source_date) for every fact this run has about the opportunity."""
    tm = opp.trademark_number
    published = opp.publication_date or result.journal.publication_date
    facts: list[tuple[str, Any, date | None]] = []
    if opp.filing_date:
        facts.append(
            (
                "filing_date",
                {"trademark_number": tm, "filing_date": _iso(opp.filing_date)},
                opp.filing_date,
            )
        )
    facts.append(
        (
            "publication_date",
            {"trademark_number": tm, "journal_number": opp.journal_number},
            published,
        )
    )
    facts.append(("nice_classes", {"trademark_number": tm, "classes": opp.nice_classes}, published))

    company = opp.company
    if company.matched and company.company_number:
        number = company.company_number.strip().upper()
        if company.incorporation_date:
            facts.append(
                (
                    "incorporation_date",
                    {
                        "company_number": number,
                        "incorporation_date": _iso(company.incorporation_date),
                    },
                    company.incorporation_date,
                )
            )
        facts.append(
            (
                "company_match",
                {
                    "company_number": number,
                    "match_confidence": company.match_confidence,
                    "match_method": company.match_method,
                },
                None,
            )
        )
        facts.append(("company_status", company.company_status, None))
        facts.append(("sic_codes", list(company.sic_codes), None))
        facts.append(("accounts_category", company.accounts_category, None))

    web = opp.web
    if web.attempted:
        facts.extend(
            [
                ("website", web.website, None),
                ("website_maturity", web.website_maturity, None),
                ("retail_presence", web.retail_presence.value, None),
                ("marketplace_presence", web.marketplace_presence, None),
                ("major_retailer_presence", web.major_retailer_presence, None),
                ("social_presence", web.social_presence, None),
                ("products_on_sale", web.products_on_sale, None),
                ("launch_evidence", list(web.launch_evidence), None),
            ]
        )

    facts.extend(domain_facts(opp))

    facts.append(
        (
            "launchtrace_score",
            {
                "trademark_number": tm,
                "score": opp.score.value,
                "band": opp.score.band.value,
                "suppressed": opp.suppressed,
            },
            None,
        )
    )
    facts.append(("launch_stage", opp.launch_stage.value, None))
    return facts


def domain_facts(opp: Opportunity) -> list[tuple[str, Any, date | None]]:
    """Domain-layer observations for one opportunity (only a domain actually probed).

    The RDAP registration date is the only one with a ``source_date``; the
    rest describe the domain as it was when we looked.
    """
    d = opp.domain
    if d is None or not d.checked or not d.domain:
        return []
    name = d.domain
    out: list[tuple[str, Any, date | None]] = []
    if d.rdap_fetched:
        if d.rdap_created:
            out.append(
                (
                    "domain_created",
                    {"domain": name, "created": _iso(d.rdap_created)},
                    d.rdap_created,
                )
            )
        if d.rdap_expires:
            out.append(("domain_expires", {"domain": name, "expires": _iso(d.rdap_expires)}, None))
        if d.rdap_registrar:
            out.append(("domain_registrar", {"domain": name, "registrar": d.rdap_registrar}, None))
    for signal, value in (
        ("dns_has_a", d.has_a),
        ("dns_has_mx", d.has_mx),
        ("dns_has_ns", d.has_ns),
    ):
        if value is not None:
            out.append((signal, {"domain": name, "value": value}, None))
    if d.homepage_status is not None or d.robots_disallowed:
        out.append(
            (
                "homepage_status",
                {
                    "domain": name,
                    "status": d.homepage_status,
                    "final_url": d.final_url,
                    "redirect_target_kind": d.redirect_target_kind,
                    "robots_disallowed": d.robots_disallowed,
                },
                None,
            )
        )
    if d.homepage_fetched:
        out.append(
            (
                "site_platform",
                {"domain": name, "platform": d.platform, "shop_platform": d.shop_platform},
                None,
            )
        )
        out.append(
            (
                "holding_page",
                {"domain": name, "holding": d.is_holding_page, "parked": d.is_parked},
                None,
            )
        )
    if d.web_presence_stage and d.web_presence_stage != "unknown":
        out.append(("web_presence_stage", {"domain": name, "stage": d.web_presence_stage}, None))
    return out


# ---------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------


def record_observation(
    session: Session,
    brand_id: int,
    source: str,
    signal: str,
    value: Any,
    *,
    observed_at: datetime | None = None,
    source_date: date | None = None,
    point_in_time_safe: bool | None = None,
    run_id: str | None = None,
    journal_number: str | None = None,
) -> Observation:
    """Append one fact. There is no update: a changed fact is a new observation.

    ``signal`` must be registered in config/signals.json; its registered
    ``point_in_time_safe`` is the default. A PIT-safe observation with no
    ``source_date`` is stored as not PIT-safe, because a backtest cannot place
    it in time.
    """
    registry = signal_registry()
    if signal not in registry:
        raise ValueError(f"Signal {signal!r} is not registered in config/signals.json")
    pit = (
        bool(registry[signal]["point_in_time_safe"])
        if point_in_time_safe is None
        else bool(point_in_time_safe)
    )
    if source_date is None:
        pit = False
    row = Observation(
        brand_id=brand_id,
        source=source,
        signal=signal,
        value=value,
        observed_at=observed_at or datetime.now(UTC),
        source_date=source_date,
        point_in_time_safe=pit,
        run_id=run_id,
        journal_number=journal_number,
    )
    session.add(row)
    session.flush()
    return row


def latest_observations(
    session: Session, brand_id: int, as_of: datetime | None = None
) -> dict[str, Observation]:
    """The most recent observation of each signal, optionally as known at ``as_of``.

    "As known at" means observed at or before ``as_of`` — what LaunchTrace had
    seen by then, not what was true in the world by then (for that, use
    ``observations_as_of``).
    """
    stmt = select(Observation).where(Observation.brand_id == brand_id)
    if as_of is not None:
        stmt = stmt.where(Observation.observed_at <= _aware(as_of).astimezone(UTC))
    stmt = stmt.order_by(Observation.observed_at, Observation.id)
    out: dict[str, Observation] = {}
    for row in session.execute(stmt).scalars():
        out[row.signal] = row
    return out


def observations_as_of(
    session: Session, brand_id: int, cutoff: date, pit_safe_only: bool = True
) -> list[Observation]:
    """Facts that were true in the world on or before ``cutoff``, for backtests.

    With ``pit_safe_only`` (the default) only point-in-time-safe observations
    whose ``source_date`` is on or before the cutoff are returned. Without it,
    non-PIT observations are also returned when they were *observed* on or
    before the cutoff — the best that can be said for a fact with no date of
    its own.
    """
    rows = session.execute(
        select(Observation)
        .where(Observation.brand_id == brand_id)
        .order_by(Observation.observed_at, Observation.id)
    ).scalars()
    out: list[Observation] = []
    for row in rows:
        if row.point_in_time_safe and row.source_date is not None:
            if row.source_date <= cutoff:
                out.append(row)
        elif not pit_safe_only and _aware(row.observed_at).date() <= cutoff:
            out.append(row)
    return out


def record_stage_change(
    session: Session,
    brand_id: int,
    from_stage: str | None,
    to_stage: str,
    evidence: dict[str, Any] | None,
    run_id: str | None = None,
) -> StageChange:
    """Append a launch-stage transition for a brand."""
    row = StageChange(
        brand_id=brand_id,
        from_stage=from_stage,
        to_stage=to_stage,
        detected_at=datetime.now(UTC),
        evidence=evidence or {},
        run_id=run_id,
    )
    session.add(row)
    session.flush()
    return row
