"""JSON API for the website.

The contract a replacement front end builds against. Every endpoint here is a
thin wrapper over ``src/web/services.py``, so the JSON API and the current HTML
pages cannot drift apart in behaviour — they call the same function.

Documented in ``docs/WEBSITE_INTEGRATION.md``. Treat that document and this
module as one thing: change both together.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.settings import get_settings
from src.web.security import RateLimiter
from src.web.services import (
    ServiceResult,
    plan_summary,
    record_opt_out,
    start_checkout,
    submit_lead_feedback,
    submit_sample_request,
    supplier_types,
)


class SampleRequestBody(BaseModel):
    work_email: str = Field(max_length=254)
    company: str = Field(max_length=200)
    contact_name: str = Field(default="", max_length=120)
    supplier_type: str = Field(default="", max_length=40)
    # Must stay empty. A value here means an automated submission.
    website_url: str = Field(default="", max_length=200)


class OptOutBody(BaseModel):
    email: str = Field(max_length=254)


class FeedbackBody(BaseModel):
    state: str = Field(max_length=32)
    trademark_number: str = Field(default="", max_length=32)
    email: str = Field(default="", max_length=254)
    note: str = Field(default="", max_length=500)


def _respond(result: ServiceResult) -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "code": result.code,
        "message": result.message,
    }
    if result.data:
        payload["data"] = result.data
    return JSONResponse(payload, status_code=result.status_code)


def build_api_router(db_session_dependency: Any) -> APIRouter:
    """The ``/api`` router.

    The session dependency is injected rather than imported so tests and any
    future deployment can supply their own without this module knowing how the
    application is wired.
    """
    router = APIRouter(prefix="/api", tags=["public"])
    # Per-router, so each application instance starts with a clean window.
    sample_limiter = RateLimiter(limit=5, window_seconds=3600)
    feedback_limiter = RateLimiter(limit=30, window_seconds=3600)

    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @router.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "service": "launchtrace"})

    @router.get("/config")
    def config() -> JSONResponse:
        """What a front end needs to render itself correctly.

        Deliberately says which integrations are live, so a replacement site
        can show a sample form and hide a broken checkout button rather than
        sending someone to a page that cannot work.
        """
        settings = get_settings()
        return JSONResponse(
            {
                "ok": True,
                "data": {
                    "site_url": settings.site_url,
                    "plans": plan_summary(settings),
                    "supplier_types": supplier_types(),
                    "checkout_available": settings.stripe_enabled,
                    "sample_requests_open": True,
                    "attribution": (
                        "UK Intellectual Property Office and Companies House data, "
                        "Open Government Licence v3.0"
                    ),
                    "legal": {
                        "privacy": "/privacy",
                        "terms": "/terms",
                        "attribution": "/attribution",
                    },
                },
            }
        )

    @router.post("/sample-request")
    def sample_request(
        body: SampleRequestBody,
        request: Request,
        session: Session = Depends(db_session_dependency),
    ) -> JSONResponse:
        if not sample_limiter.allow(_client_ip(request)):
            return _respond(
                ServiceResult(
                    ok=False,
                    code="rate_limited",
                    message="Too many requests from this address. Please try again later.",
                    status_code=429,
                )
            )
        return _respond(
            submit_sample_request(
                session,
                work_email=body.work_email,
                company=body.company,
                contact_name=body.contact_name,
                supplier_type=body.supplier_type,
                honeypot=body.website_url,
                client_ip=_client_ip(request),
            )
        )

    @router.post("/opt-out")
    def opt_out(
        body: OptOutBody, session: Session = Depends(db_session_dependency)
    ) -> JSONResponse:
        return _respond(record_opt_out(session, body.email, source="api"))

    @router.post("/checkout")
    def checkout(plan: str = "founding_monthly", email: str | None = None) -> JSONResponse:
        return _respond(start_checkout(plan, email=email))

    @router.post("/feedback")
    def feedback(
        body: FeedbackBody,
        request: Request,
        session: Session = Depends(db_session_dependency),
    ) -> JSONResponse:
        if not feedback_limiter.allow(_client_ip(request)):
            return _respond(
                ServiceResult(
                    ok=False,
                    code="rate_limited",
                    message="Too many submissions. Please try again later.",
                    status_code=429,
                )
            )
        return _respond(
            submit_lead_feedback(
                session,
                state=body.state,
                trademark_number=body.trademark_number,
                email=body.email,
                note=body.note,
            )
        )

    return router


__all__ = ["build_api_router"]
