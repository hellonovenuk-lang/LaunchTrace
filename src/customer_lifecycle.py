"""What happens to a customer after they pay.

This is the state machine between Stripe and the Friday email. It is complete
in code without any credential: with no Stripe key the webhook layer runs in
stub mode, and with no Resend key every message is written to
``reports/outbox/`` exactly as it would have been sent.

The three transitions that matter:

* **subscription started** — the customer exists, recipients are recorded,
  delivery is enabled, and welcome, confirmation and first-feed-timing messages
  are prepared once each;
* **payment failed** — the account goes past due, one message goes out, and the
  feed continues through a grace period before pausing. It is never cancelled
  by us, and the message is never sent twice for the same failure;
* **cancelled** — delivery stops at the next run and a cancellation message is
  prepared once.

Every prepared message is recorded in ``deliveries`` with an idempotency key, so
retried webhooks and re-run commands cannot produce a second copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.repository import record_delivery
from src.db.tables import Customer, CustomerPreference
from src.deliver.email_render import RenderedEmail
from src.deliver.resend_client import EmailSender
from src.deliver.transactional import (
    next_friday,
    render_cancellation_confirmed,
    render_first_feed_timing,
    render_payment_failed,
    render_subscription_confirmed,
    render_welcome,
)
from src.logging_setup import get_logger
from src.settings import Settings, get_settings

log = get_logger(__name__)

# How long a past-due customer keeps receiving the feed before it pauses.
# Long enough that an expired card is not a lost customer, short enough that a
# non-paying account is not an indefinite free subscription.
PAST_DUE_GRACE_DAYS = 14

# Subscription states that receive the weekly feed at all.
DELIVERABLE_STATUSES = {"active", "trialing", "past_due"}


@dataclass
class PreparedMessage:
    kind: str
    recipient: str
    subject: str
    status: str  # sent | rendered_not_sent | failed | already_prepared
    path: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"sent", "rendered_not_sent", "already_prepared"}


@dataclass
class LifecycleOutcome:
    action: str
    customer_id: int | None = None
    messages: list[PreparedMessage] = field(default_factory=list)
    delivery_enabled: bool = False
    detail: str = ""


def _recipients(session: Session, customer: Customer) -> list[CustomerPreference]:
    return list(
        session.execute(
            select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
        ).scalars()
    )


def _prepare(
    session: Session,
    customer: Customer,
    kind: str,
    rendered: RenderedEmail,
    recipient: str,
    sender: EmailSender,
    reference: str = "",
) -> PreparedMessage:
    """Render, record and hand one message to the sender exactly once.

    ``reference`` distinguishes repeatable events — a second failed invoice is
    a genuinely new message, a redelivered webhook for the same one is not.
    """
    key = f"customer:{customer.id}:{kind}" + (f":{reference}" if reference else "")
    delivery, created = record_delivery(
        session,
        run_id=f"lifecycle:{kind}",
        journal_number="",
        recipient_email=recipient,
        idempotency_key=key,
        customer_id=customer.id,
        kind=kind,
    )
    if not created and delivery.status in {"sent", "rendered_not_sent"}:
        return PreparedMessage(
            kind=kind,
            recipient=recipient,
            subject=rendered.subject,
            status="already_prepared",
        )

    result = sender.send([recipient], rendered, idempotency_key=key)
    delivery.status = result.status
    delivery.provider_message_id = result.message_id
    delivery.error = result.error
    if result.status == "sent":
        delivery.sent_at = datetime.now(UTC)
    session.flush()
    return PreparedMessage(
        kind=kind,
        recipient=recipient,
        subject=rendered.subject,
        status=result.status,
        path=result.path,
        error=result.error,
    )


def on_subscription_started(
    session: Session,
    customer: Customer,
    settings: Settings | None = None,
    sender: EmailSender | None = None,
    first_feed: date | None = None,
) -> LifecycleOutcome:
    """Everything that must be true once a subscription succeeds."""
    settings = settings or get_settings()
    sender = sender or EmailSender(settings)

    customer.subscription_status = (
        customer.subscription_status if customer.subscription_status == "trialing" else "active"
    )
    customer.delivery_enabled = True
    customer.cancelled_at = None
    customer.past_due_since = None
    session.flush()

    preferences = _recipients(session, customer)
    friday = first_feed or next_friday(date.today())
    messages: list[PreparedMessage] = []

    for pref in preferences:
        messages.append(
            _prepare(
                session,
                customer,
                "welcome",
                render_welcome(
                    company=customer.company,
                    contact_name=customer.contact_name,
                    plan_key=customer.plan_key,
                    recipients=[p.recipient_email for p in preferences],
                    first_feed=friday,
                    settings=settings,
                ),
                pref.recipient_email,
                sender,
            )
        )
        messages.append(
            _prepare(
                session,
                customer,
                "subscription_confirmed",
                render_subscription_confirmed(
                    company=customer.company,
                    plan_key=customer.plan_key,
                    contact_name=customer.contact_name,
                    settings=settings,
                ),
                pref.recipient_email,
                sender,
            )
        )
        messages.append(
            _prepare(
                session,
                customer,
                "first_feed_timing",
                render_first_feed_timing(
                    company=customer.company,
                    first_feed=friday,
                    contact_name=customer.contact_name,
                    settings=settings,
                ),
                pref.recipient_email,
                sender,
            )
        )

    log.info(
        "lifecycle.subscription_started",
        customer_id=customer.id,
        recipients=len(preferences),
        prepared=len(messages),
    )
    return LifecycleOutcome(
        action="subscription_started",
        customer_id=customer.id,
        messages=messages,
        delivery_enabled=True,
        detail=(
            f"{len(preferences)} recipient(s) recorded; first feed {friday.isoformat()}"
            if preferences
            else "No recipients recorded yet — add one before Friday"
        ),
    )


def on_payment_failed(
    session: Session,
    customer: Customer,
    invoice_id: str = "",
    settings: Settings | None = None,
    sender: EmailSender | None = None,
) -> LifecycleOutcome:
    """Mark the account past due and warn once, without stopping the feed yet."""
    settings = settings or get_settings()
    sender = sender or EmailSender(settings)

    customer.subscription_status = "past_due"
    if customer.past_due_since is None:
        customer.past_due_since = datetime.now(UTC)
    session.flush()

    messages = [
        _prepare(
            session,
            customer,
            "payment_failed",
            render_payment_failed(
                company=customer.company,
                grace_days=PAST_DUE_GRACE_DAYS,
                contact_name=customer.contact_name,
                settings=settings,
            ),
            pref.recipient_email,
            sender,
            reference=invoice_id,
        )
        for pref in _recipients(session, customer)
    ]

    log.info("lifecycle.payment_failed", customer_id=customer.id, invoice=invoice_id or "unknown")
    return LifecycleOutcome(
        action="payment_failed",
        customer_id=customer.id,
        messages=messages,
        delivery_enabled=delivery_allowed(customer),
        detail=f"Feed continues for {PAST_DUE_GRACE_DAYS} days from the first failure",
    )


def on_payment_recovered(session: Session, customer: Customer) -> LifecycleOutcome:
    """A successful payment clears past-due state without any email.

    Nobody wants a message telling them their card worked.
    """
    if customer.subscription_status == "past_due":
        customer.subscription_status = "active"
    customer.past_due_since = None
    customer.delivery_enabled = True
    session.flush()
    return LifecycleOutcome(
        action="payment_recovered",
        customer_id=customer.id,
        delivery_enabled=True,
        detail="Past-due state cleared",
    )


def on_cancelled(
    session: Session,
    customer: Customer,
    settings: Settings | None = None,
    sender: EmailSender | None = None,
    last_feed: date | None = None,
) -> LifecycleOutcome:
    """Stop delivery and confirm it. Cancellation is immediate, as promised."""
    settings = settings or get_settings()
    sender = sender or EmailSender(settings)

    customer.subscription_status = "cancelled"
    customer.delivery_enabled = False
    customer.cancelled_at = customer.cancelled_at or datetime.now(UTC)
    customer.past_due_since = None
    session.flush()

    messages = [
        _prepare(
            session,
            customer,
            "cancellation_confirmed",
            render_cancellation_confirmed(
                company=customer.company,
                last_feed=last_feed,
                contact_name=customer.contact_name,
                settings=settings,
            ),
            pref.recipient_email,
            sender,
        )
        for pref in _recipients(session, customer)
    ]

    log.info("lifecycle.cancelled", customer_id=customer.id)
    return LifecycleOutcome(
        action="cancelled",
        customer_id=customer.id,
        messages=messages,
        delivery_enabled=False,
        detail="Delivery stops with the next run",
    )


def delivery_allowed(customer: Customer, reference: datetime | None = None) -> bool:
    """Whether this customer should receive the next weekly feed.

    One function, used by both the delivery service and the operator CLI, so
    what the operator sees is what actually happens.
    """
    if not customer.delivery_enabled:
        return False
    if customer.subscription_status not in DELIVERABLE_STATUSES:
        return False
    if customer.subscription_status == "past_due" and customer.past_due_since is not None:
        now = reference or datetime.now(UTC)
        since = customer.past_due_since
        if since.tzinfo is None:  # SQLite hands back naive datetimes
            since = since.replace(tzinfo=UTC)
        if now - since > timedelta(days=PAST_DUE_GRACE_DAYS):
            return False
    return True


def delivery_block_reason(customer: Customer, reference: datetime | None = None) -> str | None:
    """Why this customer is not receiving the feed, in one line for the operator."""
    if delivery_allowed(customer, reference):
        return None
    if not customer.delivery_enabled:
        return "delivery switched off"
    if customer.subscription_status == "cancelled":
        return "subscription cancelled"
    if customer.subscription_status == "past_due":
        return f"past due for more than {PAST_DUE_GRACE_DAYS} days"
    return f"subscription status is {customer.subscription_status}"


__all__ = [
    "DELIVERABLE_STATUSES",
    "LifecycleOutcome",
    "PAST_DUE_GRACE_DAYS",
    "PreparedMessage",
    "delivery_allowed",
    "delivery_block_reason",
    "on_cancelled",
    "on_payment_failed",
    "on_payment_recovered",
    "on_subscription_started",
]
