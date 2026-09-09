"""LaunchTrace commercial operations command line.

The companion to ``python -m src.pipeline``:

* ``src.pipeline`` runs the **product** — ingest, score, deliver the feed.
* ``src.admin`` runs the **business** — prospects, outreach, samples,
  customers, feedback and money.

    python -m src.admin prospects ready
    python -m src.admin prospect-preview --prospect-id P001 --draft-email
    python -m src.admin outreach-due
    python -m src.admin prepare-sample --prospect-id P001
    python -m src.admin business-status

Run ``python -m src.admin --help`` for everything.

**Nothing in this CLI sends an email to a prospect.** Drafts are written to
disk for you to send yourself. That is a deliberate design decision, not a
missing feature.
"""

from __future__ import annotations

import argparse
import sys

from src.logging_setup import configure_logging
from src.sales.feedback import FeedbackState
from src.sales.models import SUPPLIER_CATEGORIES, Priority, ProspectStatus
from src.settings import get_settings

EPILOG = """\
Nothing here contacts anyone. Every outreach command writes a draft you send
by hand from your own mailbox.

Start here:  python -m src.admin business-status
Then read:   FIRST_CUSTOMER_PLAYBOOK.md
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.admin",
        description=(
            "LaunchTrace commercial operations — prospects, outreach, samples, "
            "customers, feedback and cost. The product pipeline lives in "
            "python -m src.pipeline."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- prospects ---------------------------------------------------------
    prospects = sub.add_parser(
        "prospects",
        help="The supplier prospect list: view, prioritise, audit and move through the funnel",
    )
    prospect_actions = prospects.add_subparsers(dest="action", required=True)

    listing = prospect_actions.add_parser("list", help="List prospects, best fit first")
    listing.add_argument("--priority", choices=[p.value for p in Priority])
    listing.add_argument("--status", choices=[s.value for s in ProspectStatus])
    listing.add_argument("--category", choices=SUPPLIER_CATEGORIES)
    listing.add_argument("--limit", type=int, default=200)

    prospect_actions.add_parser(
        "ready", help="Who is ready for a first email right now, best fit first"
    )

    show = prospect_actions.add_parser(
        "show", help="Everything known about one prospect, including why it scored as it did"
    )
    show.add_argument("--prospect-id", required=True)

    set_status = prospect_actions.add_parser(
        "set-status", help="Move a prospect through the funnel after you have acted"
    )
    set_status.add_argument("--prospect-id", required=True)
    set_status.add_argument("--status", required=True, choices=[s.value for s in ProspectStatus])
    set_status.add_argument("--date", help="When it happened (YYYY-MM-DD), default today")
    set_status.add_argument("--note", help="Anything worth recording, e.g. what they said")

    prospect_actions.add_parser(
        "rescore", help="Re-run ICP scoring and reassign Priority A/B/C after editing the config"
    )
    prospect_actions.add_parser(
        "audit", help="Duplicates, missing research and anything else worth fixing before sending"
    )

    opt_out = prospect_actions.add_parser(
        "opt-out", help="Record an opt-out. One-way, and adds them to the suppression list"
    )
    opt_out.add_argument("--prospect-id", required=True)
    opt_out.add_argument("--reason", help="What they said, in their words if possible")

    # -- preview and outreach ---------------------------------------------
    preview = sub.add_parser(
        "prospect-preview",
        help="Pick the strongest genuinely relevant opportunities for one prospect",
        description=(
            "Selects opportunities that suit this specific supplier. Returns fewer than "
            "asked for if fewer genuinely fit — padding the list with weak leads is the "
            "one thing that makes the first email worse."
        ),
    )
    preview.add_argument("--prospect-id", required=True)
    preview.add_argument("--count", type=int, default=3, help="How many to select (default 3)")
    preview.add_argument("--from-csv", help="Read opportunities from a CSV instead of the database")
    preview.add_argument("--journal", help="Restrict to one journal number")
    preview.add_argument("--csv", action="store_true", help="Also write the preview as a CSV")
    preview.add_argument(
        "--draft-email", action="store_true", help="Also draft Email 1 with the leads inserted"
    )
    preview.add_argument("--out", help="Output directory (default reports/previews)")

    due = sub.add_parser(
        "outreach-due", help="Who is due which sales action today, in the order to work through"
    )
    due.add_argument(
        "--no-commands", dest="commands", action="store_false", help="Hide the suggested commands"
    )

    draft = sub.add_parser("outreach-draft", help="Draft one outreach email for one prospect")
    draft.add_argument("--prospect-id", required=True)
    draft.add_argument("--template", default="email_1", choices=["email_1", "email_2", "email_3"])
    draft.add_argument("--from-csv", help="Read opportunities from a CSV instead of the database")

    # -- samples -----------------------------------------------------------
    prepare = sub.add_parser(
        "prepare-sample",
        help="Build the full customer sample for one prospect, with a cover email",
    )
    prepare.add_argument("--prospect-id", required=True)
    prepare.add_argument("--from-csv", help="Read opportunities from a CSV instead of the database")
    prepare.add_argument("--journal", help="Restrict to one journal number")
    prepare.add_argument("--out", help="Output directory")
    prepare.add_argument(
        "--no-watermark", action="store_true", help="Drop the SAMPLE banner (for a real feed)"
    )

    build = sub.add_parser("build-sample", help="Build a sample with no prospect attached")
    build.add_argument("--from-csv")
    build.add_argument("--journal")
    build.add_argument("--out")
    build.add_argument("--no-watermark", action="store_true")

    # -- customers ---------------------------------------------------------
    status = sub.add_parser(
        "customer-status", help="Subscription, delivery and message state for a customer"
    )
    status.add_argument("--customer-id", help="Omit to show every customer")

    lifecycle = sub.add_parser(
        "customer-lifecycle",
        help="Run an onboarding, payment-failure or cancellation transition by hand",
        description=(
            "Stripe drives these automatically once connected. Use this for a customer "
            "you added manually, or to see exactly what each transition produces."
        ),
    )
    lifecycle.add_argument("--customer-id", required=True)
    lifecycle.add_argument("--event", required=True, choices=["start", "payment-failed", "cancel"])
    lifecycle.add_argument("--reference", help="Invoice id, so a repeat failure is a new message")

    # -- feedback ----------------------------------------------------------
    feedback = sub.add_parser(
        "feedback", help="Record and summarise what customers said about delivered leads"
    )
    feedback_actions = feedback.add_subparsers(dest="action", required=True)

    add = feedback_actions.add_parser("add", help="Record one piece of feedback")
    add.add_argument("--state", required=True, choices=[s.value for s in FeedbackState])
    add.add_argument("--trademark", help="Trade mark number from the CSV")
    add.add_argument("--dedupe-key")
    add.add_argument("--customer-id")
    add.add_argument("--note", help="What they actually said")

    imported = feedback_actions.add_parser("import", help="Import feedback from a CSV")
    imported.add_argument("--path", required=True)

    feedback_actions.add_parser("summary", help="Recurring patterns across all feedback")

    # -- business ----------------------------------------------------------
    sub.add_parser(
        "business-status", help="The whole picture: funnel, product, operations, cost and margin"
    )

    costs = sub.add_parser("costs", help="Estimated cost per run, per customer, and gross margin")
    costs.add_argument(
        "--customers", type=int, help="Model a customer count instead of the real one"
    )

    backup = sub.add_parser(
        "backup", help="Copy prospects, suppressions, customers, feedback and config somewhere safe"
    )
    backup.add_argument("--out", help="Destination directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    args = build_parser().parse_args(argv)

    from src.sales_commands import (
        cmd_backup,
        cmd_build_sample,
        cmd_business_status,
        cmd_costs,
        cmd_customer_lifecycle,
        cmd_customer_status,
        cmd_feedback,
        cmd_outreach_draft,
        cmd_outreach_due,
        cmd_prepare_sample,
        cmd_prospect_preview,
        cmd_prospects,
    )

    handlers = {
        "prospects": cmd_prospects,
        "prospect-preview": cmd_prospect_preview,
        "outreach-due": cmd_outreach_due,
        "outreach-draft": cmd_outreach_draft,
        "prepare-sample": cmd_prepare_sample,
        "build-sample": cmd_build_sample,
        "customer-status": cmd_customer_status,
        "customer-lifecycle": cmd_customer_lifecycle,
        "feedback": cmd_feedback,
        "business-status": cmd_business_status,
        "costs": cmd_costs,
        "backup": cmd_backup,
    }
    try:
        return int(handlers[args.command](args) or 0)
    except KeyError as exc:
        print(str(exc).strip('"'), file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
