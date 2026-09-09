"""Reading and writing the prospect list.

``outreach/prospects.csv`` is the source of truth. This module is the only code
that writes it, and every write is whole-file: the list is small, and a partial
write is worse than a slow one.

Two safety properties matter more than anything else here:

* **A suppressed or opted-out prospect can never be silently revived.** Import
  and update paths both refuse.
* **The same business cannot enter the list twice**, whether by name, domain or
  company number, because contacting someone twice is the fastest way to lose
  them.
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.logging_setup import get_logger
from src.sales.models import (
    EmailSource,
    Priority,
    Prospect,
    ProspectStatus,
    ReplyState,
    TransitionError,
    check_transition,
    normalise_company,
    normalise_domain,
    today,
)
from src.settings import REPO_ROOT

log = get_logger(__name__)

OUTREACH_DIR = REPO_ROOT / "outreach"
PROSPECTS_CSV = OUTREACH_DIR / "prospects.csv"
SUPPRESSIONS_CSV = OUTREACH_DIR / "suppressions.csv"

CSV_COLUMNS = [
    "prospect_id",
    "company_name",
    "website",
    "companies_house_number",
    "company_type",
    "supplier_category",
    "supplier_subcategory",
    "geography",
    "icp_reason",
    "products_services",
    "buying_intent_categories",
    "contact_route",
    "generic_contact_email",
    "named_contact",
    "decision_maker_role",
    "email_source",
    "company_size_hint",
    "status",
    "priority",
    "icp_score",
    "date_added",
    "email_1_sent_date",
    "sample_requested_date",
    "sample_sent_date",
    "offer_sent_date",
    "converted_date",
    "follow_up_due_date",
    "stripe_customer_id",
    "reply_state",
    "opted_out",
    "suppression_reason",
    "notes",
]

SUPPRESSION_COLUMNS = [
    "value",
    "kind",
    "company_name",
    "date_added",
    "reason",
    "added_by",
]

_DATE_FIELDS = [
    "date_added",
    "email_1_sent_date",
    "sample_requested_date",
    "sample_sent_date",
    "offer_sent_date",
    "converted_date",
    "follow_up_due_date",
]


class DuplicateProspectError(ValueError):
    """A business already on the list, arriving again under any identity."""


class SuppressedProspectError(ValueError):
    """An attempt to contact or revive someone who is off-limits."""


@dataclass
class SuppressionEntry:
    value: str
    kind: str = "email"  # email | domain | company
    company_name: str = ""
    date_added: str = ""
    reason: str = ""
    added_by: str = "operator"

    @property
    def key(self) -> str:
        text = self.value.strip().lower()
        if self.kind == "domain":
            return normalise_domain(text)
        if self.kind == "company":
            return normalise_company(text)
        return text


@dataclass
class SuppressionList:
    """Who must never be contacted, by any identity we can check.

    Held separately from the prospect list so that deleting a prospect row can
    never delete the record of their opt-out.
    """

    entries: list[SuppressionEntry] = field(default_factory=list)

    @property
    def emails(self) -> set[str]:
        return {e.key for e in self.entries if e.kind == "email" and e.key}

    @property
    def domains(self) -> set[str]:
        return {e.key for e in self.entries if e.kind == "domain" and e.key}

    @property
    def companies(self) -> set[str]:
        return {e.key for e in self.entries if e.kind == "company" and e.key}

    def blocks(self, prospect: Prospect) -> str | None:
        """Returns the reason this prospect is blocked, or None."""
        email = (prospect.generic_contact_email or "").strip().lower()
        if email and email in self.emails:
            return f"email {email} is on the suppression list"
        if prospect.domain and prospect.domain in self.domains:
            return f"domain {prospect.domain} is on the suppression list"
        if prospect.company_key and prospect.company_key in self.companies:
            return f"company {prospect.company_name} is on the suppression list"
        return None

    def add(self, entry: SuppressionEntry) -> bool:
        """Returns True if this was new. Suppression is never removed here."""
        if any(e.kind == entry.kind and e.key == entry.key for e in self.entries):
            return False
        self.entries.append(entry)
        return True


# ---------------------------------------------------------------------------
# suppression list
# ---------------------------------------------------------------------------


def load_suppressions(path: Path | None = None) -> SuppressionList:
    path = path or SUPPRESSIONS_CSV
    if not path.exists():
        return SuppressionList()
    entries: list[SuppressionEntry] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            # The first version of this file used an 'email' column. Read both
            # shapes so an existing list is never lost on upgrade.
            value = (row.get("value") or row.get("email") or "").strip()
            if not value:
                continue
            entries.append(
                SuppressionEntry(
                    value=value,
                    kind=(row.get("kind") or "email").strip() or "email",
                    company_name=(row.get("company_name") or "").strip(),
                    date_added=(row.get("date_added") or "").strip(),
                    reason=(row.get("reason") or "").strip(),
                    added_by=(row.get("added_by") or "").strip() or "operator",
                )
            )
    return SuppressionList(entries=entries)


def save_suppressions(suppressions: SuppressionList, path: Path | None = None) -> Path:
    """Append-only in spirit: this never writes fewer entries than it read."""
    path = path or SUPPRESSIONS_CSV
    existing = load_suppressions(path)
    merged = SuppressionList(entries=list(existing.entries))
    for entry in suppressions.entries:
        merged.add(entry)
    if len(merged.entries) < len(existing.entries):  # pragma: no cover - defensive
        raise SuppressedProspectError("Refusing to write a shorter suppression list")
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUPPRESSION_COLUMNS)
        writer.writeheader()
        for entry in merged.entries:
            writer.writerow(
                {
                    "value": entry.value,
                    "kind": entry.kind,
                    "company_name": entry.company_name,
                    "date_added": entry.date_added,
                    "reason": entry.reason,
                    "added_by": entry.added_by,
                }
            )
    return path


def _backup(path: Path) -> None:
    """Keep the previous version of anything we overwrite.

    The suppression list in particular must survive a mistake, so the copy is
    taken before every write rather than on a schedule.
    """
    if not path.exists():
        return
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / f"{path.stem}.previous{path.suffix}")


# ---------------------------------------------------------------------------
# prospects
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _row_to_prospect(row: dict[str, str]) -> Prospect:
    data: dict[str, object] = {}
    for column in CSV_COLUMNS:
        value = (row.get(column) or "").strip()
        if column in _DATE_FIELDS:
            data[column] = _parse_date(value)
        elif column == "opted_out":
            data[column] = value.lower() in {"true", "yes", "1", "y"}
        elif column == "icp_score":
            data[column] = int(value) if value.isdigit() else 0
        elif column == "status":
            data[column] = ProspectStatus(value) if value else ProspectStatus.RESEARCHED
        elif column == "priority":
            data[column] = Priority(value) if value else Priority.C
        elif column == "reply_state":
            data[column] = ReplyState(value) if value else ReplyState.NONE
        elif column == "email_source":
            data[column] = EmailSource(value) if value else EmailSource.NONE
        else:
            data[column] = value
    return Prospect(**data)  # type: ignore[arg-type]


def _prospect_to_row(prospect: Prospect) -> dict[str, str]:
    row: dict[str, str] = {}
    for column in CSV_COLUMNS:
        value = getattr(prospect, column)
        if column in _DATE_FIELDS:
            row[column] = value.isoformat() if value else ""
        elif column == "opted_out":
            row[column] = "true" if value else "false"
        elif column == "buying_intent_categories":
            row[column] = "|".join(value)
        elif hasattr(value, "value"):
            row[column] = str(value.value)
        else:
            row[column] = "" if value is None else str(value)
    return row


class ProspectStore:
    """The prospect list, loaded once and saved deliberately."""

    def __init__(self, prospects: list[Prospect], path: Path, suppressions: SuppressionList):
        self.prospects = prospects
        self.path = path
        self.suppressions = suppressions

    # -- lookup ------------------------------------------------------------

    def get(self, prospect_id: str) -> Prospect | None:
        wanted = (prospect_id or "").strip().upper()
        for p in self.prospects:
            if p.prospect_id.upper() == wanted:
                return p
        return None

    def require(self, prospect_id: str) -> Prospect:
        found = self.get(prospect_id)
        if found is None:
            raise KeyError(
                f"No prospect with id {prospect_id!r}. "
                "List them with: python -m src.admin prospects list"
            )
        return found

    def find_by_company(self, name: str) -> Prospect | None:
        key = normalise_company(name)
        if not key:
            return None
        return next((p for p in self.prospects if p.company_key == key), None)

    def find_by_domain(self, website: str) -> Prospect | None:
        key = normalise_domain(website)
        if not key:
            return None
        return next((p for p in self.prospects if p.domain == key), None)

    def find_by_email(self, email: str) -> Prospect | None:
        key = (email or "").strip().lower()
        if not key:
            return None
        return next(
            (p for p in self.prospects if p.generic_contact_email.strip().lower() == key), None
        )

    # -- duplicates --------------------------------------------------------

    def duplicate_of(self, candidate: Prospect) -> tuple[Prospect, str] | None:
        """The existing row this candidate would duplicate, and how it matched."""
        if candidate.companies_house_number:
            number = candidate.companies_house_number.strip().lstrip("0").upper()
            for p in self.prospects:
                if p is candidate or not p.companies_house_number:
                    continue
                if p.companies_house_number.strip().lstrip("0").upper() == number:
                    return p, "company number"
        if candidate.domain:
            for p in self.prospects:
                if p is not candidate and p.domain and p.domain == candidate.domain:
                    return p, "website domain"
        if candidate.company_key:
            for p in self.prospects:
                if p is not candidate and p.company_key == candidate.company_key:
                    return p, "company name"
        if candidate.generic_contact_email:
            email = candidate.generic_contact_email.strip().lower()
            for p in self.prospects:
                if p is not candidate and p.generic_contact_email.strip().lower() == email:
                    return p, "contact email"
        return None

    def duplicates(self, unresolved_only: bool = True) -> list[tuple[Prospect, Prospect, str]]:
        """Duplicate pairs in the list.

        A pair where one side is already suppressed has been dealt with, so by
        default it is not reported again — otherwise the audit never comes back
        clean and stops being read.
        """
        found: list[tuple[Prospect, Prospect, str]] = []
        seen: set[str] = set()
        for i, candidate in enumerate(self.prospects):
            for other in self.prospects[:i]:
                reason = _same_business(candidate, other)
                if not reason:
                    continue
                if unresolved_only and not (candidate.contactable and other.contactable):
                    continue
                pair = f"{other.prospect_id}:{candidate.prospect_id}"
                if pair not in seen:
                    seen.add(pair)
                    found.append((other, candidate, reason))
        return found

    # -- mutation ----------------------------------------------------------

    def next_id(self) -> str:
        numbers = [
            int(p.prospect_id[1:])
            for p in self.prospects
            if p.prospect_id[:1].upper() == "P" and p.prospect_id[1:].isdigit()
        ]
        return f"P{(max(numbers) + 1) if numbers else 1:03d}"

    def add(self, prospect: Prospect) -> Prospect:
        """Add a prospect, refusing duplicates and anyone suppressed."""
        blocked = self.suppressions.blocks(prospect)
        if blocked:
            raise SuppressedProspectError(
                f"Refusing to add {prospect.company_name}: {blocked}. "
                "Someone on the suppression list is never re-imported."
            )
        duplicate = self.duplicate_of(prospect)
        if duplicate is not None:
            existing, how = duplicate
            raise DuplicateProspectError(
                f"{prospect.company_name} already on the list as "
                f"{existing.prospect_id} ({existing.company_name}), matched on {how}."
            )
        if not prospect.prospect_id:
            prospect.prospect_id = self.next_id()
        if prospect.date_added is None:
            prospect.date_added = today()
        self.prospects.append(prospect)
        return prospect

    def set_status(
        self,
        prospect: Prospect,
        target: ProspectStatus,
        when: date | None = None,
        note: str | None = None,
    ) -> Prospect:
        """Move a prospect through the funnel, stamping the matching date.

        Refuses transitions the funnel does not allow, so the conversion
        numbers in ``business-status`` mean what they say.
        """
        if not prospect.status.contactable and target != prospect.status:
            raise SuppressedProspectError(
                f"{prospect.prospect_id} is {prospect.status.value} and cannot be moved. "
                "Opt-out and suppression are one-way."
            )
        check_transition(prospect.status, target)
        stamp = when or today()
        prospect.status = target
        if target == ProspectStatus.EMAIL_1_SENT:
            prospect.email_1_sent_date = prospect.email_1_sent_date or stamp
            prospect.follow_up_due_date = None
        elif target == ProspectStatus.SAMPLE_REQUESTED:
            prospect.sample_requested_date = prospect.sample_requested_date or stamp
        elif target == ProspectStatus.SAMPLE_SENT:
            prospect.sample_sent_date = prospect.sample_sent_date or stamp
        elif target == ProspectStatus.OFFER_SENT:
            prospect.offer_sent_date = prospect.offer_sent_date or stamp
        elif target == ProspectStatus.SUBSCRIBED:
            prospect.converted_date = prospect.converted_date or stamp
            prospect.follow_up_due_date = None
        elif target == ProspectStatus.OPTED_OUT:
            prospect.opted_out = True
            prospect.reply_state = ReplyState.OPT_OUT
            prospect.follow_up_due_date = None
            prospect.suppression_reason = note or prospect.suppression_reason or "opted out"
        elif target == ProspectStatus.SUPPRESSED:
            prospect.follow_up_due_date = None
            prospect.suppression_reason = note or prospect.suppression_reason or "suppressed"
        if note:
            prospect.notes = f"{prospect.notes} | {note}".strip(" |") if prospect.notes else note
        return prospect

    def opt_out(self, prospect: Prospect, reason: str = "requested no further contact") -> Prospect:
        """Record an opt-out on both the prospect row and the suppression list.

        Both, always: the row can be edited or deleted by hand, the suppression
        list is what actually stops a future import.
        """
        prospect.opted_out = True
        prospect.reply_state = ReplyState.OPT_OUT
        prospect.status = ProspectStatus.OPTED_OUT
        prospect.suppression_reason = reason
        prospect.follow_up_due_date = None
        stamp = today().isoformat()
        if prospect.generic_contact_email:
            self.suppressions.add(
                SuppressionEntry(
                    value=prospect.generic_contact_email,
                    kind="email",
                    company_name=prospect.company_name,
                    date_added=stamp,
                    reason=reason,
                    added_by="prospect_optout",
                )
            )
        if prospect.domain:
            self.suppressions.add(
                SuppressionEntry(
                    value=prospect.domain,
                    kind="domain",
                    company_name=prospect.company_name,
                    date_added=stamp,
                    reason=reason,
                    added_by="prospect_optout",
                )
            )
        self.suppressions.add(
            SuppressionEntry(
                value=prospect.company_name,
                kind="company",
                company_name=prospect.company_name,
                date_added=stamp,
                reason=reason,
                added_by="prospect_optout",
            )
        )
        log.info("prospect.opted_out", prospect_id=prospect.prospect_id)
        return prospect

    # -- persistence -------------------------------------------------------

    def save(self) -> Path:
        save_prospects(self.prospects, self.path)
        save_suppressions(self.suppressions, SUPPRESSIONS_CSV)
        return self.path


def _same_business(a: Prospect, b: Prospect) -> str | None:
    def number(prospect: Prospect) -> str:
        return prospect.companies_house_number.strip().lstrip("0").upper()

    if a.companies_house_number and b.companies_house_number and number(a) == number(b):
        return "company number"
    if a.domain and a.domain == b.domain:
        return "website domain"
    if a.company_key and a.company_key == b.company_key:
        return "company name"
    if a.generic_contact_email and (
        a.generic_contact_email.strip().lower() == b.generic_contact_email.strip().lower()
    ):
        return "contact email"
    return None


def load_prospects(path: Path | None = None) -> list[Prospect]:
    path = path or PROSPECTS_CSV
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [_row_to_prospect(row) for row in csv.DictReader(fh)]


def save_prospects(prospects: list[Prospect], path: Path | None = None) -> Path:
    path = path or PROSPECTS_CSV
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for prospect in prospects:
            writer.writerow(_prospect_to_row(prospect))
    log.info("prospects.saved", count=len(prospects), path=str(path))
    return path


def load_store(path: Path | None = None, suppressions_path: Path | None = None) -> ProspectStore:
    path = path or PROSPECTS_CSV
    return ProspectStore(
        prospects=load_prospects(path),
        path=path,
        suppressions=load_suppressions(suppressions_path),
    )


__all__ = [
    "CSV_COLUMNS",
    "DuplicateProspectError",
    "PROSPECTS_CSV",
    "ProspectStore",
    "SUPPRESSIONS_CSV",
    "SuppressedProspectError",
    "SuppressionEntry",
    "SuppressionList",
    "TransitionError",
    "load_prospects",
    "load_store",
    "load_suppressions",
    "save_prospects",
    "save_suppressions",
]
