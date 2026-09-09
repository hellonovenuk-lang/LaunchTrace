"""Loading LaunchTrace opportunities for sales use.

A ``Lead`` is a read-only view of one opportunity, flat enough to rank, render
and quote in an email. It exists so the sales tooling never reaches into the
pipeline's internals and can never write back to them.

Two sources, both real output:

* the database, which is what a live weekly run fills;
* a delivered CSV, which is what you already have from the historical
  validation and what an operator can point at on any machine.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.tables import OpportunityRow
from src.settings import REPORTS_DIR, load_config

# Which CSV column carries which buying-intent category.
_CSV_INTENT_COLUMNS = {
    "flexible_packaging": "packaging_relevance",
    "labels": "label_relevance",
    "cartons": "carton_relevance",
    "contract_manufacturing": "contract_manufacturing_relevance",
    "copacking": "copacking_relevance",
    "distribution": "distribution_relevance",
    "brokerage": "brokerage_relevance",
    "fulfilment": "fulfilment_relevance",
    "marketing": "marketing_relevance",
}


@dataclass
class Lead:
    """One opportunity, as a salesperson would describe it."""

    brand_name: str
    trademark_number: str
    product_category: str = ""  # taxonomy key, e.g. "snacks"
    product_category_label: str = ""
    goods_summary: str = ""
    applicant_name: str = ""
    company_name: str = ""
    company_number: str = ""
    company_incorporation_date: date | None = None
    company_region: str = ""
    company_age_years_at_filing: float | None = None
    website: str = ""
    launch_stage: str = "unknown"
    retail_presence: str = "unknown"
    intents: dict[str, str] = field(default_factory=dict)
    score: int = 0
    band: str = "SUPPRESS"
    reasons: list[str] = field(default_factory=list)
    source_url: str = ""
    evidence_urls: list[str] = field(default_factory=list)
    filing_date: date | None = None
    publication_date: date | None = None
    journal_number: str = ""
    suppressed: bool = False
    intents_complete: bool = True

    @property
    def display_company(self) -> str:
        return self.company_name or self.applicant_name or "Company not matched"

    @property
    def display_category(self) -> str:
        return self.product_category_label or self.product_category.replace("_", " ").title()

    def intent(self, key: str) -> str:
        """The relevance band for one supplier category.

        A category the source file never carried comes back as UNKNOWN rather
        than NONE. Reporting "no relevance" for something that was simply not
        recorded would be a claim we cannot support.
        """
        if key in self.intents:
            return self.intents[key]
        return "NONE" if self.intents_complete else "UNKNOWN"

    def early_stage_evidence(self) -> str:
        """One line on why this looks early, drawn only from stored reasons.

        Never asserts more than the score reasons already say.
        """
        preferred = ("incorporat", "first trade mark", "single mark", "no established")
        for reason in self.reasons:
            if any(marker in reason.lower() for marker in preferred):
                return reason
        return self.reasons[0] if self.reasons else "Newly published UK trade mark filing"


def _label_to_key() -> dict[str, str]:
    taxonomy = load_config("food_taxonomy.json")
    return {
        str(group["label"]).strip().lower(): str(group["key"])
        for group in taxonomy.get("product_groups", [])
    }


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def leads_from_csv(path: str | Path) -> list[Lead]:
    """Read a delivered opportunities CSV.

    Tolerant of older files: the carton and brokerage columns were added after
    the historical validation ran, so a file without them is read with those
    two categories marked unknown rather than silently scored as NONE.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No opportunities CSV at {path}")
    label_to_key = _label_to_key()
    leads: list[Lead] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = set(reader.fieldnames or [])
        missing_intent = {
            key for key, column in _CSV_INTENT_COLUMNS.items() if column not in columns
        }
        for row in reader:
            label = (row.get("product_category") or "").strip()
            intents = {
                key: (row.get(column) or "NONE").strip().upper()
                for key, column in _CSV_INTENT_COLUMNS.items()
                if column in columns
            }
            leads.append(
                Lead(
                    brand_name=(row.get("brand_name") or "").strip(),
                    trademark_number=(row.get("trademark_number") or "").strip(),
                    product_category=label_to_key.get(label.lower(), ""),
                    product_category_label=label,
                    goods_summary=(row.get("goods_summary") or "").strip(),
                    company_name=(row.get("company_name") or "").strip(),
                    company_number=(row.get("company_number") or "").strip(),
                    company_incorporation_date=_parse_date(
                        row.get("company_incorporation_date", "")
                    ),
                    company_region=(row.get("company_region") or "").strip(),
                    website=(row.get("website") or "").strip(),
                    launch_stage=(row.get("launch_stage") or "unknown")
                    .strip()
                    .lower()
                    .replace(" ", "_"),
                    retail_presence=(row.get("retail_presence") or "unknown").strip(),
                    intents=intents,
                    score=int(row.get("launchtrace_score") or 0),
                    band=(row.get("score_band") or "SUPPRESS").strip().upper(),
                    reasons=[
                        r.strip() for r in (row.get("score_reasons") or "").split("|") if r.strip()
                    ],
                    source_url=(row.get("source_url") or "").strip(),
                    evidence_urls=[
                        u for u in (row.get("evidence_urls") or "").split(" ") if u.strip()
                    ],
                    filing_date=_parse_date(row.get("filing_date", "")),
                    publication_date=_parse_date(row.get("publication_date", "")),
                    journal_number=(row.get("journal_number") or "").strip(),
                    intents_complete=not missing_intent,
                )
            )
    return leads


def leads_from_db(
    session: Session,
    journal_number: str | None = None,
    limit: int = 500,
    include_suppressed: bool = False,
) -> list[Lead]:
    """Read stored opportunities, newest journal first unless one is named."""
    stmt = select(OpportunityRow)
    if journal_number:
        stmt = stmt.where(OpportunityRow.journal_number == journal_number)
    if not include_suppressed:
        stmt = stmt.where(OpportunityRow.suppressed.is_(False))
    stmt = stmt.order_by(
        OpportunityRow.publication_date.desc(), OpportunityRow.launchtrace_score.desc()
    ).limit(limit)

    taxonomy = load_config("food_taxonomy.json")
    key_to_label = {str(g["key"]): str(g["label"]) for g in taxonomy.get("product_groups", [])}
    leads: list[Lead] = []
    for row in session.execute(stmt).scalars():
        leads.append(
            Lead(
                brand_name=row.brand_name or "",
                trademark_number=row.trademark_number,
                product_category=row.product_category or "",
                product_category_label=key_to_label.get(row.product_category or "", ""),
                goods_summary=row.goods_summary or "",
                applicant_name=row.applicant_name or "",
                company_name=row.company_name or "",
                company_number=row.company_number or "",
                company_incorporation_date=row.company_incorporation_date,
                company_region=row.company_region or "",
                company_age_years_at_filing=row.company_age_years_at_filing,
                website=row.website or "",
                launch_stage=row.launch_stage or "unknown",
                retail_presence=row.retail_presence or "unknown",
                intents={k: str(v).upper() for k, v in (row.buying_intent or {}).items()},
                score=row.launchtrace_score,
                band=row.score_band,
                reasons=list(row.score_reasons or []),
                source_url=row.source_url or "",
                evidence_urls=list(row.evidence_urls or []),
                filing_date=row.filing_date,
                publication_date=row.publication_date,
                journal_number=row.journal_number,
                suppressed=row.suppressed,
            )
        )
    return leads


def default_csv_candidates() -> list[Path]:
    """Where to look for a delivered CSV when no source is named.

    Most recent weekly run first, then the historical validation output, which
    is the file that exists before the first live Friday.
    """
    candidates: list[Path] = []
    runs_dir = REPORTS_DIR / "runs"
    if runs_dir.exists():
        for run in sorted(runs_dir.iterdir(), reverse=True):
            csv_path = run / "opportunities.csv"
            if csv_path.exists():
                candidates.append(csv_path)
    validation = REPORTS_DIR / "validation" / "top_opportunities.csv"
    if validation.exists():
        candidates.append(validation)
    return candidates


def load_leads(
    session: Session | None = None,
    csv_path: str | Path | None = None,
    journal_number: str | None = None,
) -> tuple[list[Lead], str]:
    """Get the best available leads, and say where they came from.

    Explicit CSV wins, then the database, then the newest CSV on disk. The
    source string is shown to the operator so a preview is never mistaken for
    fresher data than it is.
    """
    if csv_path:
        return leads_from_csv(csv_path), str(csv_path)
    if session is not None:
        leads = leads_from_db(session, journal_number=journal_number)
        if leads:
            source = f"database ({journal_number})" if journal_number else "database"
            return leads, source
    for candidate in default_csv_candidates():
        leads = leads_from_csv(candidate)
        if leads:
            return leads, str(candidate)
    return [], "no source found"


__all__ = ["Lead", "leads_from_csv", "leads_from_db", "load_leads"]
