"""Command implementations for the commercial operator CLI.

Separate from ``src/commands.py`` for the same reason ``src/sales`` is separate
from the pipeline: running the business and running the product are different
jobs, and mixing their commands makes both harder to read.

Output is written for a non-technical operator. Every command that identifies
work to do also prints the command that does it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select

from src.customer_lifecycle import (
    PAST_DUE_GRACE_DAYS,
    delivery_allowed,
    delivery_block_reason,
    on_cancelled,
    on_payment_failed,
    on_subscription_started,
)
from src.db import init_db, session_scope
from src.db.tables import Customer, CustomerPreference, Delivery, LeadFeedback
from src.deliver.sample_pack import build_sample_pack
from src.logging_setup import get_logger
from src.sales.costs import estimate_costs, format_pounds
from src.sales.feedback import (
    STATE_MEANING,
    FeedbackState,
    import_feedback_csv,
    record_feedback,
    summarise_feedback,
)
from src.sales.icp import apply_scores, score_prospect
from src.sales.leads import load_leads
from src.sales.matching import select_for_prospect
from src.sales.metrics import business_status, unrated_lead_count
from src.sales.models import EmailSource, Prospect, ProspectStatus, today
from src.sales.outreach import (
    next_follow_up_date,
    outreach_due,
    render_draft,
    write_draft,
)
from src.sales.preview import render_email_block, render_markdown, write_preview_csv
from src.sales.store import (
    SuppressedProspectError,
    TransitionError,
    load_store,
)
from src.settings import REPORTS_DIR, get_settings

log = get_logger(__name__)

PREVIEW_DIR = REPORTS_DIR / "previews"
SAMPLE_DIR = REPORTS_DIR / "samples"


def _rule(title: str) -> None:
    print(f"\n{title}\n{'─' * max(len(title), 12)}")


def _prospect_line(prospect: Prospect) -> str:
    return (
        f"{prospect.prospect_id:<6} {prospect.priority.value:<9} {prospect.icp_score:>3}  "
        f"{prospect.company_name[:32]:<34}{prospect.supplier_category[:22]:<24}"
        f"{prospect.status.value}"
    )


# ---------------------------------------------------------------------------
# prospects
# ---------------------------------------------------------------------------


def cmd_prospects(args) -> int:  # type: ignore[no-untyped-def]
    action = getattr(args, "action", "list")
    handlers = {
        "list": _prospects_list,
        "ready": _prospects_ready,
        "show": _prospects_show,
        "set-status": _prospects_set_status,
        "set-contact": _prospects_set_contact,
        "rescore": _prospects_rescore,
        "audit": _prospects_audit,
        "opt-out": _prospects_opt_out,
    }
    return handlers[action](args)


def _prospects_list(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    rows = store.prospects
    if getattr(args, "priority", None):
        rows = [p for p in rows if p.priority.value == args.priority]
    if getattr(args, "status", None):
        rows = [p for p in rows if p.status.value == args.status]
    if getattr(args, "category", None):
        rows = [p for p in rows if p.supplier_category == args.category]
    rows = sorted(rows, key=lambda p: (p.priority.value, -p.icp_score, p.company_name))

    if not rows:
        print("No prospects match that filter.")
        return 0
    print(f"{'id':<6} {'priority':<9} {'icp':>3}  {'company':<34}{'category':<24}status")
    for prospect in rows[: getattr(args, "limit", 200)]:
        print(_prospect_line(prospect))
    print(f"\n{len(rows)} prospect(s).")
    return 0


def _prospects_ready(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    ready = [
        p
        for p in store.prospects
        if p.status == ProspectStatus.READY and p.contactable and not store.suppressions.blocks(p)
    ]
    ready.sort(key=lambda p: (p.priority.value, -p.icp_score))
    if not ready:
        print(
            "Nobody is marked READY yet.\n\n"
            "Prospects start as RESEARCHED. Check the contact route on their website, then:\n"
            "  python -m src.admin prospects set-status --prospect-id P001 --status READY"
        )
        return 0
    print(f"{'id':<6} {'priority':<9} {'icp':>3}  {'company':<34}{'category':<24}contact")
    for prospect in ready:
        contact = prospect.generic_contact_email or f"({prospect.contact_route})"
        print(
            f"{prospect.prospect_id:<6} {prospect.priority.value:<9} {prospect.icp_score:>3}  "
            f"{prospect.company_name[:32]:<34}{prospect.supplier_category[:22]:<24}{contact}"
        )
    print(f"\n{len(ready)} ready for first contact.")
    print("Next: python -m src.admin prospect-preview --prospect-id <id> --draft-email")
    return 0


def _prospects_show(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    prospect = store.require(args.prospect_id)
    result = score_prospect(prospect)

    _rule(f"{prospect.prospect_id} — {prospect.company_name}")
    fields = [
        ("Website", prospect.website),
        ("Supplier category", prospect.supplier_category),
        ("Subcategory", prospect.supplier_subcategory),
        ("Geography", prospect.geography),
        ("Company type", prospect.company_type),
        ("Companies House", prospect.companies_house_number),
        ("Why they fit", prospect.icp_reason),
        ("Products / services", prospect.products_services),
        ("Buying intent", ", ".join(prospect.buying_intent_categories)),
        ("Contact route", prospect.contact_route),
        ("Contact email", prospect.generic_contact_email or "(none verified)"),
        ("Email source", prospect.email_source.value),
        ("Named contact", prospect.named_contact),
        ("Decision-maker role", prospect.decision_maker_role),
        ("Status", prospect.status.value),
        ("Priority", f"{prospect.priority.value} (ICP score {prospect.icp_score})"),
        ("Reply state", prospect.reply_state.value),
        ("Opted out", "yes" if prospect.opted_out else "no"),
        ("Suppression reason", prospect.suppression_reason),
        ("Stripe customer", prospect.stripe_customer_id),
        ("Notes", prospect.notes),
    ]
    width = max(len(k) for k, _ in fields)
    for key, value in fields:
        if value:
            print(f"  {key:<{width}}  {value}")

    _rule("Dates")
    milestones: list[tuple[str, date | None]] = [
        ("Added", prospect.date_added),
        ("Email 1 sent", prospect.email_1_sent_date),
        ("Sample requested", prospect.sample_requested_date),
        ("Sample sent", prospect.sample_sent_date),
        ("Offer sent", prospect.offer_sent_date),
        ("Converted", prospect.converted_date),
        ("Follow-up due", prospect.follow_up_due_date),
    ]
    for label, when in milestones:
        print(f"  {label:<18}  {when.isoformat() if when else '—'}")

    _rule("ICP scoring")
    for line in result.explain():
        print(f"  {line}")
    print(f"\n  Total {result.score} → Priority {result.priority.value}")

    blocked = store.suppressions.blocks(prospect)
    if blocked or not prospect.contactable:
        print(f"\n  DO NOT CONTACT: {blocked or prospect.status.value}")
    return 0


def _prospects_set_status(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    prospect = store.require(args.prospect_id)
    target = ProspectStatus(args.status)
    when = date.fromisoformat(args.date) if getattr(args, "date", None) else today()
    try:
        store.set_status(prospect, target, when=when, note=getattr(args, "note", None))
    except Exception as exc:
        print(f"Cannot do that: {exc}")
        return 1
    follow_up = next_follow_up_date(target, when)
    if follow_up:
        prospect.follow_up_due_date = follow_up
    store.save()
    print(f"{prospect.prospect_id} ({prospect.company_name}) is now {target.value}.")
    if follow_up:
        print(f"Follow-up due {follow_up.isoformat()} — one follow-up only, then stop.")
    return 0


def _prospects_set_contact(args) -> int:  # type: ignore[no-untyped-def]
    """Record a contact address you have checked on the company's own website.

    The address and its source are personal data about a real business contact,
    so they are written to the database, never to a git-tracked file. There is
    deliberately no way to record an address without saying where it came from.
    """
    store = load_store()
    prospect = store.require(args.prospect_id)
    if not prospect.contactable:
        print(f"{prospect.prospect_id} is {prospect.status.value} and must not be contacted.")
        return 1

    email = (getattr(args, "email", "") or "").strip()
    if email:
        prospect.generic_contact_email = email
        prospect.email_source = EmailSource(args.source)
    if getattr(args, "named_contact", None):
        prospect.named_contact = args.named_contact.strip()
    if getattr(args, "role", None):
        prospect.decision_maker_role = args.role.strip()

    blocked = store.suppressions.blocks(prospect)
    if blocked:
        print(f"Refusing: {blocked}. Nothing was recorded.")
        return 1

    duplicate = store.duplicate_of(prospect)
    if duplicate is not None:
        existing, how = duplicate
        print(
            f"Refusing: that matches {existing.prospect_id} ({existing.company_name}) "
            f"on {how}. Contacting the same business twice is the fastest way to lose them."
        )
        return 1

    store.save()
    print(
        f"{prospect.prospect_id} ({prospect.company_name}): "
        f"{prospect.generic_contact_email or 'no address'} "
        f"[{prospect.email_source.value}]"
    )
    print("Stored in the database, not in git.")
    return 0


def _prospects_opt_out(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    prospect = store.require(args.prospect_id)
    store.opt_out(prospect, reason=getattr(args, "reason", None) or "requested no further contact")
    store.save()
    print(
        f"{prospect.prospect_id} ({prospect.company_name}) is opted out and added to the "
        "suppression list by email, domain and company name.\n"
        "They cannot be re-imported and will not appear in outreach-due again."
    )
    return 0


def _prospects_rescore(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    before = {p.prospect_id: p.priority.value for p in store.prospects}
    results = apply_scores(store.prospects)
    store.save()

    changed = [p for p in store.prospects if before.get(p.prospect_id) != p.priority.value]
    counts: dict[str, int] = {}
    for prospect in store.prospects:
        counts[prospect.priority.value] = counts.get(prospect.priority.value, 0) + 1

    print(f"Scored {len(results)} prospects.")
    for band in ("A", "B", "C", "SUPPRESS"):
        print(f"  Priority {band:<9} {counts.get(band, 0)}")
    if changed:
        print(f"\n{len(changed)} changed band:")
        for prospect in changed[:20]:
            print(
                f"  {prospect.prospect_id}  {prospect.company_name[:34]:<36}"
                f"{before.get(prospect.prospect_id)} → {prospect.priority.value}"
            )
    print("\nWeights live in config/icp_scoring.json. Edit and re-run to change the ordering.")
    return 0


def _prospects_audit(args) -> int:  # type: ignore[no-untyped-def]
    """Everything wrong with the list, in the order worth fixing."""
    store = load_store()
    problems = 0

    _rule("Duplicates")
    duplicates = store.duplicates()
    if duplicates:
        problems += len(duplicates)
        for first, second, how in duplicates:
            print(
                f"  {first.prospect_id} {first.company_name}  ↔  "
                f"{second.prospect_id} {second.company_name}   (same {how})"
            )
        print("\n  Contacting the same business twice loses it. Suppress one of each pair:")
        print(
            "    python -m src.admin prospects set-status --prospect-id <id> "
            "--status SUPPRESSED --note 'duplicate of <other id>'"
        )
    else:
        print("  None.")

    _rule("Blocked by the suppression list")
    blocked = [(p, store.suppressions.blocks(p)) for p in store.prospects]
    blocked = [(p, reason) for p, reason in blocked if reason]
    if blocked:
        for prospect, reason in blocked:
            print(f"  {prospect.prospect_id} {prospect.company_name}: {reason}")
    else:
        print("  None.")

    _rule("Missing research")
    missing_website = [p for p in store.prospects if not p.website]
    unverified = [p for p in store.prospects if "verify" in (p.notes or "").lower()]
    no_contact = [
        p
        for p in store.prospects
        if p.contactable and not p.has_verified_email and p.contact_route == "unknown"
    ]
    for label, rows in [
        ("no website recorded", missing_website),
        ("website flagged for verification", unverified),
        ("no usable contact route", no_contact),
    ]:
        print(f"  {len(rows):>3}  {label}")
        for prospect in rows[:8]:
            print(f"         {prospect.prospect_id} {prospect.company_name}")
        if len(rows) > 8:
            print(f"         … and {len(rows) - 8} more")
    problems += len(unverified)

    _rule("Priority spread")
    counts: dict[str, int] = {}
    for prospect in store.prospects:
        counts[prospect.priority.value] = counts.get(prospect.priority.value, 0) + 1
    for band in ("A", "B", "C", "SUPPRESS"):
        print(f"  Priority {band:<9} {counts.get(band, 0)}")
    if not counts:
        print("  Not scored yet — run: python -m src.admin prospects rescore")

    _rule("Contact details")
    verified = sum(1 for p in store.prospects if p.has_verified_email)
    print(f"  {verified} of {len(store.prospects)} have a verified contact address.")
    print(
        "  Addresses are never generated. Open each website, find the real sales@ or\n"
        "  enquiries@ address, then record it with:\n"
        "    python -m src.admin prospects set-contact --prospect-id P001 \\\n"
        "      --email sales@example.co.uk --source website_verified"
    )

    print(f"\n{problems} item(s) worth fixing before you start sending.")
    return 0


# ---------------------------------------------------------------------------
# preview and outreach
# ---------------------------------------------------------------------------


def cmd_prospect_preview(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    prospect = store.require(args.prospect_id)

    init_db()
    with session_scope() as session:
        leads, source = load_leads(
            session=session,
            csv_path=getattr(args, "from_csv", None),
            journal_number=getattr(args, "journal", None),
        )

    if not leads:
        print(
            "No opportunities available.\n\n"
            "Run a week first:   python -m src.pipeline weekly\n"
            "or point at a file: --from-csv reports/validation/top_opportunities.csv"
        )
        return 1

    result = select_for_prospect(
        prospect, leads, source=source, count=int(getattr(args, "count", 3) or 3)
    )
    markdown = render_markdown(result)
    print(markdown)

    out_dir = Path(getattr(args, "out", None) or PREVIEW_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{prospect.prospect_id}_preview.md"
    md_path.write_text(markdown, encoding="utf-8")
    written = [md_path]

    if getattr(args, "csv", False):
        written.append(write_preview_csv(result, out_dir / f"{prospect.prospect_id}_preview.csv"))

    if getattr(args, "draft_email", False):
        if not prospect.contactable:
            print("\nNot drafting an email: this prospect must not be contacted.")
        elif result.count == 0:
            print("\nNot drafting an email: no suitable opportunities to show them.")
        else:
            draft = render_draft(prospect, "email_1", preview=result)
            written.append(write_draft(draft))

    _rule("Email-ready block")
    print(render_email_block(result))

    _rule("Written")
    for path in written:
        print(f"  {path}")
    print(
        "\nNothing has been sent. Read the draft, check every brand against the source "
        "link, then send it yourself from your own mailbox."
    )
    if result.count:
        print(
            f"After sending: python -m src.admin prospects set-status "
            f"--prospect-id {prospect.prospect_id} --status EMAIL_1_SENT"
        )
    return 0


def cmd_outreach_due(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    report = outreach_due(store.prospects, suppressions=store.suppressions)

    if not report.actions:
        print("Nothing is due today.")
    else:
        current = ""
        for action in report.actions:
            if action.action != current:
                current = action.action
                _rule(current)
            prospect = action.prospect
            print(
                f"  {prospect.prospect_id}  {prospect.company_name[:32]:<34}"
                f"[{prospect.priority.value}]  {action.reason}"
            )
            if action.command and getattr(args, "commands", True):
                print(f"        {action.command}")

    _rule("Summary")
    print(f"  Due an action              {len(report.actions)}")
    print(f"  Waiting (nothing to do)    {len(report.waiting)}")
    print(f"  Subscribed                 {len(report.subscribed)}")
    print(f"  Must not be contacted      {len(report.do_not_contact)}")
    if report.blocked_by_suppression:
        print(f"  Blocked by suppression     {len(report.blocked_by_suppression)}")
        for prospect, reason in report.blocked_by_suppression:
            print(f"      {prospect.prospect_id} {prospect.company_name}: {reason}")

    if report.do_not_contact:
        _rule("Do not contact")
        for prospect in report.do_not_contact:
            print(
                f"  {prospect.prospect_id}  {prospect.company_name[:32]:<34}"
                f"{prospect.status.value}"
                + (f" — {prospect.suppression_reason}" if prospect.suppression_reason else "")
            )

    print("\nNo email is sent by any of this. Every send is yours to make by hand.")
    return 0


def cmd_outreach_draft(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    prospect = store.require(args.prospect_id)
    template = getattr(args, "template", "email_1")

    preview = None
    if template == "email_1":
        init_db()
        with session_scope() as session:
            leads, source = load_leads(session=session, csv_path=getattr(args, "from_csv", None))
        if leads:
            preview = select_for_prospect(prospect, leads, source=source)

    try:
        draft = render_draft(prospect, template, preview=preview)
    except PermissionError as exc:
        print(f"Refused: {exc}")
        return 1

    path = write_draft(draft)
    print(f"Subject: {draft.subject}\n")
    print(draft.body)
    if draft.warnings:
        _rule("Before you send")
        for warning in draft.warnings:
            print(f"  - {warning}")
    print(f"\nDraft written to {path}. Nothing has been sent.")
    return 0


# ---------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------


def cmd_prepare_sample(args) -> int:  # type: ignore[no-untyped-def]
    """The whole sample workflow, short of the send itself."""
    store = load_store()
    prospect = store.require(args.prospect_id)

    if not prospect.contactable:
        print(
            f"Refused: {prospect.prospect_id} ({prospect.company_name}) is "
            f"{prospect.status.value} and must not be contacted."
        )
        return 1

    init_db()
    with session_scope() as session:
        leads, source = load_leads(
            session=session,
            csv_path=getattr(args, "from_csv", None),
            journal_number=getattr(args, "journal", None),
        )
    if not leads:
        print("No opportunities available to build a sample from. Run a week first.")
        return 1

    # Record that they asked, unless they are already further along. A prospect
    # who is already at SAMPLE_SENT does not go backwards, and that is not an
    # error worth stopping for.
    if prospect.status in {ProspectStatus.EMAIL_1_SENT, ProspectStatus.REPLIED_INTERESTED}:
        try:
            store.set_status(prospect, ProspectStatus.SAMPLE_REQUESTED)
            store.save()
        except (TransitionError, SuppressedProspectError) as exc:
            print(f"Note: status left as {prospect.status.value} — {exc}")

    out_dir = Path(getattr(args, "out", None) or (SAMPLE_DIR / prospect.prospect_id))
    pack = build_sample_pack(
        leads,
        out_dir=out_dir,
        watermark=not getattr(args, "no_watermark", False),
        supplier_label=prospect.supplier_category.replace("_", " ").title(),
    )

    draft = render_draft(prospect, "email_2", sample_count=pack.count)
    draft_path = write_draft(draft, directory=out_dir)

    _rule(f"Sample prepared for {prospect.company_name}")
    print(f"  Opportunities   {pack.count} ({pack.high_count} HIGH, {pack.medium_count} MEDIUM)")
    print(f"  Journal weeks   {', '.join(pack.journals) or '—'}")
    print(f"  Lead source     {source}")
    if pack.excluded:
        print("  Held back       " + ", ".join(f"{n} {why}" for why, n in pack.excluded.items()))

    _rule("Files")
    print(f"  CSV to attach   {pack.csv_path}")
    print(f"  HTML report     {pack.html_path}")
    print(f"  Cover email     {draft_path}")

    settings = get_settings()
    _rule("What to do now")
    print("  1. Open the HTML report and read it as the customer would.")
    print(f"  2. Check the row count in the CSV matches the {pack.count} in the cover email.")
    print("  3. Send the cover email yourself, with the CSV attached.")
    if not settings.email_enabled:
        print(
            "     (Resend is not connected, which is why nothing is sent from here. "
            "That is the intended state until you have a customer.)"
        )
    print(
        f"  4. Then record it: python -m src.admin prospects set-status "
        f"--prospect-id {prospect.prospect_id} --status SAMPLE_SENT"
    )
    if draft.warnings:
        _rule("Warnings")
        for warning in draft.warnings:
            print(f"  - {warning}")
    return 0


def cmd_build_sample(args) -> int:  # type: ignore[no-untyped-def]
    """A sample with no prospect attached — for the website or a cold link."""
    init_db()
    with session_scope() as session:
        leads, source = load_leads(
            session=session,
            csv_path=getattr(args, "from_csv", None),
            journal_number=getattr(args, "journal", None),
        )
    if not leads:
        print("No opportunities available. Run a week first, or pass --from-csv.")
        return 1
    out_dir = Path(getattr(args, "out", None) or (SAMPLE_DIR / "generic"))
    pack = build_sample_pack(
        leads, out_dir=out_dir, watermark=not getattr(args, "no_watermark", False)
    )
    print(f"Source: {source}")
    print(f"{pack.count} opportunities ({pack.high_count} HIGH, {pack.medium_count} MEDIUM)")
    if pack.excluded:
        print("Held back: " + ", ".join(f"{n} {why}" for why, n in pack.excluded.items()))
    print(f"\n  {pack.csv_path}\n  {pack.html_path}")
    return 0


# ---------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------


def cmd_customer_status(args) -> int:  # type: ignore[no-untyped-def]
    init_db()
    with session_scope() as session:
        stmt = select(Customer)
        if getattr(args, "customer_id", None):
            stmt = stmt.where(Customer.id == int(args.customer_id))
        customers = list(session.execute(stmt).scalars())
        if not customers:
            print("No customers found.")
            return 0
        for customer in customers:
            recipients = list(
                session.execute(
                    select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
                ).scalars()
            )
            deliveries = list(
                session.execute(
                    select(Delivery).where(Delivery.customer_id == customer.id)
                ).scalars()
            )
            _rule(f"#{customer.id} {customer.company}")
            print(f"  Subscription      {customer.subscription_status}")
            print(f"  Plan              {customer.plan_key}")
            print(f"  Supplier type     {customer.supplier_type or '—'}")
            print(f"  From prospect     {customer.prospect_id or '—'}")
            print(f"  Stripe customer   {customer.stripe_customer_id or '— (not connected)'}")
            allowed = delivery_allowed(customer)
            print(
                f"  Next Friday feed  {'yes' if allowed else 'no'}"
                + (f" — {delivery_block_reason(customer)}" if not allowed else "")
            )
            if customer.past_due_since:
                print(
                    f"  Past due since    {customer.past_due_since:%Y-%m-%d} "
                    f"(grace {PAST_DUE_GRACE_DAYS} days)"
                )
            if customer.cancelled_at:
                print(f"  Cancelled         {customer.cancelled_at:%Y-%m-%d}")
            print(f"  Recipients        {', '.join(p.recipient_email for p in recipients) or '—'}")
            for pref in recipients:
                if pref.supplier_category or pref.buying_intent_categories:
                    print(
                        f"      {pref.recipient_email}: {pref.supplier_category or 'any'} · "
                        f"{', '.join(pref.buying_intent_categories) or 'all categories'}"
                    )
            weekly = [d for d in deliveries if d.kind == "weekly_feed"]
            transactional = [d for d in deliveries if d.kind != "weekly_feed"]
            print(
                f"  Weekly feeds      {len(weekly)} "
                f"({sum(1 for d in weekly if d.status == 'sent')} sent, "
                f"{sum(1 for d in weekly if d.status == 'rendered_not_sent')} to outbox)"
            )
            if transactional:
                print(
                    "  Transactional     "
                    + ", ".join(f"{d.kind}={d.status}" for d in transactional)
                )
    return 0


def cmd_customer_lifecycle(args) -> int:  # type: ignore[no-untyped-def]
    """Drive a lifecycle transition by hand, for a customer added manually.

    Stripe does this automatically once connected. Until then this is how a
    customer who paid by invoice gets the same onboarding.
    """
    init_db()
    event = args.event
    with session_scope() as session:
        customer = session.get(Customer, int(args.customer_id))
        if customer is None:
            print(f"No customer with id {args.customer_id}.")
            return 1
        if event == "start":
            outcome = on_subscription_started(session, customer)
        elif event == "payment-failed":
            outcome = on_payment_failed(
                session, customer, invoice_id=getattr(args, "reference", "")
            )
        else:
            outcome = on_cancelled(session, customer)

        print(f"{customer.company}: {outcome.action}")
        print(f"  {outcome.detail}")
        print(f"  Next Friday feed: {'yes' if outcome.delivery_enabled else 'no'}")
        if outcome.messages:
            _rule("Messages prepared")
            for message in outcome.messages:
                location = message.path or message.error or ""
                print(f"  {message.kind:<24} {message.recipient:<32} {message.status} {location}")
        else:
            print("\n  No recipients recorded, so no messages were prepared.")
            print("  Add one: python -m src.pipeline add-customer --company '…' --email …")
    return 0


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------


def cmd_feedback(args) -> int:  # type: ignore[no-untyped-def]
    action = getattr(args, "action", "summary")
    init_db()
    if action == "add":
        with session_scope() as session:
            feedback = record_feedback(
                session,
                state=args.state,
                trademark_number=getattr(args, "trademark", None),
                dedupe_key=getattr(args, "dedupe_key", None),
                customer_id=int(args.customer_id) if getattr(args, "customer_id", None) else None,
                note=getattr(args, "note", None),
            )
            print(
                f"Recorded {feedback.state} for "
                f"{feedback.brand_name or feedback.trademark_number or 'lead'}."
            )
            print(f"  {STATE_MEANING[FeedbackState(feedback.state)]}")
        print("\nThis does not change scoring. It is evidence for tuning it later, by hand.")
        return 0

    if action == "import":
        with session_scope() as session:
            imported, problems = import_feedback_csv(session, args.path)
        print(f"Imported {imported} feedback row(s).")
        for problem in problems:
            print(f"  skipped: {problem}")
        return 0 if not problems else 1

    with session_scope() as session:
        summary = summarise_feedback(session)
        unrated = unrated_lead_count(session)

    if not summary.total:
        print(
            "No feedback recorded yet.\n\n"
            "Ask each customer which leads were useful, then record it:\n"
            "  python -m src.admin feedback add --state USEFUL --trademark UK00003275632 "
            "--customer-id 1\n"
            "or import a spreadsheet they sent back:\n"
            "  python -m src.admin feedback import --path feedback.csv\n\n"
            "States: " + ", ".join(s.value for s in FeedbackState)
        )
        return 0

    _rule("Feedback")
    print(f"  {summary.total} rated, {unrated} delivered leads still unrated")
    rate = summary.usefulness_rate
    print(
        f"  Called useful or acted on: {summary.positive}"
        + (f" ({rate:.0%})" if rate is not None else "")
    )

    _rule("By state")
    for state, count in sorted(summary.by_state.items(), key=lambda kv: -kv[1]):
        print(f"  {state:<18} {count:>3}")

    patterns = summary.patterns()
    if patterns:
        _rule("Recurring patterns")
        for line in patterns:
            print(f"  {line}")
    else:
        print("\nNo pattern yet — a state needs to appear at least twice to be worth reading.")

    if summary.by_customer:
        _rule("By customer")
        for name, states in summary.by_customer.items():
            print(f"  {name:<32} " + ", ".join(f"{k}×{v}" for k, v in states.items()))

    if summary.by_category:
        _rule("By product category")
        for name, states in summary.by_category.items():
            print(f"  {name:<32} " + ", ".join(f"{k}×{v}" for k, v in states.items()))

    if summary.notes:
        _rule("What they actually said")
        for brand, note in summary.notes:
            print(f"  {brand[:28]:<30} {note}")

    print("\nScoring is unchanged by any of this, deliberately. See src/sales/feedback.py.")
    return 0


# ---------------------------------------------------------------------------
# business status and costs
# ---------------------------------------------------------------------------


def cmd_business_status(args) -> int:  # type: ignore[no-untyped-def]
    store = load_store()
    init_db()
    with session_scope() as session:
        status = business_status(session, store.prospects)

    funnel = status.funnel
    print("# LaunchTrace business status")
    print(f"\nGenerated {status.generated_at:%Y-%m-%d %H:%M} UTC")

    _rule("Sales funnel")
    rows = [
        ("Prospects on the list", funnel.total),
        ("Qualified (Priority A or B)", funnel.qualified),
        ("First email sent", funnel.email_1_sent),
        ("Replied", funnel.replies),
        ("Sample requested", funnel.sample_requests),
        ("Samples sent", funnel.samples_sent),
        ("Offers sent", funnel.offers_sent),
        ("Paying customers", funnel.subscribed),
        ("Opted out", funnel.opted_out),
        ("Suppressed", funnel.suppressed),
    ]
    width = max(len(k) for k, _ in rows)
    for label, value in rows:
        print(f"  {label:<{width}}  {value:>4}")

    _rule("Conversion")
    for rate in (
        funnel.reply_rate,
        funnel.sample_rate,
        funnel.conversion_rate,
        funnel.end_to_end_rate,
    ):
        print(f"  {rate.label:<34} {rate.display()}")
    print(f"\n  MRR  {status.mrr_display}")

    product = status.product
    _rule("Product")
    print(f"  Deliverable opportunities stored   {product.opportunities_total}")
    print(f"  HIGH / MEDIUM                      {product.high} / {product.medium}")
    print(f"  Journal weeks processed            {product.weeks_covered}")
    print(f"  Average per week                   {product.per_week}")
    print(f"  Feedback recorded                  {product.feedback.total}")
    print(f"  Called useful                      {product.usefulness.display()}")
    print(f"  Flagged as should-not-have-shown   {product.false_positive_signals}")

    ops = status.operations
    _rule("Operations")
    print(
        f"  Last run            {ops.last_run_id or '—'} "
        f"({ops.last_run_status}, journal {ops.last_run_journal or '—'})"
    )
    print(f"  Runs total/failed   {ops.runs_total} / {ops.runs_failed}")
    print(
        f"  Deliveries          {ops.deliveries_sent} sent, "
        f"{ops.deliveries_rendered_only} to outbox, {ops.deliveries_failed} failed"
    )
    print(f"  Active customers    {ops.active_customers}")
    print(f"  Past due            {ops.past_due_customers}")
    print(f"  Cancelled           {ops.cancelled_customers}")
    print(f"  Errors logged       {ops.recent_errors}")
    print(
        f"  Live integrations   send_mode={ops.send_mode}, "
        f"email={'live' if ops.email_live else 'outbox'}, "
        f"stripe={'live' if ops.stripe_live else 'stub'}, "
        f"search={'live' if ops.search_live else 'off'}"
    )
    for company, reason in ops.blocked_customers:
        print(f"      not receiving: {company} — {reason}")

    costs = status.costs
    _rule("Cost and margin")
    print(f"  Estimated cost per weekly run     {format_pounds(costs.weekly_run_pence)}")
    print(f"  Estimated monthly cost            {format_pounds(costs.total_monthly_pence)}")
    if costs.customers:
        print(
            f"  Cost per active customer          {format_pounds(costs.per_customer_monthly_pence)}"
        )
        print(f"  Monthly revenue                   {format_pounds(costs.monthly_revenue_pence)}")
        margin = costs.gross_margin_percent
        print(
            f"  Gross margin                      {format_pounds(costs.gross_margin_pence)}"
            + (f" ({margin}%)" if margin is not None else "")
        )
    else:
        print(f"  Break-even                        {costs.breakeven_customers} customer(s)")
    if costs.assumed_lines:
        print("\n  Assumed, not invoiced: " + ", ".join(line.label for line in costs.assumed_lines))
        print("  Replace them in config/costs.json once you have a real bill.")

    if not funnel.email_1_sent:
        _rule("Next action")
        print("  No outreach has happened yet. Start with:")
        print("    python -m src.admin prospects ready")
        print("  Then follow FIRST_CUSTOMER_PLAYBOOK.md.")
    return 0


def cmd_costs(args) -> int:  # type: ignore[no-untyped-def]
    settings = get_settings()
    customers = int(getattr(args, "customers", 0) or 0)
    if not customers:
        init_db()
        with session_scope() as session:
            from src.sales.metrics import paying_customers

            customers = len(paying_customers(session))

    estimate = estimate_costs(
        customers=customers,
        llm_enabled=settings.llm_enabled,
        search_enabled=settings.search_enabled,
    )

    _rule(f"Estimated cost at {customers} paying customer(s)")
    width = max((len(line.label) for line in estimate.lines), default=10)
    for line in estimate.lines:
        flag = "  (assumed)" if line.assumed else ""
        print(
            f"  {line.label:<{width}}  {format_pounds(line.monthly_pence):>10}  {line.basis}{flag}"
        )

    _rule("Totals")
    print(f"  Per weekly pipeline run   {format_pounds(estimate.weekly_run_pence)}")
    print(f"  Fixed monthly             {format_pounds(estimate.fixed_pence)}")
    print(f"  Monthly total             {format_pounds(estimate.total_monthly_pence)}")
    if customers:
        print(f"  Per active customer       {format_pounds(estimate.per_customer_monthly_pence)}")
        print(f"  Revenue                   {format_pounds(estimate.monthly_revenue_pence)}")
        margin = estimate.gross_margin_percent
        print(
            f"  Gross margin              {format_pounds(estimate.gross_margin_pence)}"
            + (f" ({margin}%)" if margin is not None else "")
        )
    print(f"  Break-even                {estimate.breakeven_customers} customer(s)")

    if estimate.assumed_lines:
        _rule("Assumptions to replace with real invoices")
        for line in estimate.assumed_lines:
            print(f"  {line.label}: {line.source_note}")
    print("\nEdit config/costs.json to change any figure.")
    return 0


def cmd_backup(args) -> int:  # type: ignore[no-untyped-def]
    """Copy everything that would hurt to lose into one dated folder."""
    import shutil

    destination = Path(
        getattr(args, "out", None) or (REPORTS_DIR / "backups" / today().isoformat())
    )
    destination.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    copied: list[str] = []
    for source in [
        Path("outreach/prospects_seed.csv"),
        Path("config"),
    ]:
        if not source.exists():
            continue
        target = destination / source.name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        copied.append(str(target))

    if settings.is_sqlite:
        db_file = Path(settings.database_url.split("///")[-1])
        if db_file.exists():
            shutil.copy2(db_file, destination / db_file.name)
            copied.append(str(destination / db_file.name))

    init_db()
    with session_scope() as session:
        from src.db.tables import ProspectStateRow, ProspectSuppression, SuppressionRule

        rules = list(session.execute(select(SuppressionRule)).scalars())
        prospect_state = list(session.execute(select(ProspectStateRow)).scalars())
        prospect_suppressions = list(session.execute(select(ProspectSuppression)).scalars())
        feedback = list(session.execute(select(LeadFeedback)).scalars())
        customers = list(session.execute(select(Customer)).scalars())

    import csv as _csv

    with (destination / "customers.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(
            [
                "id",
                "company",
                "plan_key",
                "subscription_status",
                "stripe_customer_id",
                "stripe_subscription_id",
                "prospect_id",
                "created_at",
            ]
        )
        for customer in customers:
            writer.writerow(
                [
                    customer.id,
                    customer.company,
                    customer.plan_key,
                    customer.subscription_status,
                    customer.stripe_customer_id or "",
                    customer.stripe_subscription_id or "",
                    customer.prospect_id or "",
                    customer.created_at.isoformat(),
                ]
            )
    copied.append(str(destination / "customers.csv"))

    with (destination / "db_suppression_rules.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["rule_type", "value", "reason", "created_by", "active", "created_at"])
        for rule in rules:
            writer.writerow(
                [
                    rule.rule_type,
                    rule.value,
                    rule.reason or "",
                    rule.created_by or "",
                    rule.active,
                    rule.created_at.isoformat(),
                ]
            )
    copied.append(str(destination / "db_suppression_rules.csv"))

    # The live outreach state. It is not in git by design, which makes this
    # export the only copy outside the database.
    from src.sales.store import STATE_COLUMNS

    with (destination / "prospect_state.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["prospect_id", *STATE_COLUMNS])
        for state_row in prospect_state:
            writer.writerow(
                [state_row.prospect_id]
                + [
                    value.isoformat()
                    if hasattr(value, "isoformat")
                    else ("" if value is None else value)
                    for value in (getattr(state_row, name) for name in STATE_COLUMNS)
                ]
            )
    copied.append(str(destination / "prospect_state.csv"))

    with (destination / "prospect_suppressions.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["kind", "value", "company_name", "date_added", "reason", "added_by"])
        for entry in prospect_suppressions:
            writer.writerow(
                [
                    entry.kind,
                    entry.value,
                    entry.company_name or "",
                    entry.date_added or "",
                    entry.reason or "",
                    entry.added_by or "",
                ]
            )
    copied.append(str(destination / "prospect_suppressions.csv"))

    with (destination / "lead_feedback.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(
            ["customer_id", "trademark_number", "brand_name", "state", "note", "created_at"]
        )
        for row in feedback:
            writer.writerow(
                [
                    row.customer_id or "",
                    row.trademark_number or "",
                    row.brand_name or "",
                    row.state,
                    row.note or "",
                    row.created_at.isoformat(),
                ]
            )
    copied.append(str(destination / "lead_feedback.csv"))

    print(f"Backup written to {destination}\n")
    for path in copied:
        print(f"  {path}")
    print(
        "\nKeep a copy somewhere that is not this machine. prospect_suppressions.csv is the "
        "one file that must never be lost — losing it means contacting people who asked you not to."
    )
    print("See docs/BACKUP_AND_RECOVERY.md for what is source of truth for each file.")
    return 0


__all__ = [
    "cmd_backup",
    "cmd_build_sample",
    "cmd_business_status",
    "cmd_costs",
    "cmd_customer_lifecycle",
    "cmd_customer_status",
    "cmd_feedback",
    "cmd_outreach_draft",
    "cmd_outreach_due",
    "cmd_prepare_sample",
    "cmd_prospect_preview",
    "cmd_prospects",
]
