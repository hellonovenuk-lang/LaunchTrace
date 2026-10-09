"""Point-in-time (PIT) scoring for backtests.

A backtest asks: *what would LaunchTrace have scored this filing using only
what could have been known at the time?* The answer must not leak the future,
or every weight it suggests is biased towards hindsight.

The policy (config/backtest.json -> ``pit``):

* the **cutoff** for a filing is ``filing_date + pit_window_days`` (0 by
  default: strictly as of the filing date);
* the **stored trade mark record** (mark text, classes, goods text, applicant,
  as published) is the subject of the prediction and is always used;
* beyond it, only observations that are ``point_in_time_safe`` **and** whose
  ``source_date`` is on or before the cutoff **and** whose signal is in
  ``allowed_signals`` may enter the scoring context (``observations_as_of``);
* every other input is dropped or neutralised: web search results (scored as
  "search not run", exactly as the live pipeline does without a provider),
  Companies House status / SIC codes / accounts category (empty), DNS,
  homepage and platform facts (absent), and our own past score (never read);
* the one exception is the *identity link* of a company match (company number
  and match confidence, from ``company_match``), used only to attach a PIT-safe
  ``incorporation_date`` that is inside the cutoff to the applicant. Without
  such a date the applicant is scored as unmatched.

The score itself comes from the real ``LaunchTraceScorer`` through the real
``Pipeline._build_opportunity`` -- the same code path a weekly run takes -- so
the backtest validates the scoring logic actually in use rather than a copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.brands import observations_as_of
from src.classify.pipeline import ProductClassifier
from src.db.tables import Observation, TrademarkRecordRow
from src.enrich.companies_house import NullCompanyRegistry
from src.enrich.domain import NullDomainProber
from src.enrich.providers import NullSearchProvider
from src.enrich.web import WebEnricher
from src.ingest.fixture import FixtureJournalSource
from src.models import (
    ApplicantType,
    CompanyMatch,
    DomainSignals,
    Opportunity,
    TrademarkRecord,
    WebEnrichment,
)
from src.pipeline_core import Pipeline
from src.settings import Settings, get_settings, load_config


@dataclass(frozen=True)
class PitPolicy:
    window_days: int = 0
    allowed_signals: frozenset[str] = frozenset()
    identity_signal: str | None = "company_match"
    not_evaluable: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, config: dict[str, Any] | None = None) -> PitPolicy:
        cfg = config if config is not None else load_config("backtest.json")["pit"]
        return cls(
            window_days=int(cfg.get("pit_window_days", 0)),
            allowed_signals=frozenset(cfg.get("allowed_signals", [])),
            identity_signal=cfg.get("identity_link_signal"),
            not_evaluable={
                k: str(v)
                for k, v in (cfg.get("not_evaluable_indicators") or {}).items()
                if not k.startswith("_")
            },
        )

    def cutoff(self, filing_date: date) -> date:
        return filing_date + timedelta(days=self.window_days)


@dataclass
class PitFacts:
    """What a backtest may know about a brand at one cutoff, and what it refused."""

    cutoff: date
    company_number: str | None = None
    incorporation_date: date | None = None
    match_confidence: int = 0
    domain: str | None = None
    domain_created: date | None = None
    used: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.company_number and self.incorporation_date)


def _value(row: Observation, key: str) -> Any:
    return row.value.get(key) if isinstance(row.value, dict) else None


def pit_facts(session: Session, brand_id: int, cutoff: date, policy: PitPolicy) -> PitFacts:
    """The PIT-safe facts about a brand on ``cutoff`` (see module docstring)."""
    facts = PitFacts(cutoff=cutoff)
    admitted = observations_as_of(session, brand_id, cutoff, pit_safe_only=True)
    admitted_ids = {row.id for row in admitted}
    for row in admitted:
        if row.signal not in policy.allowed_signals:
            facts.refused.append({"signal": row.signal, "reason": "not in allowed_signals"})
            continue
        facts.used.append({"id": row.id, "signal": row.signal, "source_date": str(row.source_date)})
        if row.signal == "incorporation_date":
            raw = _value(row, "incorporation_date")
            facts.incorporation_date = date.fromisoformat(raw) if raw else row.source_date
            facts.company_number = _value(row, "company_number")
        elif row.signal == "domain_created":
            facts.domain = _value(row, "domain")
            facts.domain_created = row.source_date

    # Record what was refused, so a report can say why (and a test can prove it).
    for row in session.execute(
        select(Observation).where(Observation.brand_id == brand_id).order_by(Observation.id)
    ).scalars():
        if row.id in admitted_ids:
            continue
        if not row.point_in_time_safe:
            reason = "not point-in-time safe"
        else:
            reason = f"source_date {row.source_date} after cutoff {cutoff}"
        facts.refused.append({"signal": row.signal, "reason": reason})

    if facts.company_number and policy.identity_signal:
        # Identity link only: which company, and how sure the match was.
        confidence: int | None = None
        for row in session.execute(
            select(Observation)
            .where(
                Observation.brand_id == brand_id,
                Observation.signal == policy.identity_signal,
            )
            .order_by(Observation.observed_at, Observation.id)
        ).scalars():
            if _value(row, "company_number") == facts.company_number:
                confidence = int(_value(row, "match_confidence") or 0)
        if confidence is None:
            # No identity evidence for this company: do not invent a match.
            facts.company_number = None
            facts.incorporation_date = None
        else:
            facts.match_confidence = confidence
    elif not policy.identity_signal:
        facts.company_number = None
        facts.incorporation_date = None
    return facts


def record_from_row(row: TrademarkRecordRow) -> TrademarkRecord:
    return TrademarkRecord(
        trademark_number=row.trademark_number,
        mark_text=row.mark_text,
        mark_type=row.mark_type,
        mark_category=row.mark_category,
        filing_date=row.filing_date,
        publication_date=row.publication_date,
        applicant_name=row.applicant_name,
        applicant_country=row.applicant_country,
        applicant_region=row.applicant_region,
        applicant_postcode_area=row.applicant_postcode_area,
        nice_classes=list(row.nice_classes or []),
        goods_text=row.goods_text,
        goods_text_available=bool(row.goods_text_available),
        series_count=row.series_count or 0,
        status=row.status,
        journal_number=row.journal_number,
        source_url=row.source_url,
        source_name=row.source_name,
    )


def applicant_history(session: Session, record: TrademarkRecord) -> tuple[bool, int]:
    """(first trade mark for this applicant, marks by this applicant in this journal).

    Both from stored source records only: journals before this one, and this
    journal itself -- the same definitions the weekly run uses.
    """
    name = (record.applicant_name or "").strip().lower()
    if not name:
        return True, 1
    earlier = session.execute(
        select(func.count())
        .select_from(TrademarkRecordRow)
        .where(
            func.lower(func.trim(TrademarkRecordRow.applicant_name)) == name,
            TrademarkRecordRow.journal_number < record.journal_number,
        )
    ).scalar_one()
    same = session.execute(
        select(func.count())
        .select_from(TrademarkRecordRow)
        .where(
            func.lower(func.trim(TrademarkRecordRow.applicant_name)) == name,
            TrademarkRecordRow.journal_number == record.journal_number,
        )
    ).scalar_one()
    return earlier == 0, max(int(same), 1)


@dataclass
class PitScore:
    trademark_number: str
    journal_number: str
    filing_date: date
    cutoff: date
    value: int
    band: str
    fired: list[str]
    facts: PitFacts
    opportunity: Opportunity


class PitScorer:
    """Scores a stored record from PIT-safe facts with the real scorer."""

    def __init__(self, settings: Settings | None = None, policy: PitPolicy | None = None) -> None:
        base = settings or get_settings()
        self.settings = base.model_copy(
            update={
                "search_provider": "none",
                "search_api_key": "",
                "llm_provider": "none",
                "llm_api_key": "",
                "domain_layer_enabled": False,
            }
        )
        self.policy = policy or PitPolicy.load()
        # Every external dependency is a null: this object never touches the
        # network, a paid API or the live register. Only its pure helpers are used.
        self.pipeline = Pipeline(
            settings=self.settings,
            source=FixtureJournalSource(self.settings),
            registry=NullCompanyRegistry(),
            classifier=ProductClassifier(self.settings),
            web=WebEnricher(provider=NullSearchProvider(), settings=self.settings),
            domain=NullDomainProber(),
        )

    def score(self, session: Session, brand_id: int, row: TrademarkRecordRow) -> PitScore | None:
        record = record_from_row(row)
        if record.filing_date is None:
            return None
        cutoff = self.policy.cutoff(record.filing_date)
        facts = pit_facts(session, brand_id, cutoff, self.policy)
        first, mark_count = applicant_history(session, record)

        if facts.matched:
            match = CompanyMatch(
                matched=True,
                company_number=facts.company_number,
                incorporation_date=facts.incorporation_date,
                match_confidence=facts.match_confidence,
                match_method="pit_identity_link",
                provider="backtest",
            )
        else:
            match = CompanyMatch(matched=False, match_method="pit_unmatched", provider="backtest")
        age = match.age_years_at(record.filing_date) if match.matched else None
        if age is not None and age < 0:
            age = 0.0  # as the weekly run does for a company incorporated just after filing

        domain: DomainSignals | None = None
        if facts.domain_created is not None:
            domain = DomainSignals(
                domain=facts.domain,
                domain_source="pit",
                checked=True,
                prober="pit",
                rdap_fetched=True,
                rdap_created=facts.domain_created,
            )

        name = record.applicant_name
        flt = self.pipeline.filter
        applicant_type = (
            ApplicantType.CORPORATE
            if flt.looks_corporate(name)
            else ApplicantType.NATURAL_PERSON
            if name
            else ApplicantType.UNKNOWN
        )
        outcome = flt.assess(record)
        web = WebEnrichment(attempted=False, provider="none")
        opp = self.pipeline._build_opportunity(
            record,
            outcome,
            applicant_type,
            match,
            age,
            web,
            applicant_journal_mark_count=mark_count,
            first_trademark=first,
            domain=domain,
        )
        fired = [r.key for r in opp.score.reasons] + [r.key for r in opp.score.negative_reasons]
        if opp.domain is not None:
            fired += [k for k in opp.domain.indicators if k not in fired]
        return PitScore(
            trademark_number=record.trademark_number,
            journal_number=record.journal_number,
            filing_date=record.filing_date,
            cutoff=cutoff,
            value=opp.score.value,
            band=opp.score.band.value,
            fired=fired,
            facts=facts,
            opportunity=opp,
        )
