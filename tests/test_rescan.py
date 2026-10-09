"""The weekly rescan (src/rescan/job.py, src/rescan/changes.py).

Network-free: the domain prober is a fixture (or a raising fake), Companies
House is the fixture registry, and search goes through a counting fake provider.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.brands import latest_observations, record_observation
from src.db.tables import Brand, Journal, Observation, PipelineRun, StageChange
from src.enrich.companies_house import (
    CompaniesHouseApiRegistry,
    CompaniesHouseBulkRegistry,
    FixtureCompanyRegistry,
    NullCompanyRegistry,
    build_bulk_index,
)
from src.enrich.domain.layer import FixtureDomainProber, NullDomainProber
from src.enrich.providers.base import SearchProvider, SearchResult
from src.enrich.web import WebEnricher
from src.rescan.changes import (
    StageReading,
    derive_company_stage,
    detect_and_record,
    dimensions,
    rescan_config,
)
from src.rescan.job import RescanConfig, RescanJob, build_job, select_brands

NOW = datetime.now(UTC)
RECENT_JOURNAL = "2026-036"
OLD_JOURNAL = "2025-001"

HOLDING = {"has_dns": True, "homepage_fetched": True, "is_holding_page": True}
LIVE = {
    "has_dns": True,
    "homepage_fetched": True,
    "is_holding_page": False,
    "store_detected": True,
    "platform": "shopify",
    "shop_platform": True,
}


def _probe_spec(stage: str, base: dict[str, Any]) -> dict[str, Any]:
    return {**base, "web_presence_stage": stage}


def _journals(session) -> None:  # type: ignore[no-untyped-def]
    session.add_all(
        [
            Journal(
                journal_number=RECENT_JOURNAL,
                source_name="fixture",
                publication_date=(NOW - timedelta(weeks=3)).date(),
                processing_status="processed",
            ),
            Journal(
                journal_number=OLD_JOURNAL,
                source_name="fixture",
                publication_date=(NOW - timedelta(weeks=60)).date(),
                processing_status="processed",
            ),
        ]
    )
    session.flush()


_counter = {"n": 0}


def make_brand(
    session,  # type: ignore[no-untyped-def]
    name: str = "CRUMBLEDGE",
    *,
    website: str | None = None,
    company_number: str | None = None,
    company_name: str | None = None,
    applicant_type: str = "corporate",
    journal: str = RECENT_JOURNAL,
    last_checked_at: datetime | None = None,
    launched_at: datetime | None = None,
    band: str = "HIGH",
    score: int = 80,
    region: str | None = "BRISTOL",
    category: str | None = "cereal_bars",
) -> Brand:
    _counter["n"] += 1
    key = f"tm:test{_counter['n']:06d}"
    brand = Brand(
        brand_uid=f"b_test{_counter['n']:012d}",
        brand_key=key,
        brand_name=name,
        company_number=company_number,
        company_name=company_name,
        applicant_type=applicant_type,
        website=website,
        product_category=category,
        region=region,
        first_seen_journal=journal,
        first_seen_at=NOW - timedelta(weeks=3),
        first_filing_date=date(2026, 6, 1),
        last_seen_journal=journal,
        current_stage="pre_launch",
        current_score=score,
        current_band=band,
        last_checked_at=last_checked_at,
        launched_at=launched_at,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(brand)
    session.flush()
    return brand


def ch_record(number: str, status: str = "Active", accounts: str | None = "DORMANT") -> dict:
    return {
        "company_name": f"COMPANY {number} LTD",
        "company_number": number,
        "company_status": status,
        "incorporation_date": "01/03/2025",
        "sic_codes": ["10720"],
        "region": "BRISTOL",
        "accounts_category": accounts,
    }


class RaisingProber:
    name = "raising"
    enabled = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def probe(self, domain: str):  # type: ignore[no-untyped-def]
        self.calls.append(domain)
        raise TimeoutError("rdap timed out")


class CountingProvider(SearchProvider):
    name = "counting"
    available = True

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        self.queries.append(query)
        return []


def make_job(settings, *, prober=None, registry=None, web=None, **overrides) -> RescanJob:  # type: ignore[no-untyped-def]
    base = RescanConfig.load()
    values = {**base.__dict__, "seconds_between_brands": 0.0, **overrides}
    return RescanJob(
        settings,
        prober=prober,
        registry=registry,
        web=web,
        config=RescanConfig(**values),
        sleep=lambda _s: None,
    )


def week_passes(session, *brands: Brand) -> None:  # type: ignore[no-untyped-def]
    """Pretend the last check was a week ago, so the brand is due again."""
    for brand in brands:
        brand.last_checked_at = (brand.last_checked_at or NOW) - timedelta(days=7)
    session.flush()


def rescan_changes(session, brand: Brand) -> list[StageChange]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(StageChange).where(StageChange.brand_id == brand.id).order_by(StageChange.id)
        ).scalars()
    )


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults_match_the_brief(self):
        cfg = RescanConfig.load()
        assert cfg.window_weeks == 26
        assert cfg.min_days_between_checks == 6
        assert cfg.domain and cfg.companies_house
        assert cfg.web_search is False
        assert cfg.web_search_max_calls > 0

    def test_ladders_and_rules_are_consistent(self):
        for dim in dimensions().values():
            assert len(set(dim.ladder)) == len(dim.ladder)
            assert dim.launched <= set(dim.ladder)
            for rule in dim.meaningful:
                assert rule.direction in {"up", "down"}
                assert rule.to <= set(dim.ladder)
            for stage in dim.ladder:
                assert len(dim.stage_name(stage)) <= 32  # stage_changes column width

    def test_web_ladder_is_the_domain_layer_vocabulary(self):
        from src.enrich.domain.common import domain_config

        values = set(domain_config()["web_presence_stage"]["values"])
        assert set(dimensions()["web_presence"].ladder) <= values

    def test_company_stage_signal_is_registered(self):
        from src.brands import signal_registry

        assert signal_registry()["company_stage"]["source"] == "rescan"
        assert rescan_config()["transitions"]["company_status"]["signal"] == "company_stage"


class TestDeriveCompanyStage:
    @pytest.mark.parametrize(
        ("status", "accounts", "stage"),
        [
            ("Active", "DORMANT", "dormant"),
            ("active", "dormant", "dormant"),
            ("Active", "NO ACCOUNTS FILED", "no_accounts"),
            ("active", None, "no_accounts"),
            ("active", "null", "no_accounts"),
            ("Active", "MICRO ENTITY", "trading"),
            ("active", "micro-entity", "trading"),
            ("Active", "TOTAL EXEMPTION FULL", "trading"),
            ("Dissolved", "MICRO ENTITY", "dissolved"),
            ("liquidation", None, "in_insolvency"),
            ("In Administration", None, "in_insolvency"),
            ("something-new", "MICRO ENTITY", "unknown"),
            (None, None, "unknown"),
        ],
    )
    def test_rules(self, status, accounts, stage):
        assert derive_company_stage(status, accounts) == stage


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


class TestSelection:
    def test_window_launched_and_min_days(self, db_session, settings):
        _journals(db_session)
        due = make_brand(db_session, "DUE", website="https://due.co.uk")
        old = make_brand(db_session, "OLD", website="https://old.co.uk", journal=OLD_JOURNAL)
        launched = make_brand(db_session, "LAUNCHED", website="https://l.co.uk", launched_at=NOW)
        recent = make_brand(
            db_session, "RECENT", website="https://r.co.uk", last_checked_at=NOW - timedelta(days=2)
        )
        stale = make_brand(
            db_session, "STALE", website="https://s.co.uk", last_checked_at=NOW - timedelta(days=9)
        )
        nothing = make_brand(db_session, "NOTHING")  # no website, no company, no search
        job = make_job(settings, prober=FixtureDomainProber(default=HOLDING))
        chosen, eligible = select_brands(
            db_session, job.config, NOW, checkable=lambda b: bool(job.checks_for(b))
        )
        names = [b.brand_name for b in chosen]
        assert names == ["DUE", "STALE"]  # never checked first, then oldest check
        assert eligible == 2
        for skipped in (old, launched, recent, nothing):
            assert skipped.brand_name not in names
        assert due is chosen[0] and stale is chosen[1]

    def test_window_falls_back_to_filing_date_without_a_journal_row(self, db_session, settings):
        brand = make_brand(db_session, "NOJOURNAL", website="https://nj.co.uk", journal="2099-999")
        brand.first_filing_date = (NOW - timedelta(weeks=40)).date()
        db_session.flush()
        job = make_job(settings, prober=FixtureDomainProber(default=HOLDING))
        chosen, _ = select_brands(db_session, job.config, NOW, checkable=job.checks_for)
        assert chosen == []

    def test_cap_and_limit(self, db_session, settings):
        _journals(db_session)
        for i in range(5):
            make_brand(db_session, f"B{i}", website=f"https://b{i}.co.uk")
        prober = FixtureDomainProber(default=HOLDING)
        job = make_job(settings, prober=prober, max_brands_per_run=3)
        summary = job.run(db_session, limit=2)
        assert summary.eligible == 5 and summary.selected == 2
        assert len(prober.calls) == 2

    def test_disabled_sources_mean_nothing_to_check(self, db_session, settings):
        _journals(db_session)
        make_brand(db_session, website="https://x.co.uk", company_number="14000001")
        job = make_job(settings, prober=NullDomainProber(), registry=NullCompanyRegistry())
        assert job.run(db_session).selected == 0

    def test_dry_run_makes_no_call_and_no_write(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, website="https://x.co.uk", company_number="14000001")
        prober = FixtureDomainProber(default=HOLDING)
        job = make_job(
            settings,
            prober=prober,
            registry=FixtureCompanyRegistry(records=[ch_record("14000001")]),
        )
        summary = job.run(db_session, dry_run=True)
        assert summary.selected == 1
        assert summary.plan[0]["checks"] == ["domain", "companies_house"]
        assert prober.calls == []
        assert brand.last_checked_at is None
        assert db_session.execute(select(Observation)).first() is None


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


class TestRun:
    def test_second_run_is_idempotent_and_makes_no_probe(self, db_session, settings):
        _journals(db_session)
        make_brand(db_session, "A", website="https://a.co.uk", company_number="14000001")
        make_brand(db_session, "B", website="https://b.co.uk")
        prober = FixtureDomainProber(default=HOLDING)
        registry = FixtureCompanyRegistry(records=[ch_record("14000001")])
        job = make_job(settings, prober=prober, registry=registry)
        first = job.run(db_session)
        assert first.checked == 2 and len(prober.calls) == 2 and first.ch_lookups == 1
        second = make_job(settings, prober=prober, registry=registry).run(db_session)
        assert second.selected == 0
        assert len(prober.calls) == 2
        assert second.ch_lookups == 0 and second.observations == 0

    def test_observations_use_the_native_sources(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, website="https://www.a.co.uk", company_number="14000001")
        job = make_job(
            settings,
            prober=FixtureDomainProber(default={**HOLDING, "web_presence_stage": "holding_page"}),
            registry=FixtureCompanyRegistry(records=[ch_record("14000001")]),
        )
        job.run(db_session)
        latest = latest_observations(db_session, brand.id)
        assert latest["web_presence_stage"].source == "homepage"
        assert latest["web_presence_stage"].value == {"domain": "a.co.uk", "stage": "holding_page"}
        assert latest["company_status"].source == "companies_house"
        assert latest["company_status"].value == "Active"
        assert latest["company_stage"].source == "rescan"
        assert latest["company_stage"].value["stage"] == "dormant"
        assert latest["company_stage"].run_id.startswith("rescan_")
        assert brand.last_checked_at is not None

    def test_web_presence_ladder_records_changes_and_launch(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, website="https://crumbledge.co.uk")
        # An earlier weekly search found no website.
        record_observation(
            db_session,
            brand.id,
            "web_search",
            "website",
            None,
            observed_at=NOW - timedelta(days=20),
        )
        prober = FixtureDomainProber(
            fixtures={"crumbledge.co.uk": _probe_spec("holding_page", HOLDING)}
        )
        fixtures = prober.fixtures  # the prober keeps its own (lower-cased) copy

        first = make_job(settings, prober=prober).run(db_session)
        assert first.stage_changes == 1 and first.meaningful_changes == 1
        changes = rescan_changes(db_session, brand)
        assert [(c.from_stage, c.to_stage) for c in changes] == [
            ("web:no_domain", "web:holding_page")
        ]
        evidence = changes[0].evidence
        assert evidence["detected_by"] == "rescan" and evidence["meaningful"] is True
        assert evidence["old_value"] == "no_domain" and evidence["new_value"] == "holding_page"
        assert evidence["old_source"] == "web_search" and evidence["new_source"] == "homepage"
        assert evidence["old_observed_at"] and evidence["new_observed_at"]
        assert brand.launched_at is None
        assert brand.current_stage == "pre_launch"

        week_passes(db_session, brand)
        fixtures["crumbledge.co.uk"] = _probe_spec("live_store", LIVE)
        second = make_job(settings, prober=prober).run(db_session)
        assert second.stage_changes == 1 and second.launched == 1
        changes = rescan_changes(db_session, brand)
        assert (changes[-1].from_stage, changes[-1].to_stage) == (
            "web:holding_page",
            "web:live_store",
        )
        assert changes[-1].evidence["launched"] is True
        assert brand.launched_at is not None
        assert brand.current_stage == "pre_launch"  # the pipeline's stage is left alone (D-602)

        # Launched brands are no longer re-checked.
        week_passes(db_session, brand)
        assert make_job(settings, prober=prober).run(db_session).selected == 0

    def test_first_reading_is_a_baseline_not_a_change(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, website="https://fresh.co.uk")
        summary = make_job(
            settings, prober=FixtureDomainProber(default=_probe_spec("holding_page", HOLDING))
        ).run(db_session)
        assert summary.stage_changes == 0
        assert rescan_changes(db_session, brand) == []

    def test_no_change_means_no_stage_change(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, website="https://same.co.uk", company_number="14000009")
        prober = FixtureDomainProber(default=_probe_spec("site_no_store", HOLDING))
        registry = FixtureCompanyRegistry(records=[ch_record("14000009", accounts="MICRO ENTITY")])
        for _ in range(3):
            make_job(settings, prober=prober, registry=registry).run(db_session)
            week_passes(db_session, brand)
        assert rescan_changes(db_session, brand) == []
        assert len(prober.calls) == 3

    def test_dns_blip_back_up_is_recorded_but_not_alert_worthy(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, website="https://blip.co.uk")
        prober = FixtureDomainProber(fixtures={"blip.co.uk": _probe_spec("site_no_store", HOLDING)})
        fixtures = prober.fixtures
        for stage in ("site_no_store", "no_dns", "site_no_store"):
            fixtures["blip.co.uk"] = _probe_spec(stage, HOLDING)
            make_job(settings, prober=prober).run(db_session)
            week_passes(db_session, brand)
        changes = rescan_changes(db_session, brand)
        assert [(c.to_stage, c.evidence["meaningful"]) for c in changes] == [
            ("web:no_dns", False),
            ("web:site_no_store", False),  # back to where it was: not a new high
        ]

    def test_unknown_stage_is_never_a_transition(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, website="https://u.co.uk")
        prober = FixtureDomainProber(fixtures={"u.co.uk": _probe_spec("holding_page", HOLDING)})
        fixtures = prober.fixtures
        make_job(settings, prober=prober).run(db_session)
        week_passes(db_session, brand)
        fixtures["u.co.uk"] = {"errors": ["rdap_timeout"], "web_presence_stage": "unknown"}
        make_job(settings, prober=prober).run(db_session)
        assert rescan_changes(db_session, brand) == []

    def test_companies_house_dormant_to_trading(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(
            db_session, company_number="14000002", company_name="COMPANY 14000002 LTD"
        )
        records = [ch_record("14000002", accounts="DORMANT")]
        registry = FixtureCompanyRegistry(records=records)
        first = make_job(settings, registry=registry).run(db_session)
        assert first.ch_lookups == 1 and first.stage_changes == 0  # baseline
        week_passes(db_session, brand)
        records[0] = ch_record("14000002", accounts="MICRO ENTITY")
        second = make_job(settings, registry=registry).run(db_session)
        assert second.stage_changes == 1 and second.meaningful_changes == 1
        change = rescan_changes(db_session, brand)[-1]
        assert (change.from_stage, change.to_stage) == ("company:dormant", "company:trading")
        assert change.evidence["new_detail"]["accounts_category"] == "MICRO ENTITY"
        assert change.evidence["launched"] is False
        assert brand.launched_at is None

    def test_dissolution_is_a_negative_meaningful_transition(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, company_number="14000003", company_name="X LTD")
        records = [ch_record("14000003", accounts="MICRO ENTITY")]
        registry = FixtureCompanyRegistry(records=records)
        make_job(settings, registry=registry).run(db_session)
        week_passes(db_session, brand)
        records[0] = ch_record("14000003", status="Dissolved", accounts="MICRO ENTITY")
        make_job(settings, registry=registry).run(db_session)
        change = rescan_changes(db_session, brand)[-1]
        assert change.to_stage == "company:dissolved"
        assert change.evidence["meaningful"] is True and change.evidence["negative"] is True

    def test_prober_exception_is_counted_and_the_run_continues(self, db_session, settings):
        _journals(db_session)
        a = make_brand(db_session, "A", website="https://a.co.uk", company_number="14000004")
        b = make_brand(db_session, "B", website="https://b.co.uk")
        prober = RaisingProber()
        registry = FixtureCompanyRegistry(records=[ch_record("14000004")])
        summary = make_job(settings, prober=prober, registry=registry).run(db_session)
        assert summary.errors == 2
        assert summary.checked == 2
        assert summary.ch_lookups == 1  # the CH check still ran for A
        assert len(prober.calls) == 2
        assert a.last_checked_at is not None and b.last_checked_at is not None

    def test_registry_exception_is_counted(self, db_session, settings):
        _journals(db_session)
        make_brand(db_session, company_number="14000005")

        class Broken(FixtureCompanyRegistry):
            def lookup_by_number(self, company_number):  # type: ignore[no-untyped-def]
                raise httpx.ConnectError("down")

        summary = make_job(settings, registry=Broken(records=[])).run(db_session)
        assert summary.errors == 1 and summary.checked == 1

    def test_unexpected_error_rolls_back_only_that_brand(self, db_session, settings, monkeypatch):
        _journals(db_session)
        bad = make_brand(db_session, "BAD", website="https://bad.co.uk")
        good = make_brand(db_session, "GOOD", website="https://good.co.uk")
        job = make_job(settings, prober=FixtureDomainProber(default=HOLDING))
        real = job._domain_check

        def flaky(session, brand, *args, **kwargs):  # type: ignore[no-untyped-def]
            if brand.brand_name == "BAD":
                raise RuntimeError("boom")
            return real(session, brand, *args, **kwargs)

        monkeypatch.setattr(job, "_domain_check", flaky)
        summary = job.run(db_session)
        assert summary.errors == 1 and summary.checked == 1
        assert latest_observations(db_session, bad.id) == {}
        assert latest_observations(db_session, good.id)
        assert bad.last_checked_at is not None

    def test_domain_probe_cap(self, db_session, settings):
        _journals(db_session)
        for i in range(4):
            make_brand(db_session, f"C{i}", website=f"https://c{i}.co.uk")
        prober = FixtureDomainProber(default=HOLDING)
        summary = make_job(settings, prober=prober, max_domain_probes_per_run=2).run(db_session)
        assert len(prober.calls) == 2 and summary.domain_cap_skipped == 2

    def test_politeness_sleeps_between_brands_that_used_the_network(self, db_session, settings):
        _journals(db_session)
        for i in range(3):
            make_brand(db_session, f"P{i}", website=f"https://p{i}.co.uk")
        slept: list[float] = []
        base = RescanConfig.load()
        job = RescanJob(
            settings,
            prober=FixtureDomainProber(default=HOLDING),
            config=RescanConfig(**{**base.__dict__, "seconds_between_brands": 1.5}),
            sleep=slept.append,
        )
        job.run(db_session)
        assert slept == [1.5, 1.5]

    def test_time_budget_stops_starting_brands(self, db_session, settings):
        _journals(db_session)
        for i in range(3):
            make_brand(db_session, f"T{i}", website=f"https://t{i}.co.uk")
        ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0])
        base = RescanConfig.load()
        job = RescanJob(
            settings,
            prober=FixtureDomainProber(default=HOLDING),
            config=RescanConfig(
                **{**base.__dict__, "seconds_between_brands": 0.0, "max_run_seconds": 5.0}
            ),
            sleep=lambda _s: None,
            monotonic=lambda: next(ticks, 10.0),
        )
        summary = job.run(db_session)
        assert summary.checked == 1 and summary.stopped_for_time == 2

    def test_stage_change_is_never_duplicated(self, db_session):
        brand = make_brand(db_session, website="https://d.co.uk")
        dim = dimensions()["web_presence"]
        old = StageReading("holding_page", "homepage", NOW)
        new = StageReading("live_store", "homepage", NOW)
        assert detect_and_record(db_session, brand, dim, old, new) is not None
        assert detect_and_record(db_session, brand, dim, old, new) is None
        assert len(rescan_changes(db_session, brand)) == 1


# ---------------------------------------------------------------------------
# web search
# ---------------------------------------------------------------------------


class TestWebSearch:
    def test_disabled_by_default_makes_no_search_call(self, db_session, settings):
        _journals(db_session)
        for i in range(3):
            make_brand(
                db_session, f"W{i}", company_number=f"1400010{i}", company_name=f"W{i} FOODS LTD"
            )
        provider = CountingProvider()
        web = WebEnricher(provider=provider, settings=settings)
        job = RescanJob(
            settings,
            registry=FixtureCompanyRegistry(records=[]),
            web=web,
            config=RescanConfig(**{**RescanConfig.load().__dict__, "seconds_between_brands": 0.0}),
        )
        summary = job.run(db_session)
        assert provider.queries == [] and summary.search_calls == 0
        assert db_session.execute(select(PipelineRun)).first() is None

    def test_enabled_respects_the_cap_and_is_counted_in_the_budget(self, db_session, settings):
        _journals(db_session)
        for i in range(5):
            make_brand(
                db_session, f"S{i}", company_number=f"1400020{i}", company_name=f"OTHER {i} LTD"
            )
        provider = CountingProvider()
        web = WebEnricher(provider=provider, settings=settings)
        job = make_job(
            settings,
            registry=FixtureCompanyRegistry(records=[]),
            web=web,
            web_search=True,
            web_search_max_calls=3,
        )
        summary = job.run(db_session)
        assert 0 < len(provider.queries) <= 3
        assert summary.search_calls == len(provider.queries)
        assert summary.search_allowance == 3
        run = db_session.execute(select(PipelineRun)).scalar_one()
        assert run.mode == "rescan" and run.counts["search_calls"] == summary.search_calls

    def test_enabled_never_exceeds_the_weekly_guard(self, db_session, settings):
        _journals(db_session)
        for i in range(3):
            make_brand(db_session, f"G{i}")
        provider = CountingProvider()
        tight = settings.model_copy(update={"search_max_calls_per_run": 1})
        web = WebEnricher(provider=provider, settings=tight)
        job = make_job(tight, web=web, web_search=True, web_search_max_calls=10)
        summary = job.run(db_session)
        assert summary.search_allowance == 1
        assert len(provider.queries) <= 1

    def test_a_search_finding_nothing_records_no_domain(self, db_session, settings):
        _journals(db_session)
        brand = make_brand(db_session, "NOSITE")
        web = WebEnricher(provider=CountingProvider(), settings=settings)
        make_job(settings, web=web, web_search=True).run(db_session)
        latest = latest_observations(db_session, brand.id)
        assert latest["website"].value is None and latest["website"].source == "web_search"


# ---------------------------------------------------------------------------
# Companies House lookups by number
# ---------------------------------------------------------------------------


class TestLookupByNumber:
    def test_fixture(self):
        registry = FixtureCompanyRegistry()
        found = registry.lookup_by_number("14000001")
        assert found is not None and found.company_name == "CRUMBLEDGE FOODS LTD"
        assert registry.lookup_by_number("99999999") is None
        assert NullCompanyRegistry().lookup_by_number("14000001") is None

    def test_bulk_index(self, tmp_path):
        source = tmp_path / "basic.csv"
        with source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "CompanyName",
                    "CompanyNumber",
                    "CompanyStatus",
                    "IncorporationDate",
                    "Accounts.AccountCategory",
                    "SICCode.SicText_1",
                ]
            )
            writer.writerow(
                ["OAT CO LTD", "12345678", "Active", "01/02/2024", "DORMANT", "10720 - x"]
            )
        index = tmp_path / "ch.sqlite"
        build_bulk_index(source, index, progress_every=0)
        registry = CompaniesHouseBulkRegistry(index)
        found = registry.lookup_by_number("12345678")
        assert found is not None
        assert found.accounts_category == "DORMANT" and found.sic_codes == ("10720",)
        assert registry.lookup_by_number("00000000") is None

    def test_api_uses_get_company(self, settings, monkeypatch):
        seen: list[str] = []

        def server(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path.endswith("/company/12345678"):
                return httpx.Response(
                    200,
                    json={
                        "company_name": "OAT CO LTD",
                        "company_number": "12345678",
                        "company_status": "active",
                        "type": "ltd",
                        "date_of_creation": "2024-02-01",
                        "sic_codes": ["10720"],
                        "registered_office_address": {"locality": "Bristol", "region": "Avon"},
                        "accounts": {"last_accounts": {"type": "micro-entity"}},
                    },
                )
            return httpx.Response(404, json={})

        real_client = httpx.Client
        monkeypatch.setattr(
            httpx,
            "Client",
            lambda *a, **k: real_client(transport=httpx.MockTransport(server), timeout=5),
        )
        keyed = settings.model_copy(update={"companies_house_api_key": "test-key"})
        registry = CompaniesHouseApiRegistry(keyed)
        found = registry.lookup_by_number("12345678")
        assert found is not None
        assert found.company_status == "active" and found.accounts_category == "micro-entity"
        assert derive_company_stage(found.company_status, found.accounts_category) == "trading"
        assert registry.lookup_by_number("87654321") is None
        assert seen == ["/company/12345678", "/company/87654321"]


class TestWiring:
    def test_build_job_uses_null_prober_when_the_domain_layer_is_off(self, settings):
        job = build_job(settings.model_copy(update={"domain_layer_enabled": False}))
        assert isinstance(job.prober, NullDomainProber)
        assert job.web is None  # web search is off by default
        assert not job.domain_on
