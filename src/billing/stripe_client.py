"""Stripe subscription billing.

Two plans exist in configuration (``founding_monthly`` at £79 and
``standard_monthly`` at £129), but only the founding plan is offered publicly
for now.

Without ``STRIPE_SECRET_KEY`` the client runs in *stub mode*: checkout returns a
local URL that records the intent, so the site, the webhook handling and the
customer lifecycle can all be exercised end to end before a Stripe account
exists.  Nothing is faked as a real payment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.logging_setup import get_logger
from src.settings import Settings, get_settings, load_config

log = get_logger(__name__)


@dataclass
class Plan:
    key: str
    name: str
    price_pence: int
    interval: str
    max_recipients: int
    stripe_price_id_env: str
    features: list[str]
    offered_publicly: bool

    @property
    def price_display(self) -> str:
        return f"£{self.price_pence / 100:.0f}"


def load_plans() -> dict[str, Plan]:
    cfg = load_config("customer_plans.json")
    return {
        p["key"]: Plan(
            key=p["key"],
            name=p["name"],
            price_pence=p["price_pence"],
            interval=p["interval"],
            max_recipients=p["max_recipients"],
            stripe_price_id_env=p["stripe_price_id_env"],
            features=p["features"],
            offered_publicly=p.get("offered_publicly", False),
        )
        for p in cfg["plans"]
        if p.get("active", True)
    }


@dataclass
class CheckoutSession:
    url: str
    session_id: str
    live: bool
    message: str | None = None


class StripeBilling:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.plans = load_plans()

    @property
    def live(self) -> bool:
        return bool(self.settings.stripe_secret_key)

    def price_id_for(self, plan_key: str) -> str | None:
        mapping = {
            "founding_monthly": self.settings.stripe_founding_price_id,
            "standard_monthly": self.settings.stripe_standard_price_id,
        }
        return mapping.get(plan_key) or None

    def create_checkout_session(
        self, plan_key: str, email: str | None = None, company: str | None = None
    ) -> CheckoutSession:
        plan = self.plans.get(plan_key)
        if plan is None:
            raise ValueError(f"Unknown plan {plan_key!r}")
        base = self.settings.site_url.rstrip("/")
        if not self.live:
            return CheckoutSession(
                url=f"{base}/billing/not-connected?plan={plan_key}",
                session_id=f"stub_{plan_key}",
                live=False,
                message=(
                    "Stripe is not connected yet. Set STRIPE_SECRET_KEY and the price IDs, "
                    "then this button opens a real Stripe Checkout page."
                ),
            )
        price_id = self.price_id_for(plan_key)
        if not price_id:
            return CheckoutSession(
                url=f"{base}/billing/not-connected?plan={plan_key}",
                session_id=f"stub_{plan_key}",
                live=False,
                message=(
                    f"Stripe is connected but {plan.stripe_price_id_env} is not set. "
                    "Create the price in Stripe and put its id in that variable."
                ),
            )

        import stripe

        stripe.api_key = self.settings.stripe_secret_key
        metadata = {"plan_key": plan_key, "company": company or ""}
        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base}/billing/cancelled",
            "allow_promotion_codes": True,
            "metadata": metadata,
            "subscription_data": {"metadata": metadata},
        }
        if email:
            params["customer_email"] = email
        session = stripe.checkout.Session.create(**params)
        log.info("stripe.checkout_created", plan=plan_key, session=session.id)
        return CheckoutSession(url=str(session.url), session_id=str(session.id), live=True)

    def create_billing_portal_session(self, stripe_customer_id: str) -> str | None:
        if not self.live:
            return None
        import stripe

        stripe.api_key = self.settings.stripe_secret_key
        portal = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{self.settings.site_url.rstrip('/')}/billing/manage",
        )
        return str(portal["url"])

    def cancel_subscription(self, subscription_id: str) -> dict[str, Any]:
        if not self.live:
            return {"id": subscription_id, "status": "canceled", "live": False}
        import stripe

        stripe.api_key = self.settings.stripe_secret_key
        cancelled = stripe.Subscription.cancel(subscription_id)
        return {"id": cancelled.id, "status": cancelled.status, "live": True}

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify the Stripe signature. Never trust an unverified webhook body."""
        import stripe

        if not self.settings.stripe_webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET is not set; refusing to process the webhook")
        return dict(
            stripe.Webhook.construct_event(payload, signature, self.settings.stripe_webhook_secret)
        )


def get_billing(settings: Settings | None = None) -> StripeBilling:
    return StripeBilling(settings)
