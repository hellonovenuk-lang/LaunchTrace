"""What happens to a customer after they pay.

Covers onboarding, payment failure, recovery and cancellation, plus the sample
package and the business metrics that report on all of it.

Two properties are load-bearing:

* a customer whose subscription has stopped stops receiving the feed;
* no transactional message is ever prepared twice for the same event.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from src.customer_lifecycle import (
    PAST_DUE_GRACE_DAYS,
    delivery_allowed,
    delivery_block_reason,
    on_cancelled,
    on_payment_failed,
    on_payment_recovered,
    on_subscription_started,
)
from src.db.tables import Customer, CustomerPreference, Delivery
from src.deliver.resend_client import EmailSender
from src.deliver.sample_pack import build_sample_pack, qualify_for_sample, render_sample_report
from src.deliver.transactional import (
    next_friday,
    render_cancellation_confirmed,
    render_onboarding,
    render_payment_failed,
)
from src.delivery_service import active_customers
from src.sales.feedback import FeedbackState, record_feedback, summarise_feedback
from src.sales.metrics import business_status, funnel_metrics
from src.sales.models import ProspectStatus
from tests.conftest import make_lead, make_prospect


def _sender(tmp_path):  # type: ignore[no-untyped-def]
    """A sender with no credential, writing to an isolated outbox."""
    from src.settings import Settings

    settings = Settings(SITE_URL="https://launchtrace.test")  # type: ignore[call-arg]
    return EmailSender(settings, outbox=tmp_path / "outbox")


class TestOnboarding:
    def test_a_new_subscription_enables_delivery_and_prepares_one_message(
        self, db_session, customer, tmp_path
    ):
        outcome = on_subscription_started(db_session, customer, sender=_sender(tmp_path))

        assert customer.subscription_status == "active"
        assert customer.delivery_enabled is True
        assert delivery_allowed(customer) is True
        assert {m.kind for m in outcome.messages} == {"onboarding"}
        assert len(outcome.messages) == 1, "one recipient gets exactly one onboarding email"
        assert all(m.ok for m in outcome.messages)

    def test_the_onboarding_message_is_written_where_it_can_be_read(
        self, db_session, customer, tmp_path
    ):
        on_subscription_started(db_session, customer, sender=_sender(tmp_path))
        written = sorted((tmp_path / "outbox").glob("*.html"))
        assert len(written) == 1, "onboarding is one email, not three"

    def test_onboarding_twice_prepares_nothing_twice(self, db_session, customer, tmp_path):
        sender = _sender(tmp_path)
        on_subscription_started(db_session, customer, sender=sender)
        second = on_subscription_started(db_session, customer, sender=sender)
        assert all(m.status == "already_prepared" for m in second.messages)
        assert len(list((tmp_path / "outbox").glob("*.html"))) == 1

    def test_the_onboarding_email_carries_all_three_old_messages(self):
        rendered = render_onboarding(
            "Pouchworks Ltd", recipients=["ops@pouchworks.test"], first_feed=date(2026, 3, 6)
        )
        # Subscription confirmed ...
        assert "payment has gone through" in rendered.html
        # ... what happens next ...
        assert "every friday" in rendered.html.lower()
        # ... and the specific first Friday.
        assert "6 March 2026" in rendered.html

    def test_a_customer_with_no_recipient_is_reported_not_silently_accepted(
        self, db_session, tmp_path
    ):
        customer = Customer(company="Nobody Ltd", subscription_status="active")
        db_session.add(customer)
        db_session.flush()
        outcome = on_subscription_started(db_session, customer, sender=_sender(tmp_path))
        assert outcome.messages == []
        assert "No recipients" in outcome.detail

    def test_the_new_customer_receives_the_next_weekly_feed(self, db_session, customer, tmp_path):
        on_subscription_started(db_session, customer, sender=_sender(tmp_path))
        assert customer in active_customers(db_session)

    def test_the_onboarding_email_names_a_real_friday(self):
        rendered = render_onboarding("Pouchworks Ltd", first_feed=date(2026, 3, 6))
        assert "6 March 2026" in rendered.html
        assert "£79" in rendered.html
        assert next_friday(date(2026, 3, 6)) == date(2026, 3, 13), (
            "on a Friday, the next feed is the following Friday"
        )
        assert next_friday(date(2026, 3, 2)).weekday() == 4


class TestPaymentFailure:
    def test_a_failure_marks_past_due_but_does_not_stop_the_feed(
        self, db_session, customer, tmp_path
    ):
        outcome = on_payment_failed(db_session, customer, "inv_1", sender=_sender(tmp_path))
        assert customer.subscription_status == "past_due"
        assert customer.past_due_since is not None
        assert delivery_allowed(customer) is True
        assert outcome.messages[0].kind == "payment_failed"

    def test_the_feed_stops_once_the_grace_period_is_over(self, db_session, customer, tmp_path):
        on_payment_failed(db_session, customer, "inv_1", sender=_sender(tmp_path))
        customer.past_due_since = datetime.now(UTC) - timedelta(days=PAST_DUE_GRACE_DAYS + 1)
        assert delivery_allowed(customer) is False
        assert "past due" in (delivery_block_reason(customer) or "")
        assert customer not in active_customers(db_session)

    def test_the_same_invoice_is_never_chased_twice(self, db_session, customer, tmp_path):
        sender = _sender(tmp_path)
        on_payment_failed(db_session, customer, "inv_1", sender=sender)
        repeat = on_payment_failed(db_session, customer, "inv_1", sender=sender)
        assert repeat.messages[0].status == "already_prepared"
        assert len(list((tmp_path / "outbox").glob("*.html"))) == 1

    def test_a_genuinely_new_failure_is_a_new_message(self, db_session, customer, tmp_path):
        sender = _sender(tmp_path)
        on_payment_failed(db_session, customer, "inv_1", sender=sender)
        second = on_payment_failed(db_session, customer, "inv_2", sender=sender)
        assert second.messages[0].status == "rendered_not_sent"

    def test_recovery_clears_past_due_and_sends_nothing(self, db_session, customer, tmp_path):
        on_payment_failed(db_session, customer, "inv_1", sender=_sender(tmp_path))
        outcome = on_payment_recovered(db_session, customer)
        assert customer.subscription_status == "active"
        assert customer.past_due_since is None
        assert outcome.messages == [], "nobody wants an email saying their card worked"
        assert delivery_allowed(customer) is True

    def test_the_warning_states_the_grace_period(self):
        rendered = render_payment_failed("Pouchworks Ltd", grace_days=14)
        assert "14 days" in rendered.html
        assert "not cancelled" in rendered.html


class TestCancellation:
    def test_cancelling_stops_delivery_and_confirms_it(self, db_session, customer, tmp_path):
        outcome = on_cancelled(db_session, customer, sender=_sender(tmp_path))
        assert customer.subscription_status == "cancelled"
        assert customer.delivery_enabled is False
        assert customer.cancelled_at is not None
        assert delivery_allowed(customer) is False
        assert outcome.messages[0].kind == "cancellation_confirmed"

    def test_a_cancelled_customer_receives_no_further_feed(self, db_session, customer, tmp_path):
        on_cancelled(db_session, customer, sender=_sender(tmp_path))
        assert active_customers(db_session) == []

    def test_cancellation_is_confirmed_only_once(self, db_session, customer, tmp_path):
        sender = _sender(tmp_path)
        on_cancelled(db_session, customer, sender=sender)
        repeat = on_cancelled(db_session, customer, sender=sender)
        assert repeat.messages[0].status == "already_prepared"

    def test_the_confirmation_promises_no_further_charges(self):
        rendered = render_cancellation_confirmed("Pouchworks Ltd", last_feed=date(2026, 3, 6))
        assert "not be billed again" in rendered.html
        assert "None" in rendered.html

    def test_every_transactional_message_offers_an_opt_out(self):
        for rendered in (
            render_onboarding("Pouchworks Ltd"),
            render_payment_failed("Pouchworks Ltd"),
            render_cancellation_confirmed("Pouchworks Ltd"),
        ):
            assert "/unsubscribe" in rendered.html


class TestCustomerPreferences:
    def test_supplier_preferences_are_recorded_for_later_use(self, db_session, customer):
        pref = db_session.query(CustomerPreference).filter_by(customer_id=customer.id).one()
        pref.supplier_category = "flexible_packaging"
        pref.buying_intent_categories = ["flexible_packaging", "labels"]
        db_session.flush()

        reloaded = db_session.query(CustomerPreference).filter_by(customer_id=customer.id).one()
        assert reloaded.supplier_category == "flexible_packaging"
        assert reloaded.buying_intent_categories == ["flexible_packaging", "labels"]

    def test_recording_a_preference_does_not_narrow_the_current_feed(self, db_session, customer):
        """The MVP still delivers the whole food feed. The fields are for later."""
        pref = db_session.query(CustomerPreference).filter_by(customer_id=customer.id).one()
        pref.supplier_category = "labels"
        db_session.flush()
        assert pref.product_categories == [], "no category filter is applied yet"
        assert customer in active_customers(db_session)


class TestSamplePack:
    def test_only_qualifying_leads_reach_a_customer(self):
        kept, excluded = qualify_for_sample(
            [
                make_lead(),
                make_lead(trademark_number="UK2", suppressed=True),
                make_lead(trademark_number="UK3", band="SUPPRESS", score=30),
                make_lead(trademark_number="UK4", company_number=""),
            ]
        )
        assert [lead.trademark_number for lead in kept] == ["UK00003900001"]
        assert excluded["suppressed record"] == 1
        assert excluded["no verified Companies House match"] == 1

    def test_the_report_shows_the_score_the_reasons_and_the_source(self):
        html = render_sample_report([make_lead()])
        assert "CRUMBLEDGE" in html
        assert "LaunchTrace Score" in html
        assert "UK company incorporated 6 months before this filing" in html
        assert "https://example.invalid/tm/UK00003900001" in html

    def test_the_report_never_claims_purchase_intent(self):
        html = render_sample_report([make_lead()])
        assert "not that it has placed an order" in html
        assert "inferred relevance" in html
        assert "Open Government Licence" in html

    def test_a_sample_is_labelled_as_one(self):
        assert "Sample — not a live subscription" in render_sample_report([make_lead()])
        assert "Sample — not a live subscription" not in render_sample_report(
            [make_lead()], watermark=False
        )

    def test_an_empty_sample_explains_itself_rather_than_breaking(self):
        html = render_sample_report([])
        assert "No opportunities cleared" in html
        assert "Nothing has been removed" in html

    def test_the_package_writes_a_csv_and_a_report(self, tmp_path):
        pack = build_sample_pack([make_lead()], out_dir=tmp_path / "sample")
        assert pack.csv_path.exists() and pack.html_path.exists()
        assert pack.count == 1
        text = pack.csv_path.read_text(encoding="utf-8-sig")
        assert "why_selected" in text
        assert "CRUMBLEDGE" in text

    def test_internal_debugging_fields_stay_out_of_the_customer_csv(self, tmp_path):
        pack = build_sample_pack([make_lead()], out_dir=tmp_path / "sample")
        header = pack.csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
        for internal in ("dedupe_key", "match_method", "match_confidence", "rejection", "run_id"):
            assert internal not in header


class TestFeedback:
    def test_feedback_is_recorded_against_the_customer(self, db_session, customer):
        row = record_feedback(
            db_session,
            state=FeedbackState.USEFUL,
            trademark_number="UK00003900001",
            customer_id=customer.id,
            note="Called them.",
        )
        assert row.state == "USEFUL"
        assert row.customer_id == customer.id
        assert row.note == "Called them."

    def test_a_summary_only_reports_a_pattern_once_it_repeats(self, db_session, customer):
        record_feedback(db_session, FeedbackState.NOT_RELEVANT, "UK1", customer_id=customer.id)
        assert summarise_feedback(db_session).patterns() == []

        record_feedback(db_session, FeedbackState.NOT_RELEVANT, "UK2", customer_id=customer.id)
        patterns = summarise_feedback(db_session).patterns()
        assert len(patterns) == 1 and "NOT_RELEVANT" in patterns[0]

    def test_usefulness_counts_action_not_just_praise(self, db_session, customer):
        record_feedback(db_session, FeedbackState.USEFUL, "UK1", customer_id=customer.id)
        record_feedback(db_session, FeedbackState.CONVERTED, "UK2", customer_id=customer.id)
        record_feedback(db_session, FeedbackState.TOO_EARLY, "UK3", customer_id=customer.id)
        summary = summarise_feedback(db_session)
        assert summary.total == 3
        assert summary.positive == 2

    def test_feedback_does_not_reach_the_scoring_configuration(self, db_session, customer):
        """Recording feedback must not move a weight. Tuning stays deliberate."""
        import pathlib

        config = pathlib.Path("config/scoring.json")
        before = config.read_bytes()

        for state in (FeedbackState.NOT_RELEVANT, FeedbackState.TOO_ESTABLISHED):
            record_feedback(db_session, state, "UK1", customer_id=customer.id)

        assert config.read_bytes() == before, "feedback changed the scoring configuration"
        assert summarise_feedback(db_session).total == 2, "but it was still recorded"

        import src.sales.feedback as feedback_module

        assert not hasattr(feedback_module, "LaunchTraceScorer")
        assert not hasattr(feedback_module, "load_config")

    def test_an_unknown_state_is_reported_rather_than_guessed(self, db_session, tmp_path):
        from src.sales.feedback import import_feedback_csv

        path = tmp_path / "feedback.csv"
        path.write_text(
            "trademark_number,state,note\nUK1,USEFUL,\nUK2,MAYBE_GOOD,\n", encoding="utf-8"
        )
        imported, problems = import_feedback_csv(db_session, path)
        assert imported == 1
        assert len(problems) == 1 and "MAYBE_GOOD" in problems[0]


class TestBusinessMetrics:
    def test_the_funnel_counts_what_happened_not_the_current_status(self):
        metrics = funnel_metrics(
            [
                make_prospect(
                    prospect_id="P001",
                    status=ProspectStatus.SUBSCRIBED,
                    email_1_sent_date=date(2026, 2, 1),
                    sample_sent_date=date(2026, 2, 8),
                    converted_date=date(2026, 2, 15),
                ),
                make_prospect(
                    prospect_id="P002",
                    company_name="Second Ltd",
                    website="https://second.test/",
                    status=ProspectStatus.NO_RESPONSE,
                    email_1_sent_date=date(2026, 2, 1),
                ),
            ]
        )
        assert metrics.email_1_sent == 2, "a subscriber was still sent a first email"
        assert metrics.samples_sent == 1
        assert metrics.subscribed == 1

    def test_a_rate_on_a_tiny_denominator_says_so(self):
        metrics = funnel_metrics(
            [make_prospect(email_1_sent_date=date(2026, 2, 1), sample_sent_date=date(2026, 2, 2))]
        )
        assert metrics.sample_rate.meaningful is False
        assert "too few" in metrics.sample_rate.display()

    def test_an_empty_funnel_reports_nothing_rather_than_dividing_by_zero(self):
        metrics = funnel_metrics([])
        assert metrics.reply_rate.value is None
        assert metrics.reply_rate.display() == "—"

    def test_mrr_counts_only_active_subscriptions(self, db_session, customer):
        trialing = Customer(
            company="Trial Ltd", plan_key="founding_monthly", subscription_status="trialing"
        )
        db_session.add(trialing)
        db_session.flush()

        status = business_status(db_session, [make_prospect()])
        assert status.mrr_pence == 7900, "only the active customer counts"
        assert status.operations.active_customers >= 1

    def test_status_survives_an_empty_database(self, db_session):
        status = business_status(db_session, [])
        assert status.mrr_pence == 0
        assert status.product.per_week == 0.0
        assert status.costs.breakeven_customers >= 1

    def test_cancelled_and_past_due_customers_are_reported(self, db_session, customer, tmp_path):
        on_payment_failed(db_session, customer, "inv_1", sender=_sender(tmp_path))
        status = business_status(db_session, [])
        assert status.operations.past_due_customers == 1
        assert status.mrr_pence == 0, "a past-due account is not revenue"


class TestCostModel:
    def test_cost_scales_with_customers_and_margin_is_reported(self):
        from src.sales.costs import estimate_costs

        one = estimate_costs(customers=1)
        ten = estimate_costs(customers=10)
        assert ten.total_monthly_pence > one.total_monthly_pence
        assert ten.gross_margin_percent is not None
        assert ten.monthly_revenue_pence == 79000

    def test_an_unconnected_provider_costs_nothing(self):
        from src.sales.costs import estimate_costs

        off = estimate_costs(customers=1, llm_enabled=False, search_enabled=False)
        on = estimate_costs(customers=1, llm_enabled=True, search_enabled=True)
        assert on.per_run_pence >= off.per_run_pence
        assert not any(line.key == "llm_classification" for line in off.lines)

    def test_assumed_figures_are_labelled_as_assumptions(self):
        from src.sales.costs import estimate_costs

        estimate = estimate_costs(customers=1)
        assert estimate.assumed_lines, "an unverified price must be marked as one"
        assert all(line.source_note for line in estimate.assumed_lines)

    def test_zero_customers_does_not_divide_by_zero(self):
        from src.sales.costs import estimate_costs

        estimate = estimate_costs(customers=0)
        assert estimate.per_customer_monthly_pence == 0.0
        assert estimate.gross_margin_percent is None
        assert estimate.breakeven_customers >= 1


class TestDeliveryRespectsSubscriptionState:
    def test_only_paying_or_trialing_customers_receive_the_feed(self, db_session):
        states = {
            "active": True,
            "trialing": True,
            "past_due": True,
            "cancelled": False,
            "incomplete": False,
            "paused": False,
        }
        for index, (state, expected) in enumerate(states.items()):
            customer = Customer(company=f"C{index}", subscription_status=state)
            db_session.add(customer)
            db_session.flush()
            assert delivery_allowed(customer) is expected, state

    def test_a_delivery_row_is_written_for_every_prepared_message(
        self, db_session, customer, tmp_path
    ):
        on_subscription_started(db_session, customer, sender=_sender(tmp_path))
        rows = db_session.query(Delivery).filter_by(customer_id=customer.id).all()
        assert {row.kind for row in rows} == {"onboarding"}
        assert all(row.idempotency_key for row in rows)
