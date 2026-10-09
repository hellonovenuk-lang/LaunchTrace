"""Decide what the public feed may show, from the database.

Everything that protects privacy lives in one function, :func:`publishable`,
and in :func:`to_public_entry`, which copies an explicit whitelist of fields.
Nothing else in this package reads a lead's raw columns. The rules (see
docs/PUBLIC_FEED.md and config/public_feed.json):

* company level only: a confirmed Companies House match (company number on the
  lead, the stored match row says ``matched``, applicant type ``corporate``);
* never the applicant's name, never an officer, contact page, email, website,
  post town or postcode;
* only delivered-quality leads: not suppressed or rejected, band allowed;
* nothing matching an active suppression rule or an opted-out company;
* a week is public only ``delay_weeks`` after the newest journal in the
  database, and only the top ``top_n_per_week`` companies of it.

The output is a pure function of the database contents and the config, so a
build is byte-for-byte repeatable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.brands import brand_uid_for, journal_sort_key
from src.db.repository import active_suppressions
from src.db.tables import (
    Brand,
    CompanyMatchRow,
    Journal,
    OpportunityRow,
    ProspectSuppression,
)
from src.deliver.csv_export import _HUMAN_STAGE
from src.enrich.companies_house import CH_COMPANY_URL
from src.parse.normalise import normalise_company_name
from src.parse.open_data import IPO_CASE_URL
from src.settings import load_config

CONFIG_NAME = "public_feed.json"

# Never published, whatever the config says.
_NEVER_BANDS = {"SUPPRESS"}
_BAND_RANK = {"HIGH": 2, "MEDIUM": 1, "SUPPRESS": 0}
_EXCLUDED_REVIEW_STATES = {"rejected", "suppressed"}

_URL_RE = re.compile(r"(https?://|www\.|\.co\.uk\b|\.com\b|@)", re.IGNORECASE)
# A full or partial UK postcode, or anything with a digit: a region never has one.
_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?(\s*\d[A-Z]{2})?\b", re.IGNORECASE)
_PUBLIC_SOURCE_HOSTS = ("ipo.gov.uk", "www.ipo.gov.uk", "trademarks.ipo.gov.uk")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedConfig:
    site_title: str = "LaunchTrace: new UK food brands"
    site_description: str = ""
    top_n_per_week: int = 5
    delay_weeks: int = 1
    min_band: str = "MEDIUM"
    bands_allowed: tuple[str, ...] = ("HIGH", "MEDIUM")
    max_weeks_in_index: int = 12
    reason_keys_allowed: tuple[str, ...] = ()
    fallback_reason: str = "New food trade mark filed by a UK company"
    cta_text: str = "Get the full weekly list"
    cta_path: str = "/#sample"
    public_base_url: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FeedConfig:
        default = cls()
        return cls(
            site_title=str(raw.get("site_title", default.site_title)),
            site_description=str(raw.get("site_description", default.site_description)),
            top_n_per_week=max(0, int(raw.get("top_n_per_week", default.top_n_per_week))),
            delay_weeks=max(0, int(raw.get("delay_weeks", default.delay_weeks))),
            min_band=str(raw.get("min_band", default.min_band)).upper(),
            bands_allowed=tuple(
                str(b).upper() for b in raw.get("bands_allowed", default.bands_allowed)
            ),
            max_weeks_in_index=max(
                1, int(raw.get("max_weeks_in_index", default.max_weeks_in_index))
            ),
            reason_keys_allowed=tuple(raw.get("reason_keys_allowed", ())),
            fallback_reason=str(raw.get("fallback_reason", default.fallback_reason)),
            cta_text=str(raw.get("cta_text", default.cta_text)),
            cta_path=str(raw.get("cta_path", default.cta_path)),
            public_base_url=str(raw.get("public_base_url", "") or ""),
        )

    def band_allowed(self, band: str | None) -> bool:
        band = (band or "").upper()
        if band in _NEVER_BANDS or band not in self.bands_allowed:
            return False
        return _BAND_RANK.get(band, -1) >= _BAND_RANK.get(self.min_band, 99)


def load_feed_config() -> FeedConfig:
    return FeedConfig.from_dict(load_config(CONFIG_NAME))


# ---------------------------------------------------------------------------
# data shapes
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A stored lead, with the private fields the filter needs. Never rendered."""

    journal_number: str
    publication_date: date | None
    trademark_number: str
    brand_name: str | None
    applicant_name: str | None
    applicant_type: str
    company_name: str | None
    company_number: str | None
    match_confirmed: bool
    post_town: str | None
    region: str | None
    product_category: str | None
    launch_stage: str
    filing_date: date | None
    score: int
    band: str
    reasons: list[str]
    review_state: str
    suppressed: bool
    source_url: str | None
    brand_uid: str | None


@dataclass(frozen=True)
class PublicEntry:
    """Exactly what may be published about one company in one week."""

    id: str
    brand_name: str
    company_name: str
    company_number: str
    product_category: str | None
    launch_stage: str
    filing_date: str | None
    region: str | None
    reason: str
    trademark_number: str
    ukipo_url: str
    companies_house_url: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "brand_name": self.brand_name,
            "company_name": self.company_name,
            "company_number": self.company_number,
            "product_category": self.product_category,
            "launch_stage": self.launch_stage,
            "filing_date": self.filing_date,
            "region": self.region,
            "reason": self.reason,
            "trademark_number": self.trademark_number,
            "ukipo_url": self.ukipo_url,
            "companies_house_url": self.companies_house_url,
        }


@dataclass(frozen=True)
class Week:
    journal_number: str
    publication_date: date
    entries: tuple[PublicEntry, ...]
    qualifying_count: int

    @property
    def slug(self) -> str:
        return journal_slug(self.journal_number)


@dataclass(frozen=True)
class Feed:
    config: FeedConfig
    weeks: tuple[Week, ...]
    updated: date | None
    pending_weeks: int = 0
    exclusions: dict[str, int] = field(default_factory=dict, compare=False)

    def week(self, journal_number: str) -> Week | None:
        for week in self.weeks:
            if week.journal_number == journal_number or week.slug == journal_number:
                return week
        return None


def journal_slug(journal_number: str) -> str:
    """A file-name-safe form of a journal number (they are already, in practice)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", journal_number.strip()) or "journal"


# ---------------------------------------------------------------------------
# the filter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Suppressions:
    """Active suppression values, lower-cased, plus normalised company names."""

    company: frozenset[str] = frozenset()
    applicant: frozenset[str] = frozenset()
    mark: frozenset[str] = frozenset()
    company_normalised: frozenset[str] = frozenset()

    @classmethod
    def from_session(cls, session: Session) -> Suppressions:
        rules = active_suppressions(session)
        opted_out = {
            (row.company_name or row.value or "").strip()
            for row in session.execute(
                select(ProspectSuppression).where(ProspectSuppression.kind == "company")
            ).scalars()
        }
        company = rules.get("company", set())
        applicant = rules.get("applicant", set())
        normalised = {normalise_company_name(v) for v in company | applicant | opted_out}
        return cls(
            company=frozenset(company),
            applicant=frozenset(applicant),
            mark=frozenset(rules.get("mark", set())),
            company_normalised=frozenset(n for n in normalised if n),
        )


def _low(value: str | None) -> str:
    return (value or "").strip().lower()


def publishable(c: Candidate, config: FeedConfig, suppressions: Suppressions) -> str | None:
    """Why this lead may not be published, or None if it may.

    The single gate for the public feed. Every rule is conservative: when a
    fact is missing, the lead is left out.
    """
    if (c.applicant_type or "").lower() != "corporate":
        return "not_corporate"
    if not (c.company_number or "").strip() or not (c.company_name or "").strip():
        return "no_company"
    if not c.match_confirmed:
        return "company_not_confirmed"
    if c.suppressed or (c.review_state or "").lower() in _EXCLUDED_REVIEW_STATES:
        return "suppressed"
    if not config.band_allowed(c.band):
        return "band"
    if c.publication_date is None:
        return "no_publication_date"
    if not (c.brand_name or "").strip():
        return "no_brand_name"
    company_values = {_low(c.company_name), _low(c.company_number)}
    if company_values & (suppressions.company | suppressions.applicant):
        return "suppression_rule"
    if _low(c.applicant_name) and _low(c.applicant_name) in (
        suppressions.applicant | suppressions.company
    ):
        return "suppression_rule"
    if {_low(c.brand_name), _low(c.trademark_number)} & suppressions.mark:
        return "suppression_rule"
    normalised = {normalise_company_name(c.company_name), normalise_company_name(c.applicant_name)}
    if normalised & suppressions.company_normalised:
        return "suppression_rule"
    return None


# ---------------------------------------------------------------------------
# field sanitising
# ---------------------------------------------------------------------------


@cache
def _category_labels() -> dict[str, str]:
    taxonomy = load_config("food_taxonomy.json")
    return {g["key"]: g["label"] for g in taxonomy.get("product_groups", [])}


def category_label(key: str | None) -> str | None:
    if not key:
        return None
    return _category_labels().get(key) or key.replace("_", " ").capitalize()


def public_region(region: str | None, post_town: str | None) -> str | None:
    """Region level only. Anything that could be a town or postcode is dropped."""
    text = (region or "").strip()
    if not text or any(ch.isdigit() for ch in text) or _POSTCODE_RE.fullmatch(text):
        return None
    if post_town and _low(text) == _low(post_town):
        return None
    if len(text) > 64:
        return None
    return text.title() if text.isupper() else text


@cache
def _reason_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    scoring = load_config("scoring.json")
    out: list[tuple[str, re.Pattern[str]]] = []
    for section in ("positive_indicators", "negative_indicators"):
        indicators = scoring.get(section, {})
        items = (
            list(indicators.items())
            if isinstance(indicators, dict)
            else [(i.get("key"), i) for i in indicators if isinstance(i, dict)]
        )
        for key, ind in items:
            template = ind.get("reason_template") if isinstance(ind, dict) else None
            if not template:
                continue
            parts = re.split(r"\{[a-z_]+\}", template)
            pattern = ".+?".join(re.escape(p) for p in parts)
            out.append((str(key), re.compile(f"^{pattern}$", re.DOTALL)))
    return tuple(out)


def reason_key(text: str) -> str | None:
    """The scoring indicator a stored reason text was rendered from."""
    for key, pattern in _reason_patterns():
        if pattern.match(text):
            return key
    return None


def public_reason(c: Candidate, config: FeedConfig) -> str:
    """The first stored reason from an allowed indicator that names nobody."""
    allowed = set(config.reason_keys_allowed)
    applicant = _low(c.applicant_name)
    for text in c.reasons or []:
        if not isinstance(text, str) or not text.strip():
            continue
        if reason_key(text) not in allowed:
            continue
        if _URL_RE.search(text):
            continue
        if applicant and applicant in text.lower():
            continue
        if len(text) > 200:
            continue
        return text.strip()
    return config.fallback_reason


def ukipo_url(c: Candidate) -> str:
    """The UKIPO record. A stored URL only if it is an https link on the IPO's site."""
    parsed = urlparse(c.source_url or "")
    if (
        parsed.scheme == "https"
        and (parsed.hostname or "") in _PUBLIC_SOURCE_HOSTS
        and c.trademark_number in (c.source_url or "")
    ):
        return c.source_url or ""
    return IPO_CASE_URL.format(number=c.trademark_number)


def to_public_entry(c: Candidate, config: FeedConfig) -> PublicEntry:
    """Copy the whitelisted, sanitised fields. Call only after :func:`publishable`."""
    number = (c.company_number or "").strip().upper()
    uid = c.brand_uid or brand_uid_for(f"ch:{number}")
    return PublicEntry(
        id=f"urn:launchtrace:feed:{journal_slug(c.journal_number)}:{uid}",
        brand_name=(c.brand_name or "").strip(),
        company_name=(c.company_name or "").strip(),
        company_number=number,
        product_category=category_label(c.product_category),
        launch_stage=_HUMAN_STAGE.get(c.launch_stage, "Unknown"),
        filing_date=c.filing_date.isoformat() if c.filing_date else None,
        region=public_region(c.region, c.post_town),
        reason=public_reason(c, config),
        trademark_number=c.trademark_number,
        ukipo_url=ukipo_url(c),
        companies_house_url=CH_COMPANY_URL.format(number=number),
    )


# ---------------------------------------------------------------------------
# reading the database
# ---------------------------------------------------------------------------


def load_candidates(session: Session) -> list[Candidate]:
    matches: dict[str, CompanyMatchRow] = {}
    for m in session.execute(select(CompanyMatchRow).order_by(CompanyMatchRow.id)).scalars():
        matches[m.dedupe_key] = m  # newest row per key wins
    stmt = (
        select(OpportunityRow, Brand.brand_uid)
        .outerjoin(Brand, OpportunityRow.brand_id == Brand.id)
        .order_by(OpportunityRow.journal_number, OpportunityRow.trademark_number)
    )
    out: list[Candidate] = []
    for row, brand_uid in session.execute(stmt):
        match = matches.get(row.dedupe_key)
        confirmed = bool(
            match is not None
            and match.matched
            and (match.company_number or "").strip().upper()
            == (row.company_number or "").strip().upper()
        )
        out.append(
            Candidate(
                journal_number=row.journal_number,
                publication_date=row.publication_date,
                trademark_number=row.trademark_number,
                brand_name=row.brand_name,
                applicant_name=row.applicant_name,
                applicant_type=row.applicant_type,
                company_name=row.company_name,
                company_number=row.company_number,
                match_confirmed=confirmed,
                post_town=match.post_town if match is not None else None,
                region=row.company_region,
                product_category=row.product_category,
                launch_stage=row.launch_stage,
                filing_date=row.filing_date,
                score=row.launchtrace_score or 0,
                band=row.score_band,
                reasons=list(row.score_reasons or []),
                review_state=row.review_state,
                suppressed=bool(row.suppressed),
                source_url=row.source_url,
                brand_uid=brand_uid,
            )
        )
    return out


def _journal_dates(session: Session, candidates: list[Candidate]) -> dict[str, date]:
    """Publication date per journal number: the journals table, else the leads."""
    dates: dict[str, date] = {}
    for c in candidates:
        if c.publication_date and (
            c.journal_number not in dates or c.publication_date < dates[c.journal_number]
        ):
            dates[c.journal_number] = c.publication_date
    for number, published in session.execute(
        select(Journal.journal_number, Journal.publication_date)
    ):
        if published is not None:
            dates[number] = min(published, dates.get(number, published))
    return dates


def build_feed(session: Session, config: FeedConfig | None = None) -> Feed:
    """Everything the public may see, newest week first."""
    config = config or load_feed_config()
    suppressions = Suppressions.from_session(session)
    candidates = load_candidates(session)
    dates = _journal_dates(session, candidates)

    # The delay is measured against the newest journal we hold, not the clock,
    # so the same database always builds the same feed.
    newest = max(dates.values(), default=None)
    cutoff = newest - timedelta(weeks=config.delay_weeks) if newest else None

    exclusions: dict[str, int] = {}
    by_week: dict[str, dict[str, tuple[Candidate, PublicEntry]]] = {}
    for c in candidates:
        published = dates.get(c.journal_number)
        if published is None or cutoff is None or published > cutoff:
            exclusions["delay"] = exclusions.get("delay", 0) + 1
            continue
        why = publishable(c, config, suppressions)
        if why:
            exclusions[why] = exclusions.get(why, 0) + 1
            continue
        entry = to_public_entry(c, config)
        week = by_week.setdefault(c.journal_number, {})
        # One company, one entry per week: its best-scoring mark.
        held = week.get(entry.company_number)
        if held is None or (-c.score, c.trademark_number) < (
            -held[0].score,
            held[0].trademark_number,
        ):
            week[entry.company_number] = (c, entry)

    order = sorted(by_week, key=lambda j: (dates[j], journal_sort_key(j)), reverse=True)
    weeks: list[Week] = []
    for number in order[: config.max_weeks_in_index]:
        ranked = sorted(
            by_week[number].values(),
            key=lambda pair: (-pair[0].score, pair[1].brand_name.lower(), pair[1].trademark_number),
        )
        weeks.append(
            Week(
                journal_number=number,
                publication_date=dates[number],
                entries=tuple(e for _, e in ranked[: config.top_n_per_week]),
                qualifying_count=len(ranked),
            )
        )
    pending = sum(1 for d in dates.values() if cutoff is None or d > cutoff)
    return Feed(
        config=config,
        weeks=tuple(weeks),
        updated=max((w.publication_date for w in weeks), default=None),
        pending_weeks=pending,
        exclusions=dict(sorted(exclusions.items())),
    )
