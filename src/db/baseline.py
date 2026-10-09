"""The schema at Alembic revision ``0001_baseline``, frozen.

A database created before Alembic (tables present, no ``alembic_version``) is
brought up to exactly this schema, additively, before it is stamped
``0001_baseline`` and upgraded. The list is frozen on purpose: a pre-Alembic
database can only ever be behind the baseline, never ahead of it, and columns
added by later revisions must be left for those revisions to add.

``tests/test_migrations.py`` proves this matches what ``0001_baseline`` creates.
Do not edit it when adding a migration.
"""

from __future__ import annotations

BASELINE_REVISION = "0001_baseline"

BASELINE_SCHEMA: dict[str, tuple[str, ...]] = {
    "company_matches": (
        "id", "dedupe_key", "applicant_name", "matched", "company_name", "company_number",
        "company_status", "company_category", "incorporation_date", "dissolution_date",
        "sic_codes", "region", "post_town", "country", "accounts_category", "match_confidence",
        "match_method", "match_evidence", "provider", "error", "created_at",
    ),
    "customer_preferences": (
        "id", "customer_id", "recipient_email", "sector", "min_score_band", "regions",
        "product_categories", "supplier_category", "buying_intent_categories", "created_at",
    ),
    "customers": (
        "id", "company", "contact_name", "supplier_type", "plan_key", "subscription_status",
        "delivery_enabled", "founding_customer", "stripe_customer_id", "stripe_subscription_id",
        "created_at", "cancelled_at", "past_due_since", "prospect_id",
    ),
    "deliveries": (
        "id", "run_id", "journal_number", "customer_id", "recipient_email", "kind", "status",
        "provider_message_id", "opportunity_count", "error", "idempotency_key", "created_at",
        "sent_at",
    ),
    "errors": ("id", "run_id", "stage", "severity", "reference", "message", "created_at"),
    "journals": (
        "id", "journal_number", "source_name", "publication_date", "source_url", "sha256",
        "byte_size", "record_count", "retrieved_at", "processed_at", "processing_status",
    ),
    "lead_feedback": (
        "id", "customer_id", "dedupe_key", "trademark_number", "brand_name", "journal_number",
        "state", "note", "source", "created_at",
    ),
    "opportunities": (
        "id", "dedupe_key", "run_id", "journal_number", "trademark_number", "brand_name",
        "filing_date", "publication_date", "goods_summary", "product_category", "applicant_name",
        "applicant_type", "company_name", "company_number", "company_incorporation_date",
        "company_age_years_at_filing", "company_region", "website", "contact_page",
        "launch_stage", "retail_presence", "launchtrace_score", "score_band", "score_reasons",
        "buying_intent", "nice_classes", "source_url", "evidence_urls", "enriched_at",
        "review_state", "delivered", "suppressed", "suppression_reason", "created_at",
    ),
    "pipeline_runs": (
        "id", "run_id", "mode", "journal_number", "source_name", "status", "started_at",
        "finished_at", "counts", "warnings", "blocked_reason", "csv_path", "email_html_path",
        "qa_report_path", "approved_at", "approved_by", "delivery_status",
    ),
    "prospect_state": (
        "id", "prospect_id", "generic_contact_email", "named_contact", "decision_maker_role",
        "email_source", "status", "priority", "icp_score", "date_added", "email_1_sent_date",
        "sample_requested_date", "sample_sent_date", "offer_sent_date", "converted_date",
        "follow_up_due_date", "stripe_customer_id", "reply_state", "opted_out",
        "suppression_reason", "notes", "created_at", "updated_at",
    ),
    "prospect_suppressions": (
        "id", "kind", "value", "company_name", "date_added", "reason", "added_by", "created_at",
    ),
    "sample_requests": (
        "id", "work_email", "company", "contact_name", "supplier_type", "source_ip_hash",
        "status", "sent_at", "created_at",
    ),
    "score_events": (
        "id", "dedupe_key", "run_id", "score", "band", "reasons", "negative_reasons",
        "scoring_config_version", "created_at",
    ),
    "suppression_rules": (
        "id", "rule_type", "value", "reason", "created_by", "active", "created_at",
    ),
    "trademark_records": (
        "id", "journal_id", "journal_number", "dedupe_key", "trademark_number", "mark_text",
        "mark_type", "mark_category", "filing_date", "publication_date", "applicant_name",
        "applicant_country", "applicant_region", "applicant_postcode_area", "nice_classes",
        "goods_text", "goods_text_available", "status", "series_count", "source_url",
        "source_name", "created_at",
    ),
    "web_enrichment": (
        "id", "dedupe_key", "provider", "attempted", "website", "contact_page",
        "brand_description", "website_maturity", "products_on_sale", "marketplace_presence",
        "major_retailer_presence", "social_presence", "retail_presence", "launch_evidence",
        "evidence_urls", "error", "enriched_at",
    ),
    "webhook_events": (
        "id", "provider", "event_id", "event_type", "processed_at", "payload_summary",
    ),
}  # fmt: skip
