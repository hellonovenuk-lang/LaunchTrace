#!/usr/bin/env python3
"""Generate the customer-facing weekly Excel workbook for one prospect.

    python3 scripts/generate_customer_workbook.py \
        --prospect P009 \
        --opportunities reports/validation/final/2026-037/opportunities.csv \
        --out reports/customer_test/2026-037

The selection is the existing supplier match, used exactly as it comes back
from ``src.sales.matching``. This script picks no leads of its own, changes no
score, and re-ranks nothing — it only decides how the result is written down.

Nothing is sent. The workbook is written to disk and that is the end of it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.deliver.customer_narrative import build_report  # noqa: E402
from src.deliver.customer_workbook import write_workbook  # noqa: E402
from src.sales.leads import leads_from_csv  # noqa: E402
from src.sales.matching import select_for_prospect  # noqa: E402
from src.sales.store import load_seed  # noqa: E402
from src.settings import load_config  # noqa: E402


# A run record carries the registered office town, which is a more useful
# "Location" for a salesperson than the county the CSV keeps. It is read only
# when the file is there; the county is a fine fallback.
def _post_towns(result_json: Path | None) -> dict[str, str]:
    if not result_json or not result_json.exists():
        return {}
    with result_json.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    towns: dict[str, str] = {}
    for opportunity in payload.get("opportunities", []):
        company = opportunity.get("company") or {}
        number = (company.get("company_number") or "").strip()
        town = (company.get("post_town") or "").strip()
        if number and town:
            towns[number] = town
    return towns


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text) or "Customer"


def _approved_marks(journal: str, prospect_id: str) -> list[str]:
    """The reviewed selection for this week and customer, if one exists.

    Weeks nobody has looked at return nothing, and the generator falls back to
    the supplier-specific fit order. Either way the leads come from the
    matcher; this only decides which of them the brief leads with.
    """
    try:
        config = load_config("customer_highlights.json")
    except FileNotFoundError:
        return []
    for selection in config.get("selections", []):
        if selection.get("journal") == journal and selection.get("prospect_id") == prospect_id:
            return [str(mark) for mark in selection.get("trademarks", [])]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prospect", default="P009", help="Prospect ID from the seed list")
    parser.add_argument(
        "--opportunities",
        default="reports/validation/final/2026-037/opportunities.csv",
        help="Delivered opportunities CSV to build the workbook from",
    )
    parser.add_argument(
        "--out",
        default="reports/customer_test/2026-037",
        help="Directory to write the workbook into",
    )
    parser.add_argument("--journal", default="", help="Journal number, e.g. 2026-037")
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="How many matches to include. The default takes everything that qualifies.",
    )
    parser.add_argument(
        "--highlights", type=int, default=4, help="How many opportunities the brief features"
    )
    parser.add_argument(
        "--highlight",
        default="",
        help=(
            "Comma-separated trade mark numbers to lead the brief with, overriding "
            "config/customer_highlights.json. They must already be qualified matches; "
            "anything else is skipped with a warning."
        ),
    )
    args = parser.parse_args()

    csv_path = (REPO_ROOT / args.opportunities).resolve()
    journal = args.journal or csv_path.parent.name

    prospect = next((p for p in load_seed() if p.prospect_id == args.prospect), None)
    if prospect is None:
        parser.error(f"No prospect {args.prospect} in the seed list")

    leads = leads_from_csv(csv_path)
    result = select_for_prospect(prospect, leads, source=str(csv_path), count=args.count)
    if not result.matches:
        print(f"Nothing in {csv_path} suits {prospect.company_name}. No workbook written.")
        return 1

    approved = (
        [mark.strip() for mark in args.highlight.split(",") if mark.strip()]
        if args.highlight
        else _approved_marks(journal, prospect.prospect_id)
    )

    config = load_config("customer_report.json")
    report = build_report(
        result,
        config=config,
        towns=_post_towns(csv_path.parent / "result.json"),
        highlight_count=args.highlights,
        approved_marks=approved,
    )
    report.journal_number = journal

    out_dir = (REPO_ROOT / args.out).resolve()
    filename = f"LaunchTrace_Food_{_slug(prospect.company_name)}_{journal}.xlsx"
    path = write_workbook(report, out_dir / filename, config=config)

    print(f"{prospect.company_name}: {report.total} opportunities, {report.top_count} top priority")
    print(f"Top matches from: {'approved selection' if approved else 'supplier fit order'}")
    for warning in report.warnings:
        print(f"  ! {warning}")
    for row in report.rows:
        print(f"  {row.priority:<10} {row.brand}")
    print(f"Brief cards: {', '.join(row.brand for row in report.highlights)}")
    print(f"Written: {path}")
    print("Nothing was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
