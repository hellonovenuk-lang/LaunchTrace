"""Transactional customer email.

Three messages carry the whole customer relationship, because there is no
dashboard and no account area:

* **onboarding** — one message on the day they subscribe: the payment is
  confirmed, here is what happens next, and here is the Friday the first feed
  arrives;
* **payment failed** — your card was declined and what happens if it stays that way;
* **cancellation confirmed** — it has stopped, and when the last feed was.

Onboarding used to be three separate messages — welcome, subscription
confirmed, first feed timing — prepared for every recipient at once. Three
emails landing together on day one reads as a mailing list rather than a
person, and every fact in them fits comfortably in one. They are now one
message, ``onboarding``.

Rendering is separate from sending, as everywhere else, so the exact message a
customer would receive can be produced and reviewed with no email credential.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.deliver.email_render import RenderedEmail
from src.errors import RenderFailureError
from src.settings import Settings, get_settings, load_config

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def next_friday(from_date: date) -> date:
    """The next Friday strictly after ``from_date``.

    The pipeline runs on Fridays, so this is when a customer subscribing today
    actually receives something. Saying "next Friday" when it is Friday is the
    kind of small wrongness that costs trust in week one.
    """
    days_ahead = (4 - from_date.weekday()) % 7 or 7
    return from_date + timedelta(days=days_ahead)


def _plan(plan_key: str) -> dict:
    plans = load_config("customer_plans.json")["plans"]
    return next((p for p in plans if p["key"] == plan_key), plans[0])


def _render(
    heading: str,
    subject: str,
    paragraphs: list[str],
    text: str,
    contact_name: str | None = None,
    facts: list[tuple[str, str]] | None = None,
    action_url: str | None = None,
    action_label: str | None = None,
    closing: str | None = None,
    settings: Settings | None = None,
) -> RenderedEmail:
    settings = settings or get_settings()
    base = settings.site_url.rstrip("/")
    try:
        html = (
            _env()
            .get_template("transactional.html.j2")
            .render(
                heading=heading,
                contact_name=contact_name,
                paragraphs=paragraphs,
                facts=facts or [],
                action_url=action_url,
                action_label=action_label,
                closing=closing,
                manage_url=f"{base}/billing/manage",
                unsubscribe_url=f"{base}/unsubscribe",
            )
        )
    except Exception as exc:
        raise RenderFailureError(f"Transactional email failed to render: {exc}") from exc
    return RenderedEmail(subject=subject, html=html, text=text)


def render_onboarding(
    company: str,
    contact_name: str | None = None,
    plan_key: str = "founding_monthly",
    recipients: list[str] | None = None,
    first_feed: date | None = None,
    settings: Settings | None = None,
) -> RenderedEmail:
    """The single message a new customer receives on the day they subscribe.

    Confirmation that the payment went through, what happens next, and the
    exact Friday the first feed arrives — in one email rather than three.
    """
    plan = _plan(plan_key)
    friday = first_feed or next_friday(date.today())
    recipients = recipients or []
    return _render(
        heading="You're subscribed to LaunchTrace Food",
        subject=(
            f"You're subscribed to LaunchTrace Food — first feed Friday {friday.strftime('%-d %B')}"
        ),
        contact_name=contact_name,
        paragraphs=[
            (
                f"Thanks — {company} is set up on {plan['name']}, payment has gone through, "
                "and Stripe will email you the receipt."
            ),
            (
                f"Your first feed arrives on Friday {friday.strftime('%-d %B %Y')}, and every "
                "Friday after that. Each one is an email with the strongest new signals "
                "summarised and the full week attached as a CSV. It covers the trade mark "
                "journal published that week, so the brands in it are days old rather than "
                "months old — a quiet week means the week was quiet, not that anything is "
                "broken."
            ),
            (
                "There is no dashboard and no login, by design. Everything arrives in your "
                f"inbox. You can have up to {plan['max_recipients']} recipients on this plan — "
                "reply to this email with the addresses and I'll add them. Cancelling works "
                "the same way: reply to any LaunchTrace email, and it takes effect "
                "immediately with no notice period."
            ),
            (
                "One thing worth saying plainly: these are commercial signals, not confirmed "
                "purchase intent. Every entry tells you why it scored the way it did, so your "
                "team can disagree with any of it."
            ),
        ],
        facts=[
            ("Plan", plan["name"]),
            ("Price", f"£{plan['price_pence'] / 100:.0f} per month"),
            ("First feed", friday.strftime("%A %-d %B %Y")),
            ("Then", "Every Friday"),
            ("Recipients", ", ".join(recipients) if recipients else "to be confirmed"),
            ("Cancel", "Any time, effective immediately"),
        ],
        closing="If anything in the feed is not useful, tell me — that feedback is what shapes it.",
        text=(
            f"{company} is subscribed to LaunchTrace Food on {plan['name']} at "
            f"£{plan['price_pence'] / 100:.0f}/month, and the payment has gone through. "
            f"Your first weekly feed arrives on {friday.isoformat()}, then every Friday. "
            f"Reply to add up to {plan['max_recipients']} recipients, or to cancel — "
            "cancellation is immediate and there is no notice period."
        ),
        settings=settings,
    )


def render_payment_failed(
    company: str,
    grace_days: int = 14,
    contact_name: str | None = None,
    settings: Settings | None = None,
) -> RenderedEmail:
    settings = settings or get_settings()
    return _render(
        heading="Your last payment didn't go through",
        subject="LaunchTrace Food — payment problem",
        contact_name=contact_name,
        paragraphs=[
            f"The most recent payment for {company} was declined.",
            (
                "This is almost always an expired card. Stripe will retry automatically, and "
                "you can update the card from the billing portal."
            ),
            (
                f"The feed keeps arriving for {grace_days} days while this is sorted out. After "
                "that it pauses until payment succeeds — it is not cancelled, and nothing is lost."
            ),
        ],
        facts=[("Account", company), ("Feed continues for", f"{grace_days} days")],
        action_url=f"{settings.site_url.rstrip('/')}/billing/manage",
        action_label="Update payment details",
        text=(
            f"The last payment for {company} was declined. The feed continues for {grace_days} "
            "days, then pauses until payment succeeds. Update your card at "
            f"{settings.site_url.rstrip('/')}/billing/manage"
        ),
        settings=settings,
    )


def render_cancellation_confirmed(
    company: str,
    last_feed: date | None = None,
    contact_name: str | None = None,
    settings: Settings | None = None,
) -> RenderedEmail:
    return _render(
        heading="Your subscription has been cancelled",
        subject="LaunchTrace Food — subscription cancelled",
        contact_name=contact_name,
        paragraphs=[
            f"The LaunchTrace Food subscription for {company} is cancelled. You will not be billed again.",
            (
                "The feed stops with immediate effect. Everything already sent to you is yours "
                "to keep and use."
            ),
            (
                "If it stopped being useful, I would genuinely like to know why — one line is "
                "enough, and it is the most useful thing anyone can send me."
            ),
        ],
        facts=[
            ("Account", company),
            (
                "Last feed",
                last_feed.strftime("%-d %B %Y") if last_feed else "the most recent Friday",
            ),
            ("Further charges", "None"),
        ],
        closing="If you want it back on later, just reply — the same founding price will still apply.",
        text=(
            f"The LaunchTrace Food subscription for {company} is cancelled. No further charges, "
            "and the feed stops immediately."
        ),
        settings=settings,
    )


TRANSACTIONAL_KINDS = {
    "onboarding": render_onboarding,
    "payment_failed": render_payment_failed,
    "cancellation_confirmed": render_cancellation_confirmed,
}


__all__ = [
    "TRANSACTIONAL_KINDS",
    "next_friday",
    "render_cancellation_confirmed",
    "render_onboarding",
    "render_payment_failed",
]
