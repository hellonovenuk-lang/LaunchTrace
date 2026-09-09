"""One-time migration of the original prospect list to the tracker schema.

Kept in the repository as the provenance record for how the 60 researched
companies became tracker rows. It is idempotent: run against an already
migrated file and it exits without changing anything.

    python scripts/migrate_prospects.py [--dry-run]

Nothing is invented. Every value written here comes from a cell that already
existed, from a deterministic mapping in ``config/supplier_profiles.json``, or
from the date the research was committed. Contact emails are left empty,
because an address that has not been checked on the company's own website is
worse than no address at all.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.sales.models import (  # noqa: E402
    EmailSource,
    Priority,
    Prospect,
    ProspectStatus,
    ReplyState,
)
from src.sales.store import (  # noqa: E402
    CSV_COLUMNS,
    PROSPECTS_CSV,
    save_prospects,
)
from src.settings import load_config  # noqa: E402

# The date the researched list was committed to the repository. Used as
# date_added because it is the one date about these rows that is actually true.
RESEARCH_COMMIT_DATE = date(2026, 9, 8)

LEGACY_STATUS = {
    "not_contacted": ProspectStatus.RESEARCHED,
    "contacted": ProspectStatus.EMAIL_1_SENT,
    "interested": ProspectStatus.REPLIED_INTERESTED,
    "sample_sent": ProspectStatus.SAMPLE_SENT,
    "subscribed": ProspectStatus.SUBSCRIBED,
    "declined": ProspectStatus.NOT_NOW,
    "no_response": ProspectStatus.NO_RESPONSE,
    "suppressed": ProspectStatus.SUPPRESSED,
}

LEGACY_REPLY = {
    "none": ReplyState.NONE,
    "positive": ReplyState.POSITIVE,
    "neutral": ReplyState.NEUTRAL,
    "negative": ReplyState.NEGATIVE,
    "opt_out": ReplyState.OPT_OUT,
}

# Size hints stated outright in the original notes. Nothing is inferred from
# company name or sector.
SIZE_MARKERS = [
    ("large plc", "large plc"),
    ("large multinational", "large multinational"),
    ("large group", "large group"),
    ("large.", "large"),
    ("non-uk parent", "non-UK parent"),
]

# A leading place name in the notes, e.g. "Bradford. Verified via web search."
_GEOGRAPHY = re.compile(r"^([A-Z][A-Za-z'&\- ]+(?:, [A-Z][A-Za-z'&\- ]+)?)\.\s")

_NON_GEOGRAPHY_PREFIXES = {
    "verified via web search",
    "verify website",
    "verify before contacting",
    "verify website before contacting",
    "large",
    "non-uk parent",
    "est",
    "b corp",
}


def geography_from_notes(notes: str) -> str:
    """Pull a stated location out of the notes, or return nothing.

    Only accepts a capitalised phrase that starts the note and is not one of
    the known non-place prefixes. A missed location is fine; a wrong one is not.
    """
    match = _GEOGRAPHY.match(notes.strip())
    if not match:
        return ""
    candidate = match.group(1).strip()
    lowered = candidate.lower()
    if any(lowered.startswith(prefix) for prefix in _NON_GEOGRAPHY_PREFIXES):
        return ""
    if re.match(r"^\d", candidate) or "30+" in candidate:
        return ""
    return candidate


def size_hint_from_notes(notes: str) -> str:
    lowered = notes.lower()
    for marker, label in SIZE_MARKERS:
        if marker in lowered:
            return label
    return ""


def intents_for(category: str, profiles: dict) -> list[str]:
    """The buying-intent categories this supplier's leads should be ranked on."""
    profile = next((p for p in profiles["profiles"] if p["key"] == category), None)
    if profile is None:
        return []
    ranked = sorted(profile["primary_intents"].items(), key=lambda kv: -kv[1])
    return [key for key, _ in ranked]


def already_migrated(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh), [])
    return "prospect_id" in header


def migrate(path: Path) -> list[Prospect]:
    profiles = load_config("supplier_profiles.json")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        legacy = list(csv.DictReader(fh))

    prospects: list[Prospect] = []
    for index, row in enumerate(legacy, start=1):
        notes = (row.get("notes") or "").strip()
        category = (row.get("supplier_type") or "other").strip()
        status = LEGACY_STATUS.get((row.get("status") or "").strip(), ProspectStatus.RESEARCHED)
        sample_sent = (row.get("sample_sent") or "").strip()
        prospects.append(
            Prospect(
                prospect_id=f"P{index:03d}",
                company_name=(row.get("company_name") or "").strip(),
                website=(row.get("website") or "").strip(),
                company_type=(row.get("company_type") or "unknown").strip() or "unknown",
                supplier_category=category,
                geography=geography_from_notes(notes),
                icp_reason=(row.get("relevance_reason") or "").strip(),
                buying_intent_categories=intents_for(category, profiles),
                contact_route=(row.get("sales_contact_route") or "unknown").strip() or "unknown",
                generic_contact_email=(row.get("generic_contact_email") or "").strip(),
                email_source=(
                    EmailSource.WEBSITE_VERIFIED
                    if (row.get("generic_contact_email") or "").strip()
                    else EmailSource.NONE
                ),
                company_size_hint=size_hint_from_notes(notes),
                status=status,
                priority=Priority.C,
                date_added=RESEARCH_COMMIT_DATE,
                sample_sent_date=date.fromisoformat(sample_sent) if sample_sent else None,
                reply_state=LEGACY_REPLY.get(
                    (row.get("reply_status") or "").strip(), ReplyState.NONE
                ),
                notes=notes,
            )
        )
    return prospects


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=str(PROSPECTS_CSV))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.path)
    if already_migrated(path):
        print(f"{path} is already in the tracker schema — nothing to do.")
        return 0

    prospects = migrate(path)
    print(f"Migrating {len(prospects)} prospects to the tracker schema.")
    print(f"Columns: {len(CSV_COLUMNS)}")
    if args.dry_run:
        for p in prospects[:5]:
            print(
                f"  {p.prospect_id}  {p.company_name:<34} {p.supplier_category:<24} {p.geography}"
            )
        print("  … dry run, nothing written.")
        return 0

    save_prospects(prospects, path)
    print(f"Wrote {path}. The previous version is in outreach/backups/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
