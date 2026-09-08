"""LaunchTrace website.

One small FastAPI application serving:

* the marketing landing page and its sample-request form
* Stripe checkout, webhook and success/cancel pages
* a token-protected operator view

There is deliberately no customer dashboard.  The product is a Friday email and
a CSV.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.billing.stripe_client import get_billing, load_plans
from src.billing.webhooks import WebhookProcessor
from src.db import get_session, init_db
from src.db.repository import add_suppression
from src.db.tables import (
    Customer,
    CustomerPreference,
    ErrorLog,
    OpportunityRow,
    PipelineRun,
    SampleRequest,
)
from src.logging_setup import configure_logging, get_logger
from src.settings import REPORTS_DIR, get_settings, load_config
from src.web.security import (
    RateLimiter,
    clean_text,
    constant_time_equals,
    hash_ip,
    is_freemail,
    is_valid_work_email,
)

log = get_logger(__name__)
BASE_DIR = Path(__file__).parent
SAMPLE_LIMITER = RateLimiter(limit=5, window_seconds=3600)


def db_session() -> Any:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    init_db()

    app = FastAPI(title="LaunchTrace", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    plans = load_plans()
    billing = get_billing(settings)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; img-src 'self' data:; form-action 'self' https://checkout.stripe.com; frame-ancestors 'none'",
        )
        return response

    # -- public ------------------------------------------------------------
    def _example_opportunities(session: Session, limit: int = 3) -> list[dict[str, Any]]:
        rows = list(
            session.execute(
                select(OpportunityRow)
                .where(OpportunityRow.score_band.in_(["HIGH", "MEDIUM"]))
                .order_by(OpportunityRow.launchtrace_score.desc())
                .limit(limit)
            ).scalars()
        )
        return [
            {
                "brand_name": r.brand_name,
                "company_name": r.company_name,
                "applicant_name": r.applicant_name,
                "company_region": r.company_region,
                "product_category": (r.product_category or "").replace("_", " ").title() or None,
                "launchtrace_score": r.launchtrace_score,
                "score_band": r.score_band,
                "first_reason": (r.score_reasons or ["Scored from public trade mark and company data"])[0],
            }
            for r in rows
        ]

    def _render_index(
        request: Request,
        session: Session,
        message: str | None = None,
        success: bool = True,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "founding": plans["founding_monthly"],
                "supplier_types": load_config("customer_plans.json")["supplier_types"],
                "examples": _example_opportunities(session),
                "message": message,
                "success": success,
                "stripe_live": billing.live,
                "form_token": "launchtrace",
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, session: Session = Depends(db_session)) -> HTMLResponse:
        return _render_index(request, session)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.post("/sample", response_class=HTMLResponse)
    def request_sample(
        request: Request,
        work_email: str = Form(...),
        company: str = Form(...),
        contact_name: str = Form(""),
        supplier_type: str = Form(""),
        website_url: str = Form(""),  # honeypot
        session: Session = Depends(db_session),
    ) -> HTMLResponse:
        client_ip = request.client.host if request.client else None

        if website_url.strip():
            # Honeypot filled: a bot. Answer as if accepted, store nothing.
            log.info("sample.honeypot_triggered")
            return _render_index(request, session, "Thanks — we'll be in touch shortly.", True)

        if not SAMPLE_LIMITER.allow(client_ip or "unknown"):
            return _render_index(
                request, session, "Too many requests from this address. Please try again later.", False
            )

        email = clean_text(work_email, 254).lower()
        valid, error = is_valid_work_email(email)
        if not valid:
            return _render_index(request, session, error or "Please check your email address.", False)

        company_clean = clean_text(company, 200)
        if not company_clean:
            return _render_index(request, session, "Please tell us your company name.", False)

        existing = session.execute(
            select(SampleRequest).where(SampleRequest.work_email == email)
        ).scalar_one_or_none()
        if existing is not None:
            return _render_index(
                request,
                session,
                "You've already requested a sample — it's on its way. Reply to that email if it hasn't arrived.",
                True,
            )

        session.add(
            SampleRequest(
                work_email=email,
                company=company_clean,
                contact_name=clean_text(contact_name, 120) or None,
                supplier_type=clean_text(supplier_type, 40) or None,
                source_ip_hash=hash_ip(client_ip),
                status="new" if not is_freemail(email) else "review_freemail",
            )
        )
        log.info("sample.requested", company=company_clean, freemail=is_freemail(email))
        return _render_index(
            request,
            session,
            "Thanks — we'll send the most recent sample to that address shortly.",
            True,
        )

    @app.get("/unsubscribe", response_class=HTMLResponse)
    def unsubscribe_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="simple.html",
            context={
                "heading": "Opt out",
                "back": True,
                "body_html": """
                <p>Enter the email address you'd like us to stop contacting. We keep a record of the
                address purely so we do not contact it again.</p>
                <form class="sample" method="post" action="/unsubscribe" style="margin-top:16px">
                  <label for="email">Email address</label>
                  <input id="email" name="email" type="email" required>
                  <button class="btn btn-primary" type="submit">Opt out</button>
                </form>
                """,
            },
        )

    @app.post("/unsubscribe", response_class=HTMLResponse)
    def unsubscribe(
        request: Request, email: str = Form(...), session: Session = Depends(db_session)
    ) -> HTMLResponse:
        address = clean_text(email, 254).lower()
        valid, _ = is_valid_work_email(address)
        if valid:
            add_suppression(session, "email", address, "self-service opt-out", "website")
            for pref in session.execute(
                select(CustomerPreference).where(CustomerPreference.recipient_email == address)
            ).scalars():
                session.delete(pref)
            log.info("unsubscribe.recorded")
        return templates.TemplateResponse(
            request=request,
            name="simple.html",
            context={
                "heading": "Opt out recorded",
                "back": True,
                "flash": "That address will not receive further LaunchTrace email.",
                "body_html": "<p>If you also have a paid subscription, cancel it from the link in your "
                "welcome email or by replying to any LaunchTrace message.</p>",
            },
        )

    # -- legal / attribution ----------------------------------------------
    def _doc_page(request: Request, heading: str, filename: str) -> HTMLResponse:
        path = Path("docs") / filename
        body = path.read_text(encoding="utf-8") if path.exists() else "Document not available."
        html = _markdown_to_html(body)
        return templates.TemplateResponse(
            request=request,
            name="simple.html",
            context={"heading": heading, "body_html": html, "back": True},
        )

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> HTMLResponse:
        return _doc_page(request, "Privacy notice", "PRIVACY.md")

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request) -> HTMLResponse:
        return _doc_page(request, "Terms of service", "TERMS.md")

    @app.get("/attribution", response_class=HTMLResponse)
    def attribution(request: Request) -> HTMLResponse:
        return _doc_page(request, "Data sources and licensing", "ATTRIBUTION.md")

    # -- billing -----------------------------------------------------------
    @app.get("/billing/checkout")
    def checkout(plan: str = "founding_monthly", email: str | None = None) -> RedirectResponse:
        if plan not in plans:
            raise HTTPException(status_code=404, detail="Unknown plan")
        session = billing.create_checkout_session(plan, email=email)
        return RedirectResponse(session.url, status_code=303)

    @app.get("/billing/success", response_class=HTMLResponse)
    def billing_success(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="simple.html",
            context={
                "heading": "You're subscribed",
                "back": True,
                "flash": "Subscription confirmed.",
                "body_html": "<p>Your first LaunchTrace Food feed arrives on the next Friday run. "
                "Reply to the welcome email to add up to two more recipients from your organisation.</p>",
            },
        )

    @app.get("/billing/cancelled", response_class=HTMLResponse)
    def billing_cancelled(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="simple.html",
            context={
                "heading": "Checkout cancelled",
                "back": True,
                "body_html": "<p>Nothing was charged. If you'd rather see a sample first, "
                '<a href="/#sample">request one here</a>.</p>',
            },
        )

    @app.get("/billing/not-connected", response_class=HTMLResponse)
    def billing_not_connected(request: Request, plan: str = "founding_monthly") -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="simple.html",
            context={
                "heading": "Checkout not available yet",
                "back": True,
                "flash": "Stripe is not connected on this deployment.",
                "flash_kind": "err",
                "body_html": "<p>Subscriptions open as soon as the Stripe account is connected. "
                'In the meantime, <a href="/#sample">request a sample</a> and we\'ll send you the '
                "subscription link directly.</p>",
            },
        )

    @app.get("/billing/manage", response_class=HTMLResponse)
    def billing_manage(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="simple.html",
            context={
                "heading": "Manage your subscription",
                "back": True,
                "body_html": "<p>Reply to any LaunchTrace email to change recipients, pause the feed "
                "or cancel. Cancellation takes effect immediately and there is no notice period.</p>"
                '<p>To stop all email including sample follow-ups, <a href="/unsubscribe">opt out here</a>.</p>',
            },
        )

    @app.post("/billing/webhook")
    async def stripe_webhook(
        request: Request, session: Session = Depends(db_session)
    ) -> JSONResponse:
        payload = await request.body()
        signature = request.headers.get("stripe-signature", "")
        if not signature:
            raise HTTPException(status_code=400, detail="Missing Stripe signature")
        try:
            event = billing.verify_webhook(payload, signature)
        except Exception as exc:
            log.warning("stripe.webhook.invalid_signature", error=str(exc)[:200])
            raise HTTPException(status_code=400, detail="Invalid signature") from exc
        outcome = WebhookProcessor(session).process(event)
        log.info("stripe.webhook.processed", type=event.get("type"), action=outcome.action)
        return JSONResponse({"received": True, "action": outcome.action})

    # -- operator ----------------------------------------------------------
    def require_admin(request: Request) -> None:
        token = settings.admin_token
        if not token:
            raise HTTPException(
                status_code=503,
                detail="ADMIN_TOKEN is not set, so the operator view is disabled.",
            )
        supplied = request.query_params.get("token") or request.headers.get("x-admin-token", "")
        if not constant_time_equals(supplied, token):
            raise HTTPException(status_code=401, detail="Unauthorised")

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, session: Session = Depends(db_session)) -> HTMLResponse:
        require_admin(request)
        runs = list(
            session.execute(
                select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(20)
            ).scalars()
        )
        customers = list(session.execute(select(Customer)).scalars())
        recipients: dict[int, list[str]] = {}
        for pref in session.execute(select(CustomerPreference)).scalars():
            recipients.setdefault(pref.customer_id, []).append(pref.recipient_email)
        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context={
                "runs": runs,
                "customers": customers,
                "recipients": recipients,
                "sample_requests": list(
                    session.execute(
                        select(SampleRequest).order_by(SampleRequest.created_at.desc()).limit(30)
                    ).scalars()
                ),
                "errors": list(
                    session.execute(
                        select(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(15)
                    ).scalars()
                ),
                "send_mode": settings.send_mode,
            },
        )

    @app.get("/admin/run/{journal_number}")
    def admin_run(request: Request, journal_number: str) -> JSONResponse:
        require_admin(request)
        qa = REPORTS_DIR / "runs" / journal_number / "qa_report.json"
        if not qa.exists():
            raise HTTPException(status_code=404, detail="No QA report for that journal")
        return JSONResponse(json.loads(qa.read_text(encoding="utf-8")))

    return app


def _markdown_to_html(text: str) -> str:
    """Minimal Markdown rendering for the three static legal documents.

    Deliberately not a general Markdown engine: these files are ours, and the
    site should not carry a parser dependency for three pages.
    """
    import html as html_lib

    out: list[str] = []
    in_list = False
    for raw in text.splitlines():
        line = raw.rstrip()
        escaped = html_lib.escape(line)
        if line.startswith("### "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{escaped[4:]}</h3>")
        elif line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2 style='font-size:18px;color:var(--ink);margin-top:24px'>{escaped[3:]}</h2>")
        elif line.startswith("# "):
            continue  # the page heading already carries the title
        elif line.startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{escaped[2:]}</li>")
        elif not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{escaped}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


app = create_app()
