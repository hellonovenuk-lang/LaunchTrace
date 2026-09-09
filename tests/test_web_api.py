"""The JSON API a replacement front end builds against.

The contract these tests protect is documented in ``docs/WEBSITE_INTEGRATION.md``.
If a test here changes, that document changes with it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.db.tables import LeadFeedback, SampleRequest, SuppressionRule


@pytest.fixture
def client(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """An application on an isolated database with no credentials configured.

    The database URL goes through the environment rather than a patched
    accessor, so every module that reads settings — including the engine —
    sees the temporary database and no test can touch the real one.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.sqlite'}")
    monkeypatch.setenv("SITE_URL", "https://launchtrace.test")
    monkeypatch.setenv("SEND_MODE", "review")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    from src.settings import get_settings

    get_settings.cache_clear()
    from src.web.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _session(client: TestClient):  # type: ignore[no-untyped-def]
    """A session on the same database the application under test is using."""
    from src.db import get_session
    from src.settings import get_settings

    return get_session(get_settings().database_url)


class TestConfigEndpoint:
    def test_it_describes_what_a_front_end_needs(self, client):
        body = client.get("/api/config").json()
        assert body["ok"] is True
        data = body["data"]
        assert data["site_url"]
        assert data["supplier_types"]
        assert data["legal"]["privacy"] == "/privacy"
        assert "Open Government Licence" in data["attribution"]

    def test_it_says_whether_checkout_actually_works(self, client):
        data = client.get("/api/config").json()["data"]
        assert data["checkout_available"] is False, "no Stripe key is configured in tests"

    def test_plans_never_leak_stripe_configuration(self, client):
        """A price id or an env-var name has no business reaching a browser."""
        for plan in client.get("/api/config").json()["data"]["plans"]:
            assert "stripe_price_id_env" not in plan
            for value in plan.values():
                assert not (isinstance(value, str) and value.startswith("price_"))
                assert not (isinstance(value, str) and value.startswith("STRIPE_"))
            assert plan["price_pence"] > 0

    def test_health_is_available_without_a_database_write(self, client):
        assert client.get("/api/health").json()["ok"] is True


class TestSampleRequests:
    def test_a_valid_request_is_recorded(self, client):
        response = client.post(
            "/api/sample-request",
            json={"work_email": "buyer@pouchworks.test", "company": "Pouchworks Ltd"},
        )
        assert response.status_code == 200
        assert response.json()["code"] == "accepted"

        session = _session(client)
        try:
            stored = session.query(SampleRequest).one()
            assert stored.work_email == "buyer@pouchworks.test"
            assert stored.company == "Pouchworks Ltd"
        finally:
            session.close()

    def test_a_bad_address_is_rejected_with_a_usable_code(self, client):
        response = client.post(
            "/api/sample-request", json={"work_email": "not-an-email", "company": "X Ltd"}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_email"

    def test_a_missing_company_is_rejected(self, client):
        response = client.post(
            "/api/sample-request", json={"work_email": "a@b.test", "company": "   "}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "missing_company"

    def test_the_honeypot_stores_nothing_and_says_nothing(self, client):
        response = client.post(
            "/api/sample-request",
            json={
                "work_email": "bot@spam.test",
                "company": "Spam Ltd",
                "website_url": "http://spam.test",
            },
        )
        assert response.json()["ok"] is True, "a bot must learn nothing from the response"

        session = _session(client)
        try:
            assert session.query(SampleRequest).count() == 0
        finally:
            session.close()

    def test_asking_twice_is_not_an_error(self, client):
        payload = {"work_email": "buyer@pouchworks.test", "company": "Pouchworks Ltd"}
        client.post("/api/sample-request", json=payload)
        second = client.post("/api/sample-request", json=payload)
        assert second.json()["code"] == "already_requested"
        assert second.json()["ok"] is True

    def test_repeated_submissions_are_rate_limited(self, client):
        for index in range(6):
            response = client.post(
                "/api/sample-request",
                json={"work_email": f"buyer{index}@pouchworks.test", "company": "Pouchworks"},
            )
        assert response.status_code == 429
        assert response.json()["code"] == "rate_limited"


class TestOptOut:
    def test_an_opt_out_is_recorded_as_a_suppression(self, client):
        response = client.post("/api/opt-out", json={"email": "stop@pouchworks.test"})
        assert response.json()["code"] == "opt_out_recorded"

        session = _session(client)
        try:
            rule = session.query(SuppressionRule).filter_by(rule_type="email").one()
            assert rule.value == "stop@pouchworks.test"
            assert rule.active is True
        finally:
            session.close()

    def test_the_response_does_not_reveal_whether_the_address_was_known(self, client):
        first = client.post("/api/opt-out", json={"email": "unknown@nowhere.test"}).json()
        second = client.post("/api/opt-out", json={"email": "unknown@nowhere.test"}).json()
        assert first == second


class TestCheckout:
    def test_checkout_returns_a_url_even_without_stripe(self, client):
        body = client.post("/api/checkout?plan=founding_monthly").json()
        assert body["ok"] is True
        assert body["code"] == "checkout_stub"
        assert body["data"]["live"] is False
        assert body["data"]["url"]

    def test_an_unknown_plan_is_a_clean_404(self, client):
        response = client.post("/api/checkout?plan=nonexistent")
        assert response.status_code == 404
        assert response.json()["code"] == "unknown_plan"


class TestFeedbackEndpoint:
    def test_feedback_is_recorded(self, client):
        response = client.post(
            "/api/feedback",
            json={"state": "USEFUL", "trademark_number": "UK00003900001", "note": "Called them"},
        )
        assert response.json()["code"] == "feedback_recorded"

        session = _session(client)
        try:
            stored = session.query(LeadFeedback).one()
            assert stored.state == "USEFUL"
            assert stored.source == "form"
        finally:
            session.close()

    def test_an_unknown_state_is_rejected_and_the_options_are_listed(self, client):
        response = client.post("/api/feedback", json={"state": "BRILLIANT"})
        assert response.status_code == 400
        assert "USEFUL" in response.json()["data"]["allowed"]

    def test_the_form_page_renders_for_a_customer_to_use(self, client):
        page = client.get("/feedback?tm=UK00003900001")
        assert page.status_code == 200
        assert "UK00003900001" in page.text
        assert "Was this lead useful" in page.text


class TestHtmlAndApiAgree:
    """The two front doors must behave identically, because they share code."""

    def test_both_paths_record_the_same_sample_request(self, client):
        client.post(
            "/api/sample-request",
            json={"work_email": "one@pouchworks.test", "company": "Pouchworks"},
        )
        client.post(
            "/sample",
            data={"work_email": "two@pouchworks.test", "company": "Pouchworks"},
        )
        session = _session(client)
        try:
            addresses = {row.work_email for row in session.query(SampleRequest).all()}
            assert addresses == {"one@pouchworks.test", "two@pouchworks.test"}
        finally:
            session.close()

    def test_both_paths_record_the_same_opt_out(self, client):
        client.post("/api/opt-out", json={"email": "api@pouchworks.test"})
        client.post("/unsubscribe", data={"email": "form@pouchworks.test"})
        session = _session(client)
        try:
            values = {row.value for row in session.query(SuppressionRule).all()}
            assert values == {"api@pouchworks.test", "form@pouchworks.test"}
        finally:
            session.close()
