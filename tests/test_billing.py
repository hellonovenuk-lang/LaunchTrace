"""Stripe subscription state transitions and webhook idempotency."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.billing.stripe_client import StripeBilling, load_plans
from src.billing.webhooks import WebhookProcessor, apply_subscription_event
from src.db.tables import Customer, CustomerPreference, WebhookEvent


def event(event_type: str, obj: dict, event_id: str = "evt_1") -> dict:
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


CHECKOUT = {
    "customer": "cus_123",
    "subscription": "sub_123",
    "customer_email": "sales@packco.test",
    "metadata": {"plan_key": "founding_monthly", "company": "PackCo Ltd"},
}


class TestPlans:
    def test_both_plans_are_configured(self):
        plans = load_plans()
        assert set(plans) == {"founding_monthly", "standard_monthly"}

    def test_only_founding_is_offered_publicly(self):
        plans = load_plans()
        assert plans["founding_monthly"].offered_publicly is True
        assert plans["standard_monthly"].offered_publicly is False

    def test_prices_match_the_commercial_decision(self):
        plans = load_plans()
        assert plans["founding_monthly"].price_pence == 7900
        assert plans["standard_monthly"].price_pence == 12900
        assert plans["founding_monthly"].price_display == "£79"

    def test_founding_plan_allows_three_recipients(self):
        assert load_plans()["founding_monthly"].max_recipients == 3


class TestStubMode:
    def test_checkout_without_a_key_does_not_pretend_to_charge(self, settings):
        session = StripeBilling(settings).create_checkout_session("founding_monthly")
        assert session.live is False
        assert "not-connected" in session.url
        assert "STRIPE_SECRET_KEY" in (session.message or "")

    def test_unknown_plan_is_rejected(self, settings):
        with pytest.raises(ValueError):
            StripeBilling(settings).create_checkout_session("nonexistent_plan")

    def test_missing_price_id_is_reported_precisely(self, settings):
        configured = settings.model_copy(update={"stripe_secret_key": "sk_test_x"})
        session = StripeBilling(configured).create_checkout_session("founding_monthly")
        assert session.live is False
        assert "STRIPE_FOUNDING_PRICE_ID" in (session.message or "")

    def test_webhook_without_a_secret_is_refused(self, settings):
        with pytest.raises(ValueError):
            StripeBilling(settings).verify_webhook(b"{}", "sig")


class TestSubscriptionLifecycle:
    def test_checkout_completion_creates_an_active_customer(self, db_session):
        outcome = apply_subscription_event(
            db_session, event("checkout.session.completed", CHECKOUT)
        )
        assert outcome.action == "subscription_started"
        customer = db_session.get(Customer, outcome.customer_id)
        assert customer.company == "PackCo Ltd"
        assert customer.subscription_status == "active"
        assert customer.delivery_enabled is True
        assert customer.founding_customer is True

    def test_checkout_completion_registers_the_recipient(self, db_session):
        outcome = apply_subscription_event(
            db_session, event("checkout.session.completed", CHECKOUT)
        )
        prefs = list(
            db_session.execute(
                select(CustomerPreference).where(
                    CustomerPreference.customer_id == outcome.customer_id
                )
            ).scalars()
        )
        assert [p.recipient_email for p in prefs] == ["sales@packco.test"]

    @pytest.mark.parametrize(
        "stripe_status,expected",
        [
            ("active", "active"),
            ("past_due", "past_due"),
            ("unpaid", "past_due"),
            ("canceled", "cancelled"),
            ("trialing", "trialing"),
            ("paused", "paused"),
        ],
    )
    def test_subscription_status_transitions(self, db_session, stripe_status, expected):
        apply_subscription_event(db_session, event("checkout.session.completed", CHECKOUT))
        apply_subscription_event(
            db_session,
            event(
                "customer.subscription.updated",
                {"customer": "cus_123", "id": "sub_123", "status": stripe_status},
                "evt_2",
            ),
        )
        customer = db_session.execute(
            select(Customer).where(Customer.stripe_customer_id == "cus_123")
        ).scalar_one()
        assert customer.subscription_status == expected

    def test_cancellation_stops_delivery(self, db_session):
        apply_subscription_event(db_session, event("checkout.session.completed", CHECKOUT))
        apply_subscription_event(
            db_session,
            event(
                "customer.subscription.deleted",
                {"customer": "cus_123", "id": "sub_123", "status": "canceled"},
                "evt_3",
            ),
        )
        customer = db_session.execute(
            select(Customer).where(Customer.stripe_customer_id == "cus_123")
        ).scalar_one()
        assert customer.subscription_status == "cancelled"
        assert customer.delivery_enabled is False
        assert customer.cancelled_at is not None

    def test_failed_payment_marks_past_due(self, db_session):
        apply_subscription_event(db_session, event("checkout.session.completed", CHECKOUT))
        apply_subscription_event(
            db_session, event("invoice.payment_failed", {"customer": "cus_123"}, "evt_4")
        )
        customer = db_session.execute(
            select(Customer).where(Customer.stripe_customer_id == "cus_123")
        ).scalar_one()
        assert customer.subscription_status == "past_due"

    def test_successful_payment_recovers_from_past_due(self, db_session):
        apply_subscription_event(db_session, event("checkout.session.completed", CHECKOUT))
        apply_subscription_event(
            db_session, event("invoice.payment_failed", {"customer": "cus_123"}, "evt_4")
        )
        apply_subscription_event(
            db_session, event("invoice.paid", {"customer": "cus_123"}, "evt_5")
        )
        customer = db_session.execute(
            select(Customer).where(Customer.stripe_customer_id == "cus_123")
        ).scalar_one()
        assert customer.subscription_status == "active"
        assert customer.delivery_enabled is True

    def test_event_for_an_unknown_customer_is_handled_safely(self, db_session):
        outcome = apply_subscription_event(
            db_session,
            event("customer.subscription.updated", {"customer": "cus_unknown", "status": "active"}),
        )
        assert outcome.action == "no_matching_customer"


class TestIdempotency:
    def test_a_redelivered_event_is_not_applied_twice(self, db_session):
        processor = WebhookProcessor(db_session)
        first = processor.process(event("checkout.session.completed", CHECKOUT, "evt_dup"))
        second = processor.process(event("checkout.session.completed", CHECKOUT, "evt_dup"))
        assert first.duplicate is False
        assert second.duplicate is True
        assert len(list(db_session.execute(select(Customer)).scalars())) == 1

    def test_processed_events_are_recorded(self, db_session):
        WebhookProcessor(db_session).process(event("checkout.session.completed", CHECKOUT, "evt_x"))
        stored = db_session.execute(
            select(WebhookEvent).where(WebhookEvent.event_id == "evt_x")
        ).scalar_one()
        assert stored.event_type == "checkout.session.completed"

    def test_unhandled_event_types_are_ignored_but_recorded(self, db_session):
        outcome = WebhookProcessor(db_session).process(
            event("charge.dispute.created", {}, "evt_ignored")
        )
        assert outcome.handled is False
        assert outcome.action == "ignored"
        assert (
            db_session.execute(
                select(WebhookEvent).where(WebhookEvent.event_id == "evt_ignored")
            ).scalar_one_or_none()
            is not None
        )
