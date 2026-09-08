"""The public site: landing page, sample form, abuse handling and admin auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.db.tables import SampleRequest, SuppressionRule
from src.web.security import (
    RateLimiter,
    clean_text,
    constant_time_equals,
    hash_ip,
    is_freemail,
    is_valid_work_email,
)


@pytest.fixture
def client(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'web.sqlite'}")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("SITE_URL", "https://launchtrace.test")
    from src.settings import get_settings

    get_settings.cache_clear()
    from src.web.app import create_app

    app = create_app()
    yield TestClient(app)
    get_settings.cache_clear()


class TestLandingPage:
    def test_renders(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Find emerging food brands while they" in response.text

    def test_states_the_founding_price(self, client):
        assert "£79" in client.get("/").text

    def test_attributes_its_data_sources(self, client):
        text = client.get("/").text
        assert "Intellectual Property Office" in text
        assert "Companies House" in text
        assert "Open Government Licence" in text

    def test_disclaims_legal_advice(self, client):
        assert "not trade mark legal advice" in client.get("/").text

    def test_does_not_claim_purchasing_intent(self, client):
        text = client.get("/").text
        assert "inference about likely need" in text

    def test_makes_no_testimonial_or_customer_count_claim(self, client):
        text = client.get("/").text.lower()
        for phrase in ("customers trust", "trusted by", "join thousands", "5 stars", "testimonial"):
            assert phrase not in text

    def test_sets_security_headers(self, client):
        headers = client.get("/").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in headers

    def test_health_endpoint(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}


class TestSampleRequest:
    def test_accepts_a_business_request(self, client):
        response = client.post(
            "/sample",
            data={
                "work_email": "buyer@packco.co.uk",
                "company": "PackCo Ltd",
                "contact_name": "Sam Reed",
                "supplier_type": "flexible_packaging",
                "website_url": "",
            },
        )
        assert response.status_code == 200
        assert "Thanks" in response.text
        from src.db import session_scope

        with session_scope() as session:
            row = session.execute(
                select(SampleRequest).where(SampleRequest.work_email == "buyer@packco.co.uk")
            ).scalar_one()
        assert row.company == "PackCo Ltd"
        assert row.supplier_type == "flexible_packaging"
        assert row.status == "new"

    def test_rejects_an_invalid_address(self, client):
        response = client.post(
            "/sample", data={"work_email": "not-an-email", "company": "X", "website_url": ""}
        )
        assert "valid email" in response.text

    def test_rejects_a_disposable_address(self, client):
        response = client.post(
            "/sample",
            data={"work_email": "x@mailinator.com", "company": "X", "website_url": ""},
        )
        assert "company email address" in response.text

    def test_honeypot_submission_is_silently_discarded(self, client):
        client.post(
            "/sample",
            data={
                "work_email": "bot@spam.test",
                "company": "Spam",
                "website_url": "http://spam.test",
            },
        )
        from src.db import session_scope

        with session_scope() as session:
            rows = list(session.execute(select(SampleRequest)).scalars())
        assert not any(r.work_email == "bot@spam.test" for r in rows)

    def test_duplicate_request_does_not_create_a_second_row(self, client):
        data = {"work_email": "buyer@packco.co.uk", "company": "PackCo Ltd", "website_url": ""}
        client.post("/sample", data=data)
        client.post("/sample", data=data)
        from src.db import session_scope

        with session_scope() as session:
            rows = [
                r
                for r in session.execute(select(SampleRequest)).scalars()
                if r.work_email == "buyer@packco.co.uk"
            ]
        assert len(rows) == 1

    def test_free_email_is_accepted_but_flagged_for_review(self, client):
        client.post(
            "/sample", data={"work_email": "someone@gmail.com", "company": "X", "website_url": ""}
        )
        from src.db import session_scope

        with session_scope() as session:
            row = session.execute(
                select(SampleRequest).where(SampleRequest.work_email == "someone@gmail.com")
            ).scalar_one()
        assert row.status == "review_freemail"

    def test_ip_address_is_stored_only_as_a_hash(self, client):
        client.post(
            "/sample", data={"work_email": "buyer2@packco.co.uk", "company": "X", "website_url": ""}
        )
        from src.db import session_scope

        with session_scope() as session:
            row = session.execute(
                select(SampleRequest).where(SampleRequest.work_email == "buyer2@packco.co.uk")
            ).scalar_one()
        assert row.source_ip_hash
        assert "." not in row.source_ip_hash


class TestUnsubscribe:
    def test_records_a_suppression(self, client):
        response = client.post("/unsubscribe", data={"email": "someone@packco.co.uk"})
        assert response.status_code == 200
        from src.db import session_scope

        with session_scope() as session:
            row = session.execute(
                select(SuppressionRule).where(SuppressionRule.value == "someone@packco.co.uk")
            ).scalar_one()
        assert row.active is True
        assert row.rule_type == "email"


class TestBilling:
    def test_checkout_redirects_to_the_not_connected_page_without_stripe(self, client):
        response = client.get("/billing/checkout?plan=founding_monthly", follow_redirects=False)
        assert response.status_code == 303
        assert "not-connected" in response.headers["location"]

    def test_unknown_plan_is_404(self, client):
        assert client.get("/billing/checkout?plan=nope").status_code == 404

    def test_webhook_without_a_signature_is_rejected(self, client):
        assert client.post("/billing/webhook", content=b"{}").status_code == 400

    def test_webhook_with_a_bad_signature_is_rejected(self, client):
        response = client.post(
            "/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=bogus"}
        )
        assert response.status_code == 400


class TestAdmin:
    def test_requires_a_token(self, client):
        assert client.get("/admin").status_code == 401

    def test_wrong_token_is_rejected(self, client):
        assert client.get("/admin?token=wrong").status_code == 401

    def test_correct_token_is_accepted(self, client):
        response = client.get("/admin?token=test-admin-token")
        assert response.status_code == 200
        assert "Operator" in response.text

    def test_token_may_be_supplied_in_a_header(self, client):
        response = client.get("/admin", headers={"x-admin-token": "test-admin-token"})
        assert response.status_code == 200


class TestLegalPages:
    @pytest.mark.parametrize("path", ["/privacy", "/terms", "/attribution"])
    def test_pages_render(self, client, path):
        assert client.get(path).status_code == 200


class TestSecurityHelpers:
    def test_email_validation(self):
        assert is_valid_work_email("a@b.co.uk")[0] is True
        assert is_valid_work_email("nope")[0] is False
        assert is_valid_work_email("")[0] is False

    def test_freemail_detection(self):
        assert is_freemail("a@gmail.com") is True
        assert is_freemail("a@packco.co.uk") is False

    def test_clean_text_strips_control_characters_and_clamps(self):
        assert clean_text("Hello\x00World") == "HelloWorld"
        assert len(clean_text("x" * 500, max_length=10)) == 10

    def test_ip_hash_is_not_reversible_to_the_address(self):
        digest = hash_ip("203.0.113.5")
        assert digest and "203" not in digest

    def test_rate_limiter_blocks_after_the_limit(self):
        limiter = RateLimiter(limit=2, window_seconds=60)
        assert limiter.allow("k") and limiter.allow("k")
        assert limiter.allow("k") is False

    def test_rate_limiter_is_per_key(self):
        limiter = RateLimiter(limit=1, window_seconds=60)
        assert limiter.allow("a") and limiter.allow("b")

    def test_token_comparison_is_constant_time(self):
        assert constant_time_equals("abc", "abc") is True
        assert constant_time_equals("abc", "abd") is False
