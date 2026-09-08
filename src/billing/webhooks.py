"""Stripe webhook handling.

Signature verification happens before anything else, and every event id is
recorded so a redelivered webhook is a no-op rather than a duplicate
subscription.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.tables import Customer, CustomerPreference, WebhookEvent
from src.logging_setup import get_logger

log = get_logger(__name__)

HANDLED_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "invoice.paid",
}

# Stripe subscription status -> LaunchTrace subscription status.
STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "canceled": "cancelled",
    "incomplete": "incomplete",
    "incomplete_expired": "cancelled",
    "paused": "paused",
}


@dataclass
class WebhookOutcome:
    handled: bool
    duplicate: bool = False
    action: str = "ignored"
    customer_id: int | None = None
    detail: str | None = None


class WebhookProcessor:
    def __init__(self, session: Session) -> None:
        self.session = session

    def already_processed(self, event_id: str, provider: str = "stripe") -> bool:
        return (
            self.session.execute(
                select(WebhookEvent).where(
                    WebhookEvent.provider == provider, WebhookEvent.event_id == event_id
                )
            ).scalar_one_or_none()
            is not None
        )

    def record(self, event: dict[str, Any], provider: str = "stripe") -> None:
        self.session.add(
            WebhookEvent(
                provider=provider,
                event_id=str(event.get("id", "")),
                event_type=str(event.get("type", "")),
                payload_summary={
                    "type": event.get("type"),
                    "object": (event.get("data", {}) or {}).get("object", {}).get("object"),
                },
            )
        )
        self.session.flush()

    def process(self, event: dict[str, Any]) -> WebhookOutcome:
        event_id = str(event.get("id", ""))
        event_type = str(event.get("type", ""))
        if event_id and self.already_processed(event_id):
            log.info("stripe.webhook.duplicate", event_id=event_id, type=event_type)
            return WebhookOutcome(handled=True, duplicate=True, action="duplicate")
        if event_type not in HANDLED_EVENTS:
            if event_id:
                self.record(event)
            return WebhookOutcome(handled=False, action="ignored", detail=event_type)

        outcome = apply_subscription_event(self.session, event)
        if event_id:
            self.record(event)
        return outcome


def _customer_for(
    session: Session, stripe_customer_id: str | None, email: str | None
) -> Customer | None:
    if stripe_customer_id:
        found = session.execute(
            select(Customer).where(Customer.stripe_customer_id == stripe_customer_id)
        ).scalar_one_or_none()
        if found:
            return found
    if email:
        pref = session.execute(
            select(CustomerPreference).where(CustomerPreference.recipient_email == email)
        ).scalar_one_or_none()
        if pref:
            return session.get(Customer, pref.customer_id)
    return None


def apply_subscription_event(session: Session, event: dict[str, Any]) -> WebhookOutcome:
    """Move a customer's subscription state in response to one Stripe event."""
    event_type = str(event.get("type", ""))
    obj: dict[str, Any] = (event.get("data", {}) or {}).get("object", {}) or {}

    if event_type == "checkout.session.completed":
        email = obj.get("customer_email") or (obj.get("customer_details") or {}).get("email")
        stripe_customer_id = obj.get("customer")
        metadata = obj.get("metadata") or {}
        company = metadata.get("company") or (email or "Unknown").split("@")[-1]
        plan_key = metadata.get("plan_key") or "founding_monthly"

        customer = _customer_for(session, stripe_customer_id, email)
        if customer is None:
            customer = Customer(company=company, plan_key=plan_key)
            session.add(customer)
            session.flush()
        customer.stripe_customer_id = stripe_customer_id
        customer.stripe_subscription_id = obj.get("subscription")
        customer.plan_key = plan_key
        customer.subscription_status = "active"
        customer.delivery_enabled = True
        customer.founding_customer = plan_key == "founding_monthly"
        customer.cancelled_at = None
        if email:
            existing = session.execute(
                select(CustomerPreference).where(
                    CustomerPreference.customer_id == customer.id,
                    CustomerPreference.recipient_email == email,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(CustomerPreference(customer_id=customer.id, recipient_email=email))
        session.flush()
        log.info("stripe.subscription_started", customer_id=customer.id, plan=plan_key)
        return WebhookOutcome(handled=True, action="subscription_started", customer_id=customer.id)

    if event_type.startswith("customer.subscription."):
        stripe_customer_id = obj.get("customer")
        customer = _customer_for(session, stripe_customer_id, None)
        if customer is None:
            return WebhookOutcome(
                handled=True, action="no_matching_customer", detail=str(stripe_customer_id)
            )
        status = STATUS_MAP.get(str(obj.get("status", "")), "unknown")
        if event_type == "customer.subscription.deleted":
            status = "cancelled"
        customer.subscription_status = status
        customer.stripe_subscription_id = obj.get("id") or customer.stripe_subscription_id
        if status == "cancelled":
            customer.delivery_enabled = False
            customer.cancelled_at = datetime.now(UTC)
        elif status in {"active", "trialing"}:
            customer.delivery_enabled = True
            customer.cancelled_at = None
        session.flush()
        log.info("stripe.subscription_updated", customer_id=customer.id, status=status)
        return WebhookOutcome(handled=True, action=f"status_{status}", customer_id=customer.id)

    if event_type == "invoice.payment_failed":
        customer = _customer_for(session, obj.get("customer"), None)
        if customer is None:
            return WebhookOutcome(handled=True, action="no_matching_customer")
        customer.subscription_status = "past_due"
        session.flush()
        return WebhookOutcome(handled=True, action="status_past_due", customer_id=customer.id)

    if event_type == "invoice.paid":
        customer = _customer_for(session, obj.get("customer"), None)
        if customer is None:
            return WebhookOutcome(handled=True, action="no_matching_customer")
        if customer.subscription_status == "past_due":
            customer.subscription_status = "active"
        customer.delivery_enabled = True
        session.flush()
        return WebhookOutcome(handled=True, action="status_active", customer_id=customer.id)

    return WebhookOutcome(handled=False, action="ignored", detail=event_type)
