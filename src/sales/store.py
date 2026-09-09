"""Reading and writing LaunchTrace's own prospect list.

The list has two halves, kept in two different places on purpose.

**The researched seed list** — ``outreach/prospects_seed.csv`` — is who these
60 companies are, what they supply and why LaunchTrace suits them. That is
reusable research, it is worth reviewing in a diff, and it contains no personal
data, so it stays in git. Nothing in this module ever writes to it.

**The live outreach state** lives in the application database, in
``prospect_state`` and ``prospect_suppressions``. Verified contact addresses,
named contacts, reply notes, the dates things were sent, opt-outs and the
current funnel position are all personal data about identifiable people. They
have a retention period, an opt-out has to be honoured permanently, and a
commit history makes deletion effectively impossible. They do not belong in
git, so they are not there.

Two safety properties matter more than anything else here, and both survived
the move:

* **A suppressed or opted-out prospect can never be silently revived.** Import
  and update paths both refuse, and the suppression table is insert-only —
  there is no code path in this module that deletes from it.
* **The same business cannot enter the list twice**, whether by name, domain,
  company number or contact address, because contacting someone twice is the
  fastest way to lose them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.engine import get_session, init_db
from src.db.tables import ProspectStateRow, ProspectSuppression
from src.logging_setup import get_logger
from src.sales.icp import apply_scores
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
PROSPECTS_SEED_CSV = OUTREACH_DIR / "prospects_seed.csv"

# The research columns, and only the research columns. If a column here ever
# starts holding something that came back from a real person, it is in the
# wrong file.
SEED_COLUMNS = [
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
    "company_size_hint",
    "researched_date",
    "research_exclusion",
    "research_notes",
]

# The live columns, held per prospect in ``prospect_state``.
STATE_COLUMNS = [
    "generic_contact_email",
    "named_contact",
    "decision_maker_role",
    "email_source",
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

_STATE_DATE_FIELDS = [
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

    Held in its own table so that deleting a prospect row can never delete the
    record of their opt-out.
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
# the seed list (git, read-only)
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _seed_row_to_prospect(row: dict[str, str]) -> Prospect:
    """One research row, as a Prospect with no outreach history on it yet.

    The only status the seed file can express is "research says do not contact
    this one" — ``research_exclusion``, used for a duplicate or a business that
    turned out not to fit. Every other funnel position is something that
    happened to a person, and that is read from the database, not from here.
    """

    def get(name: str) -> str:
        return (row.get(name) or "").strip()

    exclusion = get("research_exclusion")
    return Prospect(
        prospect_id=get("prospect_id"),
        company_name=get("company_name"),
        website=get("website"),
        companies_house_number=get("companies_house_number"),
        company_type=get("company_type") or "unknown",
        supplier_category=get("supplier_category") or "other",
        supplier_subcategory=get("supplier_subcategory"),
        geography=get("geography"),
        icp_reason=get("icp_reason"),
        products_services=get("products_services"),
        buying_intent_categories=get("buying_intent_categories"),  # type: ignore[arg-type]
        contact_route=get("contact_route") or "unknown",
        company_size_hint=get("company_size_hint"),
        status=ProspectStatus.SUPPRESSED if exclusion else ProspectStatus.RESEARCHED,
        date_added=_parse_date(get("researched_date")),
        suppression_reason=exclusion,
        notes=get("research_notes"),
    )


def _prospect_to_seed_row(prospect: Prospect) -> dict[str, str]:
    """The research half of a prospect, and nothing else.

    This is the one function that decides what may reach git. It writes the
    columns in ``SEED_COLUMNS`` and reads nothing operational, so no contact
    address, reply, date or funnel position can leak into the file even by
    accident.
    """
    return {
        "prospect_id": prospect.prospect_id,
        "company_name": prospect.company_name,
        "website": prospect.website,
        "companies_house_number": prospect.companies_house_number,
        "company_type": prospect.company_type,
        "supplier_category": prospect.supplier_category,
        "supplier_subcategory": prospect.supplier_subcategory,
        "geography": prospect.geography,
        "icp_reason": prospect.icp_reason,
        "products_services": prospect.products_services,
        "buying_intent_categories": "|".join(prospect.buying_intent_categories),
        "contact_route": prospect.contact_route,
        "company_size_hint": prospect.company_size_hint,
        "researched_date": (prospect.date_added or today()).isoformat(),
        "research_exclusion": "",
        "research_notes": prospect.notes,
    }


def append_seed_rows(prospects: list[Prospect], path: Path | None = None) -> int:
    """Append research rows for prospects the seed file does not have yet.

    Append-only. An existing row is never rewritten, so a research file that
    has been corrected by hand stays corrected, and no operational value can
    overwrite a research one.
    """
    path = path or PROSPECTS_SEED_CSV
    known = {p.prospect_id for p in load_seed(path)}
    missing = [p for p in prospects if p.prospect_id and p.prospect_id not in known]
    if not missing:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SEED_COLUMNS)
        if new_file:
            writer.writeheader()
        for prospect in missing:
            writer.writerow(_prospect_to_seed_row(prospect))
    log.info("prospects.seed_appended", count=len(missing), path=str(path))
    return len(missing)


def load_seed(path: Path | None = None) -> list[Prospect]:
    """The researched list as shipped in the repository."""
    path = path or PROSPECTS_SEED_CSV
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [_seed_row_to_prospect(row) for row in csv.DictReader(fh)]


# ---------------------------------------------------------------------------
# the live state (database)
# ---------------------------------------------------------------------------


def _state_rows(session: Session) -> dict[str, ProspectStateRow]:
    rows = session.execute(select(ProspectStateRow)).scalars().all()
    return {row.prospect_id: row for row in rows}


def _apply_state(prospect: Prospect, row: ProspectStateRow) -> Prospect:
    """Overlay the live state onto a seed prospect."""
    prospect.generic_contact_email = row.generic_contact_email or ""
    prospect.named_contact = row.named_contact or ""
    prospect.decision_maker_role = row.decision_maker_role or ""
    prospect.email_source = EmailSource(row.email_source or EmailSource.NONE.value)
    prospect.status = ProspectStatus(row.status or ProspectStatus.RESEARCHED.value)
    prospect.priority = Priority(row.priority or Priority.C.value)
    prospect.icp_score = int(row.icp_score or 0)
    for name in _STATE_DATE_FIELDS:
        setattr(prospect, name, getattr(row, name))
    prospect.stripe_customer_id = row.stripe_customer_id or ""
    prospect.reply_state = ReplyState(row.reply_state or ReplyState.NONE.value)
    prospect.opted_out = bool(row.opted_out)
    prospect.suppression_reason = row.suppression_reason or ""
    prospect.notes = row.notes or ""
    return prospect


def _write_state(session: Session, prospect: Prospect) -> ProspectStateRow:
    row = session.execute(
        select(ProspectStateRow).where(ProspectStateRow.prospect_id == prospect.prospect_id)
    ).scalar_one_or_none()
    if row is None:
        row = ProspectStateRow(prospect_id=prospect.prospect_id)
        session.add(row)
    for name in STATE_COLUMNS:
        value = getattr(prospect, name)
        setattr(row, name, value.value if hasattr(value, "value") else value)
    row.updated_at = datetime.now(UTC)
    return row


def load_suppressions(session: Session) -> SuppressionList:
    rows = session.execute(select(ProspectSuppression)).scalars().all()
    return SuppressionList(
        entries=[
            SuppressionEntry(
                value=row.value,
                kind=row.kind or "email",
                company_name=row.company_name or "",
                date_added=row.date_added or "",
                reason=row.reason or "",
                added_by=row.added_by or "operator",
            )
            for row in rows
        ]
    )


def save_suppressions(session: Session, suppressions: SuppressionList) -> int:
    """Insert-only. Returns how many entries were new.

    Nothing here deletes or updates an existing row, so a suppression list can
    only ever grow. That is the whole point of it.
    """
    existing = {
        (row.kind, SuppressionEntry(row.value, row.kind).key)
        for row in session.execute(select(ProspectSuppression)).scalars()
    }
    added = 0
    for entry in suppressions.entries:
        if (entry.kind, entry.key) in existing:
            continue
        session.add(
            ProspectSuppression(
                kind=entry.kind,
                value=entry.value,
                company_name=entry.company_name,
                date_added=entry.date_added or today().isoformat(),
                reason=entry.reason,
                added_by=entry.added_by or "operator",
            )
        )
        existing.add((entry.kind, entry.key))
        added += 1
    session.flush()
    return added


def sync_seed(session: Session, seed: list[Prospect] | None = None) -> int:
    """Give every researched company a state row, once. Returns how many were new.

    Idempotent and additive: a prospect that already has live state is left
    exactly as it is, so re-running this can never undo an opt-out or roll a
    funnel position backwards.

    New rows are scored on the way in, from ``config/icp_scoring.json``, so a
    fresh database starts with the same prioritisation the research implies
    without the derived score being checked into git.
    """
    seed = load_seed() if seed is None else seed
    known = set(_state_rows(session).keys())
    fresh = [p for p in seed if p.prospect_id not in known]
    if fresh:
        apply_scores(fresh)
    created = 0
    for prospect in fresh:
        _write_state(session, prospect)
        created += 1
    if created:
        session.flush()
        log.info("prospects.seeded", created=created)
    return created


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------


class ProspectStore:
    """The prospect list: research from git, live state from the database."""

    def __init__(
        self,
        prospects: list[Prospect],
        session: Session,
        suppressions: SuppressionList | None = None,
        seed_path: Path | None = None,
    ):
        self.prospects = prospects
        self.session = session
        self.suppressions = suppressions if suppressions is not None else SuppressionList()
        self.seed_path = seed_path or PROSPECTS_SEED_CSV

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

        Both, always: the state row is about one prospect, the suppression list
        is what actually stops a future import under a different spelling.
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

    def save(self) -> int:
        """Write the live state to the database, and only that.

        A prospect added since the seed file was written also gets its research
        appended there, so the roster stays reviewable in git. Nothing
        operational is written to a file, and no existing research row is
        rewritten.
        """
        for prospect in self.prospects:
            _write_state(self.session, prospect)
        save_suppressions(self.session, self.suppressions)
        self.session.commit()
        append_seed_rows(self.prospects, self.seed_path)
        log.info("prospects.saved", count=len(self.prospects))
        return len(self.prospects)


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


def load_prospects(session: Session, seed_path: Path | None = None) -> list[Prospect]:
    """The seed list with each prospect's live state overlaid onto it."""
    seed = load_seed(seed_path)
    sync_seed(session, seed)
    state = _state_rows(session)
    prospects = [
        _apply_state(prospect, state[prospect.prospect_id])
        for prospect in seed
        if prospect.prospect_id in state
    ]
    seeded = {p.prospect_id for p in seed}
    for prospect_id in sorted(set(state) - seeded):
        # State with no research row: someone removed a row from the seed file
        # by hand. Say so — the state, including any opt-out, is still there.
        log.warning("prospects.state_without_research_row", prospect_id=prospect_id)
    return prospects


def load_store(session: Session | None = None, seed_path: Path | None = None) -> ProspectStore:
    """Open the prospect list for reading and writing.

    With no session, the configured application database is used and the schema
    is created if it is not there yet, so the first ``prospects`` command on a
    fresh machine works without a setup step.
    """
    if session is None:
        init_db()
        session = get_session()
    return ProspectStore(
        prospects=load_prospects(session, seed_path),
        session=session,
        suppressions=load_suppressions(session),
        seed_path=seed_path,
    )


__all__ = [
    "DuplicateProspectError",
    "PROSPECTS_SEED_CSV",
    "ProspectStore",
    "SEED_COLUMNS",
    "STATE_COLUMNS",
    "SuppressedProspectError",
    "SuppressionEntry",
    "SuppressionList",
    "TransitionError",
    "load_prospects",
    "append_seed_rows",
    "load_seed",
    "load_store",
    "load_suppressions",
    "save_suppressions",
    "sync_seed",
]
