"""Website business logic, with no HTML in it.

Everything the site *does* — accepting a sample request, opening a checkout,
recording an opt-out, recording feedback — lives here as plain functions over a
database session. The FastAPI layer in ``src/web/app.py`` renders the current
pages, and ``src/web/api.py`` exposes the same operations as JSON.

The point is that the visual front end is replaceable. A new site can call the
JSON API and get identical behaviour, including the same validation and the
same protections, without any of this being reimplemented or subtly changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.billing.stripe_client import get_billing, load_plans
from src.db.repository import add_suppression
from src.db.tables import CustomerPreference, SampleRequest
from src.logging_setup import get_logger
from src.settings import Settings, get_settings, load_config
from src.web.security import clean_text, hash_ip, is_freemail, is_valid_work_email

log = get_logger(__name__)


@dataclass
class ServiceResult:
    """One outcome, shaped so both HTML and JSON can render it.

    ``code`` is the stable identifier a front end should branch on; ``message``
    is the sentence a person reads. Front ends may replace the message; they
    must not invent codes.
    """

    ok: bool
    code: str
    message: str
    status_code: int = 200
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# sample requests
# ---------------------------------------------------------------------------


def supplier_types() -> list[dict[str, str]]:
    return list(load_config("customer_plans.json")["supplier_types"])


def submit_sample_request(
    session: Session,
    work_email: str,
    company: str,
    contact_name: str = "",
    supplier_type: str = "",
    honeypot: str = "",
    client_ip: str | None = None,
) -> ServiceResult:
    """Record a request for the weekly sample.

    Validation order matters and is deliberate: the honeypot answers as though
    accepted so a bot learns nothing, and a repeat request is not an error
    because telling someone their address is already on file is unhelpful.
    """
    if honeypot.strip():
        log.info("sample.honeypot_triggered")
        return ServiceResult(
            ok=True,
            code="accepted",
            message="Thanks — we'll be in touch shortly.",
        )

    email = clean_text(work_email, 254).lower()
    valid, error = is_valid_work_email(email)
    if not valid:
        return ServiceResult(
            ok=False,
            code="invalid_email",
            message=error or "Please check your email address.",
            status_code=400,
        )

    company_clean = clean_text(company, 200)
    if not company_clean:
        return ServiceResult(
            ok=False,
            code="missing_company",
            message="Please tell us your company name.",
            status_code=400,
        )

    existing = session.execute(
        select(SampleRequest).where(SampleRequest.work_email == email)
    ).scalar_one_or_none()
    if existing is not None:
        return ServiceResult(
            ok=True,
            code="already_requested",
            message=(
                "You've already requested a sample — it's on its way. Reply to that "
                "email if it hasn't arrived."
            ),
            data={"request_id": existing.id},
        )

    request = SampleRequest(
        work_email=email,
        company=company_clean,
        contact_name=clean_text(contact_name, 120) or None,
        supplier_type=clean_text(supplier_type, 40) or None,
        source_ip_hash=hash_ip(client_ip),
        status="new" if not is_freemail(email) else "review_freemail",
    )
    session.add(request)
    session.flush()
    log.info("sample.requested", company=company_clean, freemail=is_freemail(email))
    return ServiceResult(
        ok=True,
        code="accepted",
        message="Thanks — we'll send the most recent sample to that address shortly.",
        data={"request_id": request.id, "status": request.status},
    )


# ---------------------------------------------------------------------------
# opt-out
# ---------------------------------------------------------------------------


def record_opt_out(session: Session, email: str, source: str = "website") -> ServiceResult:
    """Stop all email to one address, and stop it everywhere.

    Suppression is recorded *and* any delivery recipient rows are removed, so a
    paying customer's opt-out takes effect on the next run rather than the next
    time someone remembers.

    The response is identical for a valid address whether or not it was on any
    list, so this endpoint cannot be used to test whether an address is known.
    """
    address = clean_text(email, 254).lower()
    valid, _ = is_valid_work_email(address)
    if valid:
        add_suppression(session, "email", address, "self-service opt-out", source)
        for pref in session.execute(
            select(CustomerPreference).where(CustomerPreference.recipient_email == address)
        ).scalars():
            session.delete(pref)
        session.flush()
        log.info("unsubscribe.recorded", source=source)
    return ServiceResult(
        ok=True,
        code="opt_out_recorded",
        message="That address will not receive further LaunchTrace email.",
    )


# ---------------------------------------------------------------------------
# billing
# ---------------------------------------------------------------------------


def plan_summary(settings: Settings | None = None) -> list[dict[str, Any]]:
    """The plans a front end may display. Never exposes a Stripe price id."""
    settings = settings or get_settings()
    return [
        {
            "key": plan.key,
            "name": plan.name,
            "price_pence": plan.price_pence,
            "price_display": plan.price_display,
            "interval": plan.interval,
            "max_recipients": plan.max_recipients,
            "features": plan.features,
            "offered_publicly": plan.offered_publicly,
        }
        for plan in load_plans().values()
    ]


def start_checkout(
    plan_key: str = "founding_monthly",
    email: str | None = None,
    settings: Settings | None = None,
) -> ServiceResult:
    """Open a Stripe checkout session, or explain why it is not available.

    Without a Stripe key this returns the stub URL rather than an error, so a
    replacement front end can build and test the whole flow before the account
    exists.
    """
    settings = settings or get_settings()
    plans = load_plans()
    if plan_key not in plans:
        return ServiceResult(
            ok=False, code="unknown_plan", message="Unknown plan.", status_code=404
        )
    billing = get_billing(settings)
    checkout = billing.create_checkout_session(plan_key, email=email)
    return ServiceResult(
        ok=True,
        code="checkout_ready" if billing.live else "checkout_stub",
        message=(
            "Redirect the customer to the checkout URL."
            if billing.live
            else "Stripe is not connected, so this is the local placeholder URL."
        ),
        data={"url": checkout.url, "live": billing.live, "plan": plan_key},
    )


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------


def submit_lead_feedback(
    session: Session,
    state: str,
    trademark_number: str = "",
    email: str = "",
    note: str = "",
) -> ServiceResult:
    """Record what a customer thought of one delivered lead.

    Identified by the recipient address from their own feed email, which is the
    only identifier a customer has — there is no login, and adding one for this
    would be a worse trade than accepting a weaker identification.
    """
    from src.sales.feedback import FeedbackState, record_feedback

    try:
        resolved = FeedbackState(state.strip().upper())
    except ValueError:
        return ServiceResult(
            ok=False,
            code="unknown_state",
            message="Unknown feedback option.",
            status_code=400,
            data={"allowed": [s.value for s in FeedbackState]},
        )

    customer_id = None
    address = clean_text(email, 254).lower()
    if address:
        pref = (
            session.execute(
                select(CustomerPreference).where(CustomerPreference.recipient_email == address)
            )
            .scalars()
            .first()
        )
        if pref:
            customer_id = pref.customer_id

    record_feedback(
        session,
        state=resolved,
        trademark_number=clean_text(trademark_number, 32) or None,
        customer_id=customer_id,
        note=clean_text(note, 500) or None,
        source="form",
    )
    return ServiceResult(
        ok=True,
        code="feedback_recorded",
        message="Thank you — that is genuinely useful.",
        data={"state": resolved.value, "attributed": customer_id is not None},
    )


__all__ = [
    "ServiceResult",
    "plan_summary",
    "record_opt_out",
    "start_checkout",
    "submit_lead_feedback",
    "submit_sample_request",
    "supplier_types",
]
