"""Baseline: every table exactly as src/db/tables.py defined it before Alembic.

A database created by an earlier, pre-Alembic version of LaunchTrace is
brought up to this schema additively by ``src.db.engine.init_db`` and then
stamped with this revision, so nothing in here ever runs against it.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-09 12:34:15.882172
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("applicant_name", sa.String(length=512), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("company_name", sa.String(length=512), nullable=True),
        sa.Column("company_number", sa.String(length=16), nullable=True),
        sa.Column("company_status", sa.String(length=64), nullable=True),
        sa.Column("company_category", sa.String(length=128), nullable=True),
        sa.Column("incorporation_date", sa.Date(), nullable=True),
        sa.Column("dissolution_date", sa.Date(), nullable=True),
        sa.Column("sic_codes", sa.JSON(), nullable=False),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("post_town", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=128), nullable=True),
        sa.Column("accounts_category", sa.String(length=64), nullable=True),
        sa.Column("match_confidence", sa.Integer(), nullable=False),
        sa.Column("match_method", sa.String(length=64), nullable=False),
        sa.Column("match_evidence", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_company_matches_applicant_name"),
        "company_matches",
        ["applicant_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_matches_company_number"),
        "company_matches",
        ["company_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_matches_dedupe_key"), "company_matches", ["dedupe_key"], unique=False
    )

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("contact_name", sa.String(length=256), nullable=True),
        sa.Column("supplier_type", sa.String(length=64), nullable=True),
        sa.Column("plan_key", sa.String(length=32), nullable=False),
        sa.Column("subscription_status", sa.String(length=32), nullable=False),
        sa.Column("delivery_enabled", sa.Boolean(), nullable=False),
        sa.Column("founding_customer", sa.Boolean(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=64), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("past_due_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prospect_id", sa.String(length=16), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_customers_prospect_id"), "customers", ["prospect_id"], unique=False)
    op.create_index(
        op.f("ix_customers_stripe_customer_id"), "customers", ["stripe_customer_id"], unique=False
    )
    op.create_index(
        op.f("ix_customers_stripe_subscription_id"),
        "customers",
        ["stripe_subscription_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customers_subscription_status"), "customers", ["subscription_status"], unique=False
    )

    op.create_table(
        "errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_errors_run_id"), "errors", ["run_id"], unique=False)
    op.create_index(op.f("ix_errors_stage"), "errors", ["stage"], unique=False)

    op.create_table(
        "journals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("journal_number", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_name", "journal_number", name="uq_journal_source_number"),
    )
    op.create_index(
        op.f("ix_journals_journal_number"), "journals", ["journal_number"], unique=False
    )
    op.create_index(
        op.f("ix_journals_publication_date"), "journals", ["publication_date"], unique=False
    )

    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("journal_number", sa.String(length=32), nullable=False),
        sa.Column("trademark_number", sa.String(length=32), nullable=False),
        sa.Column("brand_name", sa.String(length=512), nullable=True),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("goods_summary", sa.Text(), nullable=True),
        sa.Column("product_category", sa.String(length=64), nullable=True),
        sa.Column("applicant_name", sa.String(length=512), nullable=True),
        sa.Column("applicant_type", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=512), nullable=True),
        sa.Column("company_number", sa.String(length=16), nullable=True),
        sa.Column("company_incorporation_date", sa.Date(), nullable=True),
        sa.Column("company_age_years_at_filing", sa.Float(), nullable=True),
        sa.Column("company_region", sa.String(length=128), nullable=True),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("contact_page", sa.String(length=512), nullable=True),
        sa.Column("launch_stage", sa.String(length=32), nullable=False),
        sa.Column("retail_presence", sa.String(length=32), nullable=False),
        sa.Column("launchtrace_score", sa.Integer(), nullable=False),
        sa.Column("score_band", sa.String(length=16), nullable=False),
        sa.Column("score_reasons", sa.JSON(), nullable=False),
        sa.Column("buying_intent", sa.JSON(), nullable=False),
        sa.Column("nice_classes", sa.JSON(), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("evidence_urls", sa.JSON(), nullable=False),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_state", sa.String(length=16), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("journal_number", "dedupe_key", name="uq_opportunity_journal_key"),
    )
    op.create_index(
        op.f("ix_opportunities_company_number"), "opportunities", ["company_number"], unique=False
    )
    op.create_index(
        op.f("ix_opportunities_dedupe_key"), "opportunities", ["dedupe_key"], unique=False
    )
    op.create_index(
        op.f("ix_opportunities_delivered"), "opportunities", ["delivered"], unique=False
    )
    op.create_index(
        op.f("ix_opportunities_journal_number"), "opportunities", ["journal_number"], unique=False
    )
    op.create_index(
        op.f("ix_opportunities_launchtrace_score"),
        "opportunities",
        ["launchtrace_score"],
        unique=False,
    )
    op.create_index(
        op.f("ix_opportunities_product_category"),
        "opportunities",
        ["product_category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_opportunities_publication_date"),
        "opportunities",
        ["publication_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_opportunities_review_state"), "opportunities", ["review_state"], unique=False
    )
    op.create_index(op.f("ix_opportunities_run_id"), "opportunities", ["run_id"], unique=False)
    op.create_index(
        op.f("ix_opportunities_score_band"), "opportunities", ["score_band"], unique=False
    )
    op.create_index(
        op.f("ix_opportunities_suppressed"), "opportunities", ["suppressed"], unique=False
    )
    op.create_index(
        op.f("ix_opportunities_trademark_number"),
        "opportunities",
        ["trademark_number"],
        unique=False,
    )
    op.create_index(
        "ix_opportunity_band_score",
        "opportunities",
        ["score_band", "launchtrace_score"],
        unique=False,
    )

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("journal_number", sa.String(length=32), nullable=True),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("blocked_reason", sa.String(length=256), nullable=True),
        sa.Column("csv_path", sa.String(length=512), nullable=True),
        sa.Column("email_html_path", sa.String(length=512), nullable=True),
        sa.Column("qa_report_path", sa.String(length=512), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("delivery_status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pipeline_runs_journal_number"), "pipeline_runs", ["journal_number"], unique=False
    )
    op.create_index(op.f("ix_pipeline_runs_run_id"), "pipeline_runs", ["run_id"], unique=True)
    op.create_index(op.f("ix_pipeline_runs_status"), "pipeline_runs", ["status"], unique=False)

    op.create_table(
        "prospect_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prospect_id", sa.String(length=16), nullable=False),
        sa.Column("generic_contact_email", sa.String(length=256), nullable=False),
        sa.Column("named_contact", sa.String(length=256), nullable=False),
        sa.Column("decision_maker_role", sa.String(length=128), nullable=False),
        sa.Column("email_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("icp_score", sa.Integer(), nullable=False),
        sa.Column("date_added", sa.Date(), nullable=True),
        sa.Column("email_1_sent_date", sa.Date(), nullable=True),
        sa.Column("sample_requested_date", sa.Date(), nullable=True),
        sa.Column("sample_sent_date", sa.Date(), nullable=True),
        sa.Column("offer_sent_date", sa.Date(), nullable=True),
        sa.Column("converted_date", sa.Date(), nullable=True),
        sa.Column("follow_up_due_date", sa.Date(), nullable=True),
        sa.Column("stripe_customer_id", sa.String(length=64), nullable=False),
        sa.Column("reply_state", sa.String(length=16), nullable=False),
        sa.Column("opted_out", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(length=512), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prospect_id", name="uq_prospect_state_prospect_id"),
    )
    op.create_index(
        op.f("ix_prospect_state_opted_out"), "prospect_state", ["opted_out"], unique=False
    )
    op.create_index(
        op.f("ix_prospect_state_priority"), "prospect_state", ["priority"], unique=False
    )
    op.create_index(
        op.f("ix_prospect_state_prospect_id"), "prospect_state", ["prospect_id"], unique=False
    )
    op.create_index(op.f("ix_prospect_state_status"), "prospect_state", ["status"], unique=False)

    op.create_table(
        "prospect_suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.Column("company_name", sa.String(length=512), nullable=False),
        sa.Column("date_added", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("added_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "value", name="uq_prospect_suppression_kind_value"),
    )
    op.create_index(
        op.f("ix_prospect_suppressions_kind"), "prospect_suppressions", ["kind"], unique=False
    )
    op.create_index(
        op.f("ix_prospect_suppressions_value"), "prospect_suppressions", ["value"], unique=False
    )

    op.create_table(
        "sample_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_email", sa.String(length=256), nullable=False),
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("contact_name", sa.String(length=256), nullable=True),
        sa.Column("supplier_type", sa.String(length=64), nullable=True),
        sa.Column("source_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_email", name="uq_sample_request_email"),
    )
    op.create_index(op.f("ix_sample_requests_status"), "sample_requests", ["status"], unique=False)
    op.create_index(
        op.f("ix_sample_requests_work_email"), "sample_requests", ["work_email"], unique=False
    )

    op.create_table(
        "score_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("negative_reasons", sa.JSON(), nullable=False),
        sa.Column("scoring_config_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_score_events_dedupe_key"), "score_events", ["dedupe_key"], unique=False
    )
    op.create_index(op.f("ix_score_events_run_id"), "score_events", ["run_id"], unique=False)

    op.create_table(
        "suppression_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_type", "value", name="uq_suppression_type_value"),
    )
    op.create_index(
        op.f("ix_suppression_rules_rule_type"), "suppression_rules", ["rule_type"], unique=False
    )
    op.create_index(
        op.f("ix_suppression_rules_value"), "suppression_rules", ["value"], unique=False
    )

    op.create_table(
        "web_enrichment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("attempted", sa.Boolean(), nullable=False),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("contact_page", sa.String(length=512), nullable=True),
        sa.Column("brand_description", sa.Text(), nullable=True),
        sa.Column("website_maturity", sa.String(length=32), nullable=True),
        sa.Column("products_on_sale", sa.Boolean(), nullable=True),
        sa.Column("marketplace_presence", sa.Boolean(), nullable=True),
        sa.Column("major_retailer_presence", sa.Boolean(), nullable=True),
        sa.Column("social_presence", sa.Boolean(), nullable=True),
        sa.Column("retail_presence", sa.String(length=32), nullable=False),
        sa.Column("launch_evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_urls", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_web_enrichment_dedupe_key"), "web_enrichment", ["dedupe_key"], unique=False
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_summary", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
    )
    op.create_index(
        op.f("ix_webhook_events_event_id"), "webhook_events", ["event_id"], unique=False
    )

    op.create_table(
        "customer_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=256), nullable=False),
        sa.Column("sector", sa.String(length=32), nullable=False),
        sa.Column("min_score_band", sa.String(length=16), nullable=False),
        sa.Column("regions", sa.JSON(), nullable=False),
        sa.Column("product_categories", sa.JSON(), nullable=False),
        sa.Column("supplier_category", sa.String(length=64), nullable=True),
        sa.Column("buying_intent_categories", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "recipient_email", name="uq_customer_recipient"),
    )
    op.create_index(
        op.f("ix_customer_preferences_recipient_email"),
        "customer_preferences",
        ["recipient_email"],
        unique=False,
    )

    op.create_table(
        "deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("journal_number", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("recipient_email", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("opportunity_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_delivery_idempotency"),
    )
    op.create_index(
        op.f("ix_deliveries_idempotency_key"), "deliveries", ["idempotency_key"], unique=False
    )
    op.create_index(
        op.f("ix_deliveries_journal_number"), "deliveries", ["journal_number"], unique=False
    )
    op.create_index(
        op.f("ix_deliveries_recipient_email"), "deliveries", ["recipient_email"], unique=False
    )
    op.create_index(op.f("ix_deliveries_run_id"), "deliveries", ["run_id"], unique=False)
    op.create_index(op.f("ix_deliveries_status"), "deliveries", ["status"], unique=False)

    op.create_table(
        "lead_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=64), nullable=True),
        sa.Column("trademark_number", sa.String(length=32), nullable=True),
        sa.Column("brand_name", sa.String(length=512), nullable=True),
        sa.Column("journal_number", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feedback_customer_state", "lead_feedback", ["customer_id", "state"], unique=False
    )
    op.create_index(
        op.f("ix_lead_feedback_dedupe_key"), "lead_feedback", ["dedupe_key"], unique=False
    )
    op.create_index(
        op.f("ix_lead_feedback_journal_number"), "lead_feedback", ["journal_number"], unique=False
    )
    op.create_index(op.f("ix_lead_feedback_state"), "lead_feedback", ["state"], unique=False)
    op.create_index(
        op.f("ix_lead_feedback_trademark_number"),
        "lead_feedback",
        ["trademark_number"],
        unique=False,
    )

    op.create_table(
        "trademark_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("journal_id", sa.Integer(), nullable=True),
        sa.Column("journal_number", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("trademark_number", sa.String(length=32), nullable=False),
        sa.Column("mark_text", sa.String(length=512), nullable=True),
        sa.Column("mark_type", sa.String(length=64), nullable=True),
        sa.Column("mark_category", sa.String(length=64), nullable=True),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("applicant_name", sa.String(length=512), nullable=True),
        sa.Column("applicant_country", sa.String(length=128), nullable=True),
        sa.Column("applicant_region", sa.String(length=128), nullable=True),
        sa.Column("applicant_postcode_area", sa.String(length=16), nullable=True),
        sa.Column("nice_classes", sa.JSON(), nullable=False),
        sa.Column("goods_text", sa.Text(), nullable=True),
        sa.Column("goods_text_available", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("series_count", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["journal_id"], ["journals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("journal_number", "trademark_number", name="uq_tm_journal_number"),
    )
    op.create_index(
        "ix_tm_applicant_journal",
        "trademark_records",
        ["applicant_name", "journal_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trademark_records_applicant_name"),
        "trademark_records",
        ["applicant_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trademark_records_dedupe_key"), "trademark_records", ["dedupe_key"], unique=False
    )
    op.create_index(
        op.f("ix_trademark_records_filing_date"), "trademark_records", ["filing_date"], unique=False
    )
    op.create_index(
        op.f("ix_trademark_records_journal_number"),
        "trademark_records",
        ["journal_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trademark_records_publication_date"),
        "trademark_records",
        ["publication_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trademark_records_trademark_number"),
        "trademark_records",
        ["trademark_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_trademark_records_trademark_number"), table_name="trademark_records")
    op.drop_index(op.f("ix_trademark_records_publication_date"), table_name="trademark_records")
    op.drop_index(op.f("ix_trademark_records_journal_number"), table_name="trademark_records")
    op.drop_index(op.f("ix_trademark_records_filing_date"), table_name="trademark_records")
    op.drop_index(op.f("ix_trademark_records_dedupe_key"), table_name="trademark_records")
    op.drop_index(op.f("ix_trademark_records_applicant_name"), table_name="trademark_records")
    op.drop_index("ix_tm_applicant_journal", table_name="trademark_records")

    op.drop_table("trademark_records")
    op.drop_index(op.f("ix_lead_feedback_trademark_number"), table_name="lead_feedback")
    op.drop_index(op.f("ix_lead_feedback_state"), table_name="lead_feedback")
    op.drop_index(op.f("ix_lead_feedback_journal_number"), table_name="lead_feedback")
    op.drop_index(op.f("ix_lead_feedback_dedupe_key"), table_name="lead_feedback")
    op.drop_index("ix_feedback_customer_state", table_name="lead_feedback")

    op.drop_table("lead_feedback")
    op.drop_index(op.f("ix_deliveries_status"), table_name="deliveries")
    op.drop_index(op.f("ix_deliveries_run_id"), table_name="deliveries")
    op.drop_index(op.f("ix_deliveries_recipient_email"), table_name="deliveries")
    op.drop_index(op.f("ix_deliveries_journal_number"), table_name="deliveries")
    op.drop_index(op.f("ix_deliveries_idempotency_key"), table_name="deliveries")

    op.drop_table("deliveries")
    op.drop_index(
        op.f("ix_customer_preferences_recipient_email"), table_name="customer_preferences"
    )

    op.drop_table("customer_preferences")
    op.drop_index(op.f("ix_webhook_events_event_id"), table_name="webhook_events")

    op.drop_table("webhook_events")
    op.drop_index(op.f("ix_web_enrichment_dedupe_key"), table_name="web_enrichment")

    op.drop_table("web_enrichment")
    op.drop_index(op.f("ix_suppression_rules_value"), table_name="suppression_rules")
    op.drop_index(op.f("ix_suppression_rules_rule_type"), table_name="suppression_rules")

    op.drop_table("suppression_rules")
    op.drop_index(op.f("ix_score_events_run_id"), table_name="score_events")
    op.drop_index(op.f("ix_score_events_dedupe_key"), table_name="score_events")

    op.drop_table("score_events")
    op.drop_index(op.f("ix_sample_requests_work_email"), table_name="sample_requests")
    op.drop_index(op.f("ix_sample_requests_status"), table_name="sample_requests")

    op.drop_table("sample_requests")
    op.drop_index(op.f("ix_prospect_suppressions_value"), table_name="prospect_suppressions")
    op.drop_index(op.f("ix_prospect_suppressions_kind"), table_name="prospect_suppressions")

    op.drop_table("prospect_suppressions")
    op.drop_index(op.f("ix_prospect_state_status"), table_name="prospect_state")
    op.drop_index(op.f("ix_prospect_state_prospect_id"), table_name="prospect_state")
    op.drop_index(op.f("ix_prospect_state_priority"), table_name="prospect_state")
    op.drop_index(op.f("ix_prospect_state_opted_out"), table_name="prospect_state")

    op.drop_table("prospect_state")
    op.drop_index(op.f("ix_pipeline_runs_status"), table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_run_id"), table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_journal_number"), table_name="pipeline_runs")

    op.drop_table("pipeline_runs")
    op.drop_index("ix_opportunity_band_score", table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_trademark_number"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_suppressed"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_score_band"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_run_id"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_review_state"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_publication_date"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_product_category"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_launchtrace_score"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_journal_number"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_delivered"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_dedupe_key"), table_name="opportunities")
    op.drop_index(op.f("ix_opportunities_company_number"), table_name="opportunities")

    op.drop_table("opportunities")
    op.drop_index(op.f("ix_journals_publication_date"), table_name="journals")
    op.drop_index(op.f("ix_journals_journal_number"), table_name="journals")

    op.drop_table("journals")
    op.drop_index(op.f("ix_errors_stage"), table_name="errors")
    op.drop_index(op.f("ix_errors_run_id"), table_name="errors")

    op.drop_table("errors")
    op.drop_index(op.f("ix_customers_subscription_status"), table_name="customers")
    op.drop_index(op.f("ix_customers_stripe_subscription_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_stripe_customer_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_prospect_id"), table_name="customers")

    op.drop_table("customers")
    op.drop_index(op.f("ix_company_matches_dedupe_key"), table_name="company_matches")
    op.drop_index(op.f("ix_company_matches_company_number"), table_name="company_matches")
    op.drop_index(op.f("ix_company_matches_applicant_name"), table_name="company_matches")

    op.drop_table("company_matches")
