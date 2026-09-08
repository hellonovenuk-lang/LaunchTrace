"""LaunchTrace command line.

    python -m src.pipeline weekly
    python -m src.pipeline backfill --weeks 4
    python -m src.pipeline smoke-test

Run ``python -m src.pipeline --help`` for the full list.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from src.logging_setup import configure_logging, get_logger
from src.settings import get_settings

log = get_logger(__name__)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", help="Journal source: ukipo_http | open_data | local | fixture")
    parser.add_argument("--journal", help="Explicit journal number, e.g. 2025-052")
    parser.add_argument("--date", help="Explicit publication date, YYYY-MM-DD")
    parser.add_argument("--max-records", type=int, help="Cap records parsed (testing)")
    parser.add_argument("--no-db", action="store_true", help="Do not write to the database")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline",
        description="LaunchTrace Food — weekly UK emerging food brand signals from UKIPO trade mark activity.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    weekly = sub.add_parser(
        "weekly", help="Process the latest journal and produce the weekly report"
    )
    _add_common(weekly)
    weekly.add_argument("--send", action="store_true", help="Attempt delivery after the run")

    backfill = sub.add_parser("backfill", help="Process several past journals")
    _add_common(backfill)
    backfill.add_argument("--weeks", type=int, default=4, help="How many journals (default 4)")

    smoke = sub.add_parser("smoke-test", help="Run the whole pipeline on fixture data")
    smoke.add_argument("--out", help="Output directory (default reports/smoke)")

    validate = sub.add_parser(
        "validate", help="Run the four-week historical validation and write reports/validation/"
    )
    validate.add_argument("--weeks", type=int, default=4)
    validate.add_argument("--source", default="open_data")

    sub.add_parser("status", help="Show recent pipeline runs")

    approve = sub.add_parser("approve", help="Approve a run for delivery (review mode)")
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--by", default="operator")

    send = sub.add_parser("send", help="Deliver an approved run to customers")
    send.add_argument("--run-id", required=True)
    send.add_argument("--force", action="store_true", help="Send even without approval")

    regen = sub.add_parser("regenerate-csv", help="Rebuild the CSV for a journal from stored data")
    regen.add_argument("--journal", required=True)

    show = sub.add_parser("opportunities", help="List opportunities for a journal")
    show.add_argument("--journal")
    show.add_argument("--band", choices=["HIGH", "MEDIUM", "SUPPRESS"])
    show.add_argument("--limit", type=int, default=25)

    sub.add_parser("customers", help="List customers and subscription state")

    add_customer = sub.add_parser("add-customer", help="Add a customer and recipients")
    add_customer.add_argument("--company", required=True)
    add_customer.add_argument("--email", required=True, nargs="+")
    add_customer.add_argument("--supplier-type")
    add_customer.add_argument("--plan", default="founding_monthly")
    add_customer.add_argument("--status", default="trialing")

    suppress = sub.add_parser("suppress", help="Suppress a company, applicant, mark or email")
    suppress.add_argument(
        "--type", required=True, choices=["company", "applicant", "mark", "email"]
    )
    suppress.add_argument("--value", required=True)
    suppress.add_argument("--reason")

    sub.add_parser("errors", help="Show recent pipeline errors")

    fetch_od = sub.add_parser(
        "fetch-open-data",
        help="Download the official IPO Open Data release and slice it into weeks",
    )
    fetch_od.add_argument("--weeks", type=int, default=4)

    index = sub.add_parser(
        "build-company-index", help="Build the free Companies House bulk index (no API key needed)"
    )
    index.add_argument("--source", help="Path to a downloaded BasicCompanyData zip/csv")
    index.add_argument("--download", action="store_true", help="Download the latest snapshot first")

    sub.add_parser("init-db", help="Create database tables")
    sub.add_parser("check-config", help="Report which integrations are connected")

    probe = sub.add_parser("probe-journal", help="Diagnose an unfamiliar journal XML file")
    probe.add_argument("path")

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    args = build_parser().parse_args(argv)

    from src.commands import (
        cmd_add_customer,
        cmd_approve,
        cmd_backfill,
        cmd_build_company_index,
        cmd_check_config,
        cmd_customers,
        cmd_errors,
        cmd_fetch_open_data,
        cmd_init_db,
        cmd_opportunities,
        cmd_probe_journal,
        cmd_regenerate_csv,
        cmd_send,
        cmd_smoke_test,
        cmd_status,
        cmd_suppress,
        cmd_validate,
        cmd_weekly,
    )

    handlers = {
        "weekly": cmd_weekly,
        "backfill": cmd_backfill,
        "smoke-test": cmd_smoke_test,
        "validate": cmd_validate,
        "status": cmd_status,
        "approve": cmd_approve,
        "send": cmd_send,
        "regenerate-csv": cmd_regenerate_csv,
        "opportunities": cmd_opportunities,
        "customers": cmd_customers,
        "add-customer": cmd_add_customer,
        "suppress": cmd_suppress,
        "errors": cmd_errors,
        "fetch-open-data": cmd_fetch_open_data,
        "build-company-index": cmd_build_company_index,
        "init-db": cmd_init_db,
        "check-config": cmd_check_config,
        "probe-journal": cmd_probe_journal,
    }
    handler = handlers[args.command]
    try:
        return int(handler(args) or 0)
    except KeyboardInterrupt:  # pragma: no cover
        print("Interrupted.", file=sys.stderr)
        return 130


def parse_date_arg(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def relative(path: str | Path | None) -> str:
    if not path:
        return "-"
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
