"""The weekly re-scan: look again at brands we have already seen.

A trade mark is filed before the brand trades. Weeks later the holding page
becomes a shop, or the dormant company files trading accounts. The rescan
re-checks recently seen, not-yet-launched brands and records what moved:

1. **Selection** (:func:`select_brands`): first seen within ``window_weeks``
   (by the publication date of its first journal), ``launched_at`` not set,
   not checked in the last ``min_days_between_checks`` days, and with
   something to check. Oldest check first, capped at ``max_brands_per_run``.
2. **Checks**, each switchable in ``config/rescan.json`` -> ``sources``:

   * ``domain`` -- the domain layer (``src/enrich/domain``) on the brand's
     verified website: RDAP, DNS, one homepage fetch;
   * ``companies_house`` -- the register entry by company number, for brands
     with a confirmed match (bulk index: local, no network; API: one GET);
   * ``web_search`` -- off by default; looks for a website for brands that
     have none, through the search cost guard, never above
     ``web_search_max_calls``.

3. **Observations** under the same signals and sources ``sync_brands`` uses
   (``web_presence_stage``/homepage, ``company_status``/companies_house, ...)
   plus the derived ``company_stage``/rescan, so ``latest_observations``
   compares like with like.
4. **Changes** (``src/rescan/changes.py``) -> ``stage_changes``, and
   ``brands.launched_at`` when a launched stage is reached.
5. ``brands.last_checked_at`` is set, so the second and third Friday attempts
   select nothing and make no external call.

Every brand is processed in its own savepoint: an error is counted and logged,
the brand's partial writes are rolled back, and the run carries on. The rescan
never runs inside a pipeline run, so it cannot move a score.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.brands import (
    domain_signal_facts,
    latest_observations,
    record_observation,
    signal_registry,
)
from src.db.tables import Brand, Journal, PipelineRun
from src.enrich.budget import SearchBudget
from src.enrich.companies_house import CompanyRegistry, NullCompanyRegistry
from src.enrich.domain.common import domain_config
from src.enrich.domain.layer import DomainProber, select_domain
from src.enrich.entity_verification import EntityContext
from src.enrich.matching import CandidateCompany
from src.enrich.web import WebEnricher
from src.logging_setup import get_logger
from src.models import DomainSignals, WebEnrichment
from src.rescan.changes import (
    READERS,
    DetectedChange,
    Dimension,
    StageReading,
    derive_company_stage,
    detect_and_record,
    dimensions,
    highest_rank_before,
    rescan_config,
)
from src.settings import Settings, get_settings

log = get_logger(__name__)

CH_API_PROVIDER = "companies_house_api"


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RescanConfig:
    window_weeks: int = 26
    min_days_between_checks: int = 6
    max_brands_per_run: int = 150
    max_domain_probes_per_run: int = 60
    max_run_seconds: float = 900.0
    domain: bool = True
    companies_house: bool = True
    web_search: bool = False
    web_search_max_calls: int = 20
    seconds_between_brands: float = 1.0
    ch_api_min_interval_seconds: float = 0.6

    @classmethod
    def load(cls, raw: dict[str, Any] | None = None) -> RescanConfig:
        cfg = raw or rescan_config()
        sources = cfg.get("sources", {})
        polite = cfg.get("politeness", {})
        return cls(
            window_weeks=int(cfg.get("window_weeks", 26)),
            min_days_between_checks=int(cfg.get("min_days_between_checks", 6)),
            max_brands_per_run=max(int(cfg.get("max_brands_per_run", 150)), 0),
            max_domain_probes_per_run=max(int(cfg.get("max_domain_probes_per_run", 60)), 0),
            max_run_seconds=float(cfg.get("max_run_seconds", 900)),
            domain=bool(sources.get("domain", True)),
            companies_house=bool(sources.get("companies_house", True)),
            web_search=bool(sources.get("web_search", False)),
            web_search_max_calls=max(int(cfg.get("web_search_max_calls", 20)), 0),
            seconds_between_brands=float(polite.get("seconds_between_brands", 1.0)),
            ch_api_min_interval_seconds=float(
                polite.get("companies_house_api_min_interval_seconds", 0.6)
            ),
        )


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class RescanSummary:
    run_id: str
    dry_run: bool = False
    eligible: int = 0
    selected: int = 0
    checked: int = 0
    domain_probes: int = 0
    domain_cap_skipped: int = 0
    ch_lookups: int = 0
    ch_api_calls: int = 0
    search_calls: int = 0
    search_allowance: int = 0
    observations: int = 0
    stage_changes: int = 0
    meaningful_changes: int = 0
    launched: int = 0
    errors: int = 0
    stopped_for_time: int = 0
    changes: list[DetectedChange] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "eligible": self.eligible,
            "selected": self.selected,
            "checked": self.checked,
            "domain_probes": self.domain_probes,
            "domain_cap_skipped": self.domain_cap_skipped,
            "ch_lookups": self.ch_lookups,
            "ch_api_calls": self.ch_api_calls,
            "search_calls": self.search_calls,
            "search_allowance": self.search_allowance,
            "observations": self.observations,
            "stage_changes": self.stage_changes,
            "meaningful_changes": self.meaningful_changes,
            "launched": self.launched,
            "errors": self.errors,
            "stopped_for_time": self.stopped_for_time,
        }


def new_run_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return f"rescan_{moment.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def journal_publication_dates(session: Session) -> dict[str, date]:
    rows = session.execute(
        select(Journal.journal_number, func.min(Journal.publication_date)).group_by(
            Journal.journal_number
        )
    ).all()
    return {number: published for number, published in rows if published is not None}


def first_seen_date(brand: Brand, published: dict[str, date]) -> date:
    """When the brand first appeared in the world as LaunchTrace knows it."""
    journal_date = published.get(brand.first_seen_journal or "")
    if journal_date is not None:
        return journal_date
    if brand.first_filing_date is not None:
        return brand.first_filing_date
    seen = _aware(brand.first_seen_at) or datetime.now(UTC)
    return seen.date()


def select_brands(
    session: Session,
    config: RescanConfig,
    now: datetime,
    *,
    checkable: Callable[[Brand], bool] | None = None,
    limit: int | None = None,
) -> tuple[list[Brand], int]:
    """(brands to re-check this run, how many were eligible before the cap)."""
    check_cutoff = now - timedelta(days=config.min_days_between_checks)
    window_start = (now - timedelta(weeks=config.window_weeks)).date()
    published = journal_publication_dates(session)
    candidates = session.execute(select(Brand).where(Brand.launched_at.is_(None))).scalars()
    eligible: list[tuple[datetime, date, int, Brand]] = []
    floor = datetime.min.replace(tzinfo=UTC)
    for brand in candidates:
        last = _aware(brand.last_checked_at)
        if last is not None and last > check_cutoff:
            continue
        seen = first_seen_date(brand, published)
        if seen < window_start:
            continue
        if checkable is not None and not checkable(brand):
            continue
        eligible.append((last or floor, seen, brand.id, brand))
    # Oldest check first (never checked first of all); then the newest brands.
    eligible.sort(key=lambda item: (item[0], -item[1].toordinal(), item[2]))
    cap = config.max_brands_per_run if limit is None else min(limit, config.max_brands_per_run)
    return [item[3] for item in eligible[: max(cap, 0)]], len(eligible)


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------


def _web_facts(web: WebEnrichment) -> list[tuple[str, Any]]:
    """The web-search signals sync_brands records, for a rescan search."""
    return [
        ("website", web.website),
        ("website_maturity", web.website_maturity),
        ("retail_presence", web.retail_presence.value),
        ("marketplace_presence", web.marketplace_presence),
        ("major_retailer_presence", web.major_retailer_presence),
        ("social_presence", web.social_presence),
        ("products_on_sale", web.products_on_sale),
        ("launch_evidence", list(web.launch_evidence)),
    ]


class RescanJob:
    """One rescan run. Every collaborator is injectable; tests pass fakes."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        prober: DomainProber | None = None,
        registry: CompanyRegistry | None = None,
        web: WebEnricher | None = None,
        config: RescanConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.config = config or RescanConfig.load()
        self.prober = prober
        self.registry = registry
        self.web = web
        self.sleep = sleep
        self.monotonic = monotonic
        self._now = now or (lambda: datetime.now(UTC))
        self.dimensions: dict[str, Dimension] = dimensions()
        self.domain_cfg = domain_config()
        self._domain_cache: dict[str, DomainSignals] = {}
        self._last_ch_api_call: float | None = None

    # -- what is switched on -------------------------------------------------

    @property
    def domain_on(self) -> bool:
        if not self.config.domain or self.prober is None:
            return False
        try:
            return bool(self.prober.enabled)
        except Exception:
            return False

    @property
    def ch_on(self) -> bool:
        return (
            self.config.companies_house
            and self.registry is not None
            and not isinstance(self.registry, NullCompanyRegistry)
        )

    @property
    def web_on(self) -> bool:
        if not self.config.web_search or self.web is None:
            return False
        try:
            return bool(self.web.available)
        except Exception:
            return False

    def _domain_for(self, brand: Brand) -> str | None:
        if not brand.website:
            return None
        domain, _, _ = select_domain(
            WebEnrichment(attempted=True, website=brand.website), self.domain_cfg
        )
        return domain

    def checks_for(self, brand: Brand) -> list[str]:
        """Which checks this brand would get (empty: nothing to do, not selected)."""
        out: list[str] = []
        if self.web_on and not brand.website and (brand.brand_name or "").strip():
            out.append("web_search")
        if self.domain_on and (brand.website or "web_search" in out):
            out.append("domain")
        if self.ch_on and brand.company_number:
            out.append("companies_house")
        return out

    # -- the run -------------------------------------------------------------

    def run(
        self,
        session: Session,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        commit: bool = False,
    ) -> RescanSummary:
        started = self._now()
        summary = RescanSummary(run_id=new_run_id(started), dry_run=dry_run)
        brands, summary.eligible = select_brands(
            session,
            self.config,
            started,
            checkable=lambda b: bool(self.checks_for(b)),
            limit=limit,
        )
        summary.selected = len(brands)

        if dry_run:
            summary.plan = [
                {
                    "brand_uid": b.brand_uid,
                    "brand_name": b.brand_name,
                    "last_checked_at": b.last_checked_at.isoformat() if b.last_checked_at else None,
                    "checks": self.checks_for(b),
                }
                for b in brands
            ]
            log.info("rescan.dry_run", **summary.as_dict())
            return summary

        budget = self._search_budget(session) if self.web_on else None
        if budget is not None and self.web is not None:
            self.web.budget = budget
            summary.search_allowance = int(budget.max_calls or 0)

        clock_start = self.monotonic()
        for index, brand in enumerate(brands):
            if self.monotonic() - clock_start > self.config.max_run_seconds:
                summary.stopped_for_time = len(brands) - index
                log.warning("rescan.time_budget_reached", remaining=summary.stopped_for_time)
                break
            used_network = self._process_brand(session, brand, summary, budget)
            if commit:
                session.commit()
            if used_network and index < len(brands) - 1 and self.config.seconds_between_brands > 0:
                self.sleep(self.config.seconds_between_brands)

        if budget is not None:
            summary.search_calls = budget.calls
        if summary.search_calls:
            self._record_search_spend(session, summary, started)
        session.flush()
        if commit:
            session.commit()
        log.info("rescan.finished", **summary.as_dict())
        return summary

    def _search_budget(self, session: Session) -> SearchBudget:
        """The smaller of the rescan cap and what the weekly search guard allows now."""
        from src.commands import search_budget_for_run

        guard_budget = search_budget_for_run(session, self.settings)
        guard_allowance = guard_budget.max_calls
        cap = self.config.web_search_max_calls
        allowance = cap if guard_allowance is None else min(cap, guard_allowance)
        return SearchBudget(allowance, limited_by=guard_budget.limited_by)

    def _record_search_spend(
        self, session: Session, summary: RescanSummary, started: datetime
    ) -> None:
        """Search calls count against the rolling monthly budget like the weekly run's."""
        session.add(
            PipelineRun(
                run_id=summary.run_id,
                mode="rescan",
                journal_number=None,
                status="completed",
                started_at=started,
                finished_at=self._now(),
                counts={"search_calls": summary.search_calls, "rescan": summary.as_dict()},
                warnings=[],
            )
        )
        session.flush()

    # -- one brand -----------------------------------------------------------

    def _process_brand(
        self,
        session: Session,
        brand: Brand,
        summary: RescanSummary,
        budget: SearchBudget | None,
    ) -> bool:
        """Re-check one brand. Never raises. Returns whether a network call was made."""
        now = self._now()
        state = {"network": False}
        try:
            with session.begin_nested():
                self._check_brand(session, brand, summary, budget, now, state)
            summary.checked += 1
        except Exception as exc:
            summary.errors += 1
            message = f"{brand.brand_uid}: {type(exc).__name__}: {str(exc)[:200]}"
            summary.error_details.append(message)
            log.warning("rescan.brand_failed", brand_uid=brand.brand_uid, error=message)
        # Checked even when it failed: a broken brand is retried next week, not
        # on every Friday attempt, so the rescan's cost stays bounded.
        try:
            brand.last_checked_at = now
            brand.updated_at = now
            session.flush()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("rescan.mark_checked_failed", brand_uid=brand.brand_uid, error=str(exc))
        return state["network"]

    def _observe(
        self,
        session: Session,
        brand: Brand,
        summary: RescanSummary,
        signal: str,
        value: Any,
        observed_at: datetime,
        *,
        source: str | None = None,
        source_date: date | None = None,
    ) -> None:
        record_observation(
            session,
            brand.id,
            source or str(signal_registry()[signal]["source"]),
            signal,
            value,
            observed_at=observed_at,
            source_date=source_date,
            run_id=summary.run_id,
        )
        summary.observations += 1

    def _check_brand(
        self,
        session: Session,
        brand: Brand,
        summary: RescanSummary,
        budget: SearchBudget | None,
        now: datetime,
        state: dict[str, bool],
    ) -> None:
        # What we knew before this rescan writes anything.
        latest = latest_observations(session, brand.id)
        previous: dict[str, StageReading | None] = {
            key: READERS[key](latest) for key in self.dimensions if key in READERS
        }
        highs = {
            key: highest_rank_before(session, brand.id, dim) for key, dim in self.dimensions.items()
        }
        current: dict[str, StageReading] = {}
        checks = self.checks_for(brand)

        if "web_search" in checks and self.web is not None:
            self._web_search(session, brand, summary, budget, now, state, current)

        if "domain" in checks:
            reading = self._domain_check(session, brand, summary, now, state)
            if reading is not None:
                current["web_presence"] = reading

        if "companies_house" in checks:
            reading = self._companies_house_check(session, brand, summary, now, state)
            if reading is not None:
                current["company_status"] = reading

        for key, dim in self.dimensions.items():
            change = detect_and_record(
                session,
                brand,
                dim,
                previous.get(key),
                current.get(key),
                run_id=summary.run_id,
                highest_before=highs.get(key),
            )
            if change is None:
                continue
            summary.changes.append(change)
            summary.stage_changes += 1
            summary.meaningful_changes += int(change.meaningful)
            summary.launched += int(change.launched)

    def _web_search(
        self,
        session: Session,
        brand: Brand,
        summary: RescanSummary,
        budget: SearchBudget | None,
        now: datetime,
        state: dict[str, bool],
        current: dict[str, StageReading],
    ) -> None:
        assert self.web is not None
        if budget is None or budget.exhausted or (budget.remaining or 0) <= 0:
            return
        context = EntityContext(
            brand_name=brand.brand_name,
            company_name=brand.company_name,
            company_number=brand.company_number,
            region=brand.region,
        )
        before = budget.calls
        web = self.web.enrich(brand.brand_name, brand.company_name, context=context)
        if budget.calls > before:
            state["network"] = True
        if not web.attempted or web.error:
            return
        for signal, value in _web_facts(web):
            self._observe(session, brand, summary, signal, value, now)
        if web.website:
            brand.website = web.website
        else:
            current["web_presence"] = StageReading("no_domain", "web_search", now)

    def _domain_check(
        self,
        session: Session,
        brand: Brand,
        summary: RescanSummary,
        now: datetime,
        state: dict[str, bool],
    ) -> StageReading | None:
        domain = self._domain_for(brand)
        if domain is None or self.prober is None:
            return None
        signals = self._domain_cache.get(domain)
        if signals is None:
            if summary.domain_probes >= self.config.max_domain_probes_per_run:
                summary.domain_cap_skipped += 1
                return None
            summary.domain_probes += 1
            state["network"] = True
            try:
                signals = self.prober.probe(domain)
            except Exception as exc:
                summary.errors += 1
                summary.error_details.append(
                    f"{brand.brand_uid}: probe {domain}: {type(exc).__name__}: {str(exc)[:200]}"
                )
                log.warning("rescan.probe_failed", domain=domain, error=str(exc)[:200])
                return None
            self._domain_cache[domain] = signals
        for signal, value, source_date in domain_signal_facts(signals):
            self._observe(session, brand, summary, signal, value, now, source_date=source_date)
        stage = signals.web_presence_stage
        if not self.dimensions.get("web_presence") or not self.dimensions["web_presence"].is_stage(
            stage
        ):
            return None
        return StageReading(
            stage,
            str(signal_registry()["web_presence_stage"]["source"]),
            now,
            {"domain": domain, "stage": stage, "platform": signals.platform},
        )

    def _throttle_ch_api(self) -> None:
        if getattr(self.registry, "name", "") != CH_API_PROVIDER:
            return
        interval = self.config.ch_api_min_interval_seconds
        if self._last_ch_api_call is not None and interval > 0:
            wait = interval - (self.monotonic() - self._last_ch_api_call)
            if wait > 0:
                self.sleep(wait)
        self._last_ch_api_call = self.monotonic()

    def _companies_house_check(
        self,
        session: Session,
        brand: Brand,
        summary: RescanSummary,
        now: datetime,
        state: dict[str, bool],
    ) -> StageReading | None:
        assert self.registry is not None and brand.company_number
        number = brand.company_number.strip().upper()
        self._throttle_ch_api()
        summary.ch_lookups += 1
        if getattr(self.registry, "name", "") == CH_API_PROVIDER:
            summary.ch_api_calls += 1
            state["network"] = True
        try:
            found: CandidateCompany | None = self.registry.lookup_by_number(number)
        except Exception as exc:
            summary.errors += 1
            summary.error_details.append(
                f"{brand.brand_uid}: companies house {number}: {type(exc).__name__}"
            )
            log.warning("rescan.ch_lookup_failed", company_number=number, error=str(exc)[:200])
            return None
        if found is None:
            return None
        self._observe(session, brand, summary, "company_status", found.company_status, now)
        self._observe(session, brand, summary, "sic_codes", list(found.sic_codes), now)
        self._observe(session, brand, summary, "accounts_category", found.accounts_category, now)
        stage = derive_company_stage(found.company_status, found.accounts_category)
        detail = {
            "company_number": number,
            "stage": stage,
            "company_status": found.company_status,
            "accounts_category": found.accounts_category,
            "provider": getattr(self.registry, "name", "unknown"),
        }
        self._observe(session, brand, summary, "company_stage", detail, now)
        dim = self.dimensions.get("company_status")
        if dim is None or not dim.is_stage(stage):
            return None
        return StageReading(stage, "companies_house", now, detail)


def build_job(settings: Settings | None = None, config: RescanConfig | None = None) -> RescanJob:
    """The production wiring: live domain prober (if enabled), the configured registry."""
    settings = settings or get_settings()
    config = config or RescanConfig.load()
    prober: DomainProber | None = None
    registry: CompanyRegistry | None = None
    web: WebEnricher | None = None
    if config.domain:
        from src.enrich.domain.layer import get_domain_prober

        prober = get_domain_prober(settings)
    if config.companies_house:
        from src.enrich.companies_house import get_company_registry

        try:
            registry = get_company_registry(settings)
        except Exception as exc:
            log.warning("rescan.registry_unavailable", error=str(exc)[:200])
            registry = None
    if config.web_search:
        from src.enrich.web import get_web_enricher

        web = get_web_enricher(settings)
    return RescanJob(settings, prober=prober, registry=registry, web=web, config=config)


__all__ = [
    "RescanConfig",
    "RescanJob",
    "RescanSummary",
    "build_job",
    "first_seen_date",
    "select_brands",
]
