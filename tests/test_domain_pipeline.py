"""The domain layer inside the pipeline: signals, CSV, scores, observations, failure."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from src.brands import domain_facts, sync_brands
from src.classify.pipeline import ProductClassifier
from src.db.tables import Observation
from src.deliver.csv_export import CSV_COLUMNS
from src.deliver.qa_report import build_qa_report
from src.enrich.companies_house import FixtureCompanyRegistry
from src.enrich.domain import DomainSignals, FixtureDomainProber, NullDomainProber
from src.enrich.providers import FixtureSearchProvider
from src.enrich.web import WebEnricher
from src.ingest.fixture import FixtureJournalSource
from src.models import Opportunity, RunStatus
from src.pipeline_core import Pipeline
from src.score.launchtrace_score import LaunchTraceScorer, ScoringContext
from src.settings import FIXTURES_DIR, Settings, load_config

from .conftest import make_assessment, make_company, make_record, make_web

PROBES = FIXTURES_DIR / "domain" / "probes.json"

RICH: dict[str, Any] = {
    "rdap_fetched": True,
    "rdap_created": "2025-06-02",
    "rdap_expires": "2027-06-02",
    "rdap_registrar": "Example Registrar Ltd",
    "has_dns": True,
    "has_a": True,
    "has_mx": True,
    "has_ns": True,
    "homepage_fetched": True,
    "homepage_status": 200,
    "platform": "shopify",
    "shop_platform": True,
    "store_detected": True,
    "is_parked": False,
    "is_holding_page": True,
    "web_presence_stage": "live_store",
}


def _pipeline(settings: Settings, out: Path, domain: Any = None) -> Pipeline:
    return Pipeline(
        settings=settings,
        source=FixtureJournalSource(settings),
        registry=FixtureCompanyRegistry(),
        classifier=ProductClassifier(settings, llm_provider=None),
        web=WebEnricher(provider=FixtureSearchProvider(), settings=settings),
        output_dir=out,
        domain=domain,
    )


def _by_tm(opps: list[Opportunity]) -> dict[str, Opportunity]:
    return {o.trademark_number: o for o in opps}


class ExplodingProber:
    name = "exploding"
    enabled = True

    def probe(self, domain: str) -> DomainSignals:
        raise RuntimeError("network on fire")


class TestPipelineIntegration:
    def test_default_in_tests_is_the_null_prober(self, pipeline: Pipeline):
        assert isinstance(pipeline.domain, NullDomainProber)
        result = pipeline.run(write_outputs=False)
        assert all(o.domain is None for o in result.opportunities)

    def test_signals_land_on_opportunities_and_csv(self, settings: Settings, tmp_path: Path):
        prober = FixtureDomainProber.from_json(PROBES)
        result = _pipeline(settings, tmp_path / "runs", prober).run(write_outputs=True)
        assert result.status == RunStatus.COMPLETED
        with_site = [o for o in result.opportunities if o.web.website]
        assert with_site, "the fixture journal has verified websites"
        for opp in with_site:
            assert opp.domain is not None and opp.domain.checked
            assert opp.domain.prober == "fixture"
            assert opp.domain.domain_source == "verified_website"
        crumbledge = next(o for o in result.opportunities if o.brand_name == "CRUMBLEDGE")
        assert crumbledge.domain is not None
        assert crumbledge.domain.rdap_created == date(2025, 6, 2)
        assert "domain_registered_recently" in crumbledge.domain.indicators
        assert "has_mx_records" in crumbledge.domain.indicators

        assert result.csv_path
        with open(result.csv_path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        assert list(rows[0].keys()) == CSV_COLUMNS
        assert CSV_COLUMNS[-4:] == [
            "domain_created",
            "web_presence_stage",
            "shop_platform",
            "has_mx",
        ]
        row = next(r for r in rows if r["brand_name"] == "CRUMBLEDGE")
        assert row["domain_created"] == "2025-06-02"
        assert row["web_presence_stage"] == "Holding / coming-soon page"
        assert row["shop_platform"] == "shopify"
        assert row["has_mx"] == "yes"

        qa = json.loads(Path(result.qa_report_path or "").read_text(encoding="utf-8"))
        assert qa["domain_layer"]["probed"] == len({o.domain.domain for o in with_site if o.domain})
        assert qa["domain_layer"]["rdap_dates"] >= 1

    def test_csv_columns_blank_without_the_layer(self, pipeline: Pipeline):
        result = pipeline.run(write_outputs=True)
        with open(result.csv_path or "", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        assert rows and all(r["domain_created"] == "" and r["has_mx"] == "" for r in rows)

    def test_scores_and_reasons_identical_with_and_without_the_layer(
        self, settings: Settings, tmp_path: Path
    ):
        plain = _pipeline(settings, tmp_path / "a", NullDomainProber()).run(write_outputs=False)
        rich = _pipeline(settings, tmp_path / "b", FixtureDomainProber(default=RICH)).run(
            write_outputs=False
        )
        a, b = _by_tm(plain.opportunities), _by_tm(rich.opportunities)
        assert set(a) == set(b)
        fired = 0
        for tm, opp in a.items():
            other = b[tm]
            assert other.score.value == opp.score.value
            assert other.score.band == opp.score.band
            assert [r.key for r in other.score.reasons] == [r.key for r in opp.score.reasons]
            assert [r.key for r in other.score.negative_reasons] == [
                r.key for r in opp.score.negative_reasons
            ]
            assert other.suppressed == opp.suppressed
            if other.domain and other.domain.indicators:
                fired += 1
        assert fired, "the rich fixture made domain indicators fire"
        assert (plain.counts.high, plain.counts.medium) == (rich.counts.high, rich.counts.medium)

    def test_prober_exception_never_blocks_the_run(self, settings: Settings, tmp_path: Path):
        result = _pipeline(settings, tmp_path / "runs", ExplodingProber()).run(write_outputs=True)
        assert result.status == RunStatus.COMPLETED
        probed = [o for o in result.opportunities if o.domain and o.domain.checked]
        assert probed and all(o.domain and o.domain.errors for o in probed)
        assert result.counts.high == 4

    def test_stage_failure_is_a_warning(
        self, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import src.pipeline_core as core

        def broken(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("config gone")

        monkeypatch.setattr(core, "DomainLayer", broken)
        result = _pipeline(settings, tmp_path / "runs", FixtureDomainProber(default=RICH)).run(
            write_outputs=False
        )
        assert result.status == RunStatus.COMPLETED
        assert any("Domain layer did not run" in w for w in result.warnings)
        assert all(o.domain is None for o in result.opportunities)

    def test_cap_reached_is_a_warning(
        self, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import src.enrich.domain.layer as layer

        cfg = json.loads(json.dumps(load_config("domain_layer.json")))
        cfg["domain_selection"]["max_domains_per_run"] = 1
        monkeypatch.setattr(layer, "domain_config", lambda: cfg)
        prober = FixtureDomainProber(default=RICH)
        result = _pipeline(settings, tmp_path / "runs", prober).run(write_outputs=False)
        assert len(set(prober.calls)) == 1
        assert any("Domain layer cap reached" in w for w in result.warnings)


class TestObservations:
    def test_domain_observations_and_pit_flags(
        self, settings: Settings, tmp_path: Path, db_session
    ):
        result = _pipeline(settings, tmp_path / "runs", FixtureDomainProber(default=RICH)).run(
            write_outputs=False
        )
        sync_brands(db_session, result)
        rows = (
            db_session.execute(
                select(Observation).where(Observation.source.in_(["rdap", "dns", "homepage"]))
            )
            .scalars()
            .all()
        )
        by_signal: dict[str, list[Observation]] = {}
        for row in rows:
            by_signal.setdefault(row.signal, []).append(row)
        assert set(by_signal) == {
            "domain_created",
            "domain_expires",
            "domain_registrar",
            "dns_has_a",
            "dns_has_mx",
            "dns_has_ns",
            "homepage_status",
            "site_platform",
            "holding_page",
            "web_presence_stage",
        }
        for row in by_signal["domain_created"]:
            assert row.source == "rdap"
            assert row.point_in_time_safe is True
            assert row.source_date == date(2025, 6, 2)
            assert row.value["created"] == "2025-06-02"
        for signal, obs in by_signal.items():
            if signal == "domain_created":
                continue
            for row in obs:
                assert row.point_in_time_safe is False, signal
                assert row.source_date is None, signal
        assert {r.source for r in by_signal["dns_has_mx"]} == {"dns"}
        assert {r.source for r in by_signal["site_platform"]} == {"homepage"}

    def test_nothing_recorded_for_an_unprobed_domain(self):
        opp = Opportunity(dedupe_key="x", trademark_number="UK1")
        assert domain_facts(opp) == []
        opp.domain = DomainSignals(domain=None, web_presence_stage="no_domain")
        assert domain_facts(opp) == []
        opp.domain = DomainSignals(domain="b.co.uk", checked=False, errors=["domain_cap_reached"])
        assert domain_facts(opp) == []

    def test_failed_checks_are_not_recorded_as_facts(self):
        opp = Opportunity(dedupe_key="x", trademark_number="UK1")
        opp.domain = DomainSignals(
            domain="b.co.uk", checked=True, errors=["rdap_timeout", "dns_timeout:A"]
        )
        assert domain_facts(opp) == []


class TestScorer:
    def _ctx(self, domain: DomainSignals | None) -> ScoringContext:
        return ScoringContext(
            record=make_record(),
            product=make_assessment(),
            company=make_company(),
            web=make_web(attempted=True, website="https://crumbledge.co.uk"),
            company_age_years=0.5,
            domain=domain,
        )

    def test_indicators_detected(self, scorer: LaunchTraceScorer):
        signals = DomainSignals.model_validate({**RICH, "domain": "b.co.uk", "checked": True})
        keys, facts = scorer.domain_indicator_keys(self._ctx(signals))
        assert keys == [
            "domain_registered_recently",
            "shop_platform_detected",
            "holding_page_detected",
            "has_mx_records",
        ]
        assert facts["domain_days_before_filing"] == (date(2025, 9, 15) - date(2025, 6, 2)).days

    def test_old_domain_is_not_recent(self, scorer: LaunchTraceScorer):
        signals = DomainSignals(domain="b.co.uk", checked=True, rdap_created=date(2015, 1, 1))
        assert scorer.domain_indicator_keys(self._ctx(signals))[0] == []

    def test_weight_zero_changes_nothing_and_gives_no_reason(self, scorer: LaunchTraceScorer):
        signals = DomainSignals.model_validate({**RICH, "domain": "b.co.uk", "checked": True})
        without = scorer.score(self._ctx(None))
        with_domain = scorer.score(self._ctx(signals))
        assert with_domain.model_dump() == without.model_dump()
        domain_keys = {i["key"] for i in load_config("scoring.json")["domain_indicators"]}
        assert not domain_keys & {r.key for r in with_domain.reasons + with_domain.negative_reasons}

    def test_every_domain_indicator_ships_at_weight_zero(self):
        cfg = load_config("scoring.json")
        assert cfg["domain_indicators"]
        assert all(i["weight"] == 0 for i in cfg["domain_indicators"])
        keys = {i["key"] for i in cfg["domain_indicators"]}
        others = {i["key"] for i in cfg["positive_indicators"] + cfg["negative_indicators"]}
        assert not keys & others

    def test_a_weighted_indicator_would_count(self):
        cfg = json.loads(json.dumps(load_config("scoring.json")))
        for ind in cfg["domain_indicators"]:
            if ind["key"] == "has_mx_records":
                ind["weight"] = 3
        weighted = LaunchTraceScorer(config=cfg)
        signals = DomainSignals(domain="b.co.uk", checked=True, has_mx=True)
        base = weighted.score(self._ctx(None))
        scored = weighted.score(self._ctx(signals))
        assert "has_mx_records" in {r.key for r in scored.reasons}
        assert scored.value >= base.value


class TestQaReport:
    def test_section_counts(self, pipeline: Pipeline):
        result = pipeline.run(write_outputs=False)
        result.opportunities[0].domain = DomainSignals(
            domain="a.co.uk",
            checked=True,
            web_presence_stage="live_store",
            rdap_created=date(2025, 1, 1),
        )
        result.opportunities[1].domain = DomainSignals(
            domain="b.co.uk",
            checked=True,
            web_presence_stage="holding_page",
            errors=["dns_timeout:A"],
        )
        section = build_qa_report(result)["domain_layer"]
        assert section["probed"] == 2
        assert section["live_store"] == 1 and section["holding_or_parked"] == 1
        assert section["with_errors"] == 1 and section["rdap_dates"] == 1
