-- LaunchTrace initial schema (PostgreSQL / Supabase).
--
-- Generated from src/db/tables.py by scripts/generate_migration.py.
-- Do not hand-edit: change the models and regenerate.
--
-- Apply with:
--     psql "$DATABASE_URL" -f migrations/0001_initial.sql
-- or paste into the Supabase SQL editor.

BEGIN;

CREATE TABLE IF NOT EXISTS company_matches (
	id SERIAL NOT NULL, 
	dedupe_key VARCHAR(64) NOT NULL, 
	applicant_name VARCHAR(512), 
	matched BOOLEAN NOT NULL, 
	company_name VARCHAR(512), 
	company_number VARCHAR(16), 
	company_status VARCHAR(64), 
	company_category VARCHAR(128), 
	incorporation_date DATE, 
	dissolution_date DATE, 
	sic_codes JSON NOT NULL, 
	region VARCHAR(128), 
	post_town VARCHAR(128), 
	country VARCHAR(128), 
	accounts_category VARCHAR(64), 
	match_confidence INTEGER NOT NULL, 
	match_method VARCHAR(64) NOT NULL, 
	match_evidence JSON NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	error VARCHAR(512), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_company_matches_applicant_name ON company_matches (applicant_name);
CREATE INDEX IF NOT EXISTS ix_company_matches_company_number ON company_matches (company_number);
CREATE INDEX IF NOT EXISTS ix_company_matches_dedupe_key ON company_matches (dedupe_key);

CREATE TABLE IF NOT EXISTS customers (
	id SERIAL NOT NULL, 
	company VARCHAR(256) NOT NULL, 
	contact_name VARCHAR(256), 
	supplier_type VARCHAR(64), 
	plan_key VARCHAR(32) NOT NULL, 
	subscription_status VARCHAR(32) NOT NULL, 
	delivery_enabled BOOLEAN NOT NULL, 
	founding_customer BOOLEAN NOT NULL, 
	stripe_customer_id VARCHAR(64), 
	stripe_subscription_id VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	cancelled_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_customers_stripe_customer_id ON customers (stripe_customer_id);
CREATE INDEX IF NOT EXISTS ix_customers_stripe_subscription_id ON customers (stripe_subscription_id);
CREATE INDEX IF NOT EXISTS ix_customers_subscription_status ON customers (subscription_status);

CREATE TABLE IF NOT EXISTS errors (
	id SERIAL NOT NULL, 
	run_id VARCHAR(64), 
	stage VARCHAR(64) NOT NULL, 
	severity VARCHAR(16) NOT NULL, 
	reference VARCHAR(128), 
	message TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_errors_run_id ON errors (run_id);
CREATE INDEX IF NOT EXISTS ix_errors_stage ON errors (stage);

CREATE TABLE IF NOT EXISTS journals (
	id SERIAL NOT NULL, 
	journal_number VARCHAR(32) NOT NULL, 
	source_name VARCHAR(64) NOT NULL, 
	publication_date DATE NOT NULL, 
	source_url VARCHAR(512), 
	sha256 VARCHAR(64), 
	byte_size INTEGER NOT NULL, 
	record_count INTEGER NOT NULL, 
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	processed_at TIMESTAMP WITH TIME ZONE, 
	processing_status VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_journal_source_number UNIQUE (source_name, journal_number)
);

CREATE INDEX IF NOT EXISTS ix_journals_journal_number ON journals (journal_number);
CREATE INDEX IF NOT EXISTS ix_journals_publication_date ON journals (publication_date);

CREATE TABLE IF NOT EXISTS opportunities (
	id SERIAL NOT NULL, 
	dedupe_key VARCHAR(64) NOT NULL, 
	run_id VARCHAR(64) NOT NULL, 
	journal_number VARCHAR(32) NOT NULL, 
	trademark_number VARCHAR(32) NOT NULL, 
	brand_name VARCHAR(512), 
	filing_date DATE, 
	publication_date DATE, 
	goods_summary TEXT, 
	product_category VARCHAR(64), 
	applicant_name VARCHAR(512), 
	applicant_type VARCHAR(32) NOT NULL, 
	company_name VARCHAR(512), 
	company_number VARCHAR(16), 
	company_incorporation_date DATE, 
	company_age_years_at_filing FLOAT, 
	company_region VARCHAR(128), 
	website VARCHAR(512), 
	contact_page VARCHAR(512), 
	launch_stage VARCHAR(32) NOT NULL, 
	retail_presence VARCHAR(32) NOT NULL, 
	launchtrace_score INTEGER NOT NULL, 
	score_band VARCHAR(16) NOT NULL, 
	score_reasons JSON NOT NULL, 
	buying_intent JSON NOT NULL, 
	nice_classes JSON NOT NULL, 
	source_url VARCHAR(512), 
	evidence_urls JSON NOT NULL, 
	enriched_at TIMESTAMP WITH TIME ZONE, 
	review_state VARCHAR(16) NOT NULL, 
	delivered BOOLEAN NOT NULL, 
	suppressed BOOLEAN NOT NULL, 
	suppression_reason VARCHAR(128), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_opportunity_journal_key UNIQUE (journal_number, dedupe_key)
);

CREATE INDEX IF NOT EXISTS ix_opportunities_company_number ON opportunities (company_number);
CREATE INDEX IF NOT EXISTS ix_opportunities_dedupe_key ON opportunities (dedupe_key);
CREATE INDEX IF NOT EXISTS ix_opportunities_delivered ON opportunities (delivered);
CREATE INDEX IF NOT EXISTS ix_opportunities_journal_number ON opportunities (journal_number);
CREATE INDEX IF NOT EXISTS ix_opportunities_launchtrace_score ON opportunities (launchtrace_score);
CREATE INDEX IF NOT EXISTS ix_opportunities_product_category ON opportunities (product_category);
CREATE INDEX IF NOT EXISTS ix_opportunities_publication_date ON opportunities (publication_date);
CREATE INDEX IF NOT EXISTS ix_opportunities_review_state ON opportunities (review_state);
CREATE INDEX IF NOT EXISTS ix_opportunities_run_id ON opportunities (run_id);
CREATE INDEX IF NOT EXISTS ix_opportunities_score_band ON opportunities (score_band);
CREATE INDEX IF NOT EXISTS ix_opportunities_suppressed ON opportunities (suppressed);
CREATE INDEX IF NOT EXISTS ix_opportunities_trademark_number ON opportunities (trademark_number);
CREATE INDEX IF NOT EXISTS ix_opportunity_band_score ON opportunities (score_band, launchtrace_score);

CREATE TABLE IF NOT EXISTS pipeline_runs (
	id SERIAL NOT NULL, 
	run_id VARCHAR(64) NOT NULL, 
	mode VARCHAR(32) NOT NULL, 
	journal_number VARCHAR(32), 
	source_name VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	counts JSON NOT NULL, 
	warnings JSON NOT NULL, 
	blocked_reason VARCHAR(256), 
	csv_path VARCHAR(512), 
	email_html_path VARCHAR(512), 
	qa_report_path VARCHAR(512), 
	approved_at TIMESTAMP WITH TIME ZONE, 
	approved_by VARCHAR(128), 
	delivery_status VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_pipeline_runs_journal_number ON pipeline_runs (journal_number);
CREATE UNIQUE INDEX ix_pipeline_runs_run_id ON pipeline_runs (run_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_status ON pipeline_runs (status);

CREATE TABLE IF NOT EXISTS sample_requests (
	id SERIAL NOT NULL, 
	work_email VARCHAR(256) NOT NULL, 
	company VARCHAR(256) NOT NULL, 
	contact_name VARCHAR(256), 
	supplier_type VARCHAR(64), 
	source_ip_hash VARCHAR(64), 
	status VARCHAR(32) NOT NULL, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_sample_request_email UNIQUE (work_email)
);

CREATE INDEX IF NOT EXISTS ix_sample_requests_status ON sample_requests (status);
CREATE INDEX IF NOT EXISTS ix_sample_requests_work_email ON sample_requests (work_email);

CREATE TABLE IF NOT EXISTS score_events (
	id SERIAL NOT NULL, 
	dedupe_key VARCHAR(64) NOT NULL, 
	run_id VARCHAR(64) NOT NULL, 
	score INTEGER NOT NULL, 
	band VARCHAR(16) NOT NULL, 
	reasons JSON NOT NULL, 
	negative_reasons JSON NOT NULL, 
	scoring_config_version VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_score_events_dedupe_key ON score_events (dedupe_key);
CREATE INDEX IF NOT EXISTS ix_score_events_run_id ON score_events (run_id);

CREATE TABLE IF NOT EXISTS suppression_rules (
	id SERIAL NOT NULL, 
	rule_type VARCHAR(32) NOT NULL, 
	value VARCHAR(512) NOT NULL, 
	reason VARCHAR(512), 
	created_by VARCHAR(128), 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_suppression_type_value UNIQUE (rule_type, value)
);

CREATE INDEX IF NOT EXISTS ix_suppression_rules_rule_type ON suppression_rules (rule_type);
CREATE INDEX IF NOT EXISTS ix_suppression_rules_value ON suppression_rules (value);

CREATE TABLE IF NOT EXISTS web_enrichment (
	id SERIAL NOT NULL, 
	dedupe_key VARCHAR(64) NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	attempted BOOLEAN NOT NULL, 
	website VARCHAR(512), 
	contact_page VARCHAR(512), 
	brand_description TEXT, 
	website_maturity VARCHAR(32), 
	products_on_sale BOOLEAN, 
	marketplace_presence BOOLEAN, 
	major_retailer_presence BOOLEAN, 
	social_presence BOOLEAN, 
	retail_presence VARCHAR(32) NOT NULL, 
	launch_evidence JSON NOT NULL, 
	evidence_urls JSON NOT NULL, 
	error VARCHAR(512), 
	enriched_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_web_enrichment_dedupe_key ON web_enrichment (dedupe_key);

CREATE TABLE IF NOT EXISTS webhook_events (
	id SERIAL NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	event_id VARCHAR(128) NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	processed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	payload_summary JSON NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_webhook_provider_event UNIQUE (provider, event_id)
);

CREATE INDEX IF NOT EXISTS ix_webhook_events_event_id ON webhook_events (event_id);

CREATE TABLE IF NOT EXISTS customer_preferences (
	id SERIAL NOT NULL, 
	customer_id INTEGER NOT NULL, 
	recipient_email VARCHAR(256) NOT NULL, 
	sector VARCHAR(32) NOT NULL, 
	min_score_band VARCHAR(16) NOT NULL, 
	regions JSON NOT NULL, 
	product_categories JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_customer_recipient UNIQUE (customer_id, recipient_email), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_customer_preferences_recipient_email ON customer_preferences (recipient_email);

CREATE TABLE IF NOT EXISTS deliveries (
	id SERIAL NOT NULL, 
	run_id VARCHAR(64) NOT NULL, 
	journal_number VARCHAR(32) NOT NULL, 
	customer_id INTEGER, 
	recipient_email VARCHAR(256) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	provider_message_id VARCHAR(128), 
	opportunity_count INTEGER NOT NULL, 
	error VARCHAR(512), 
	idempotency_key VARCHAR(128) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_delivery_idempotency UNIQUE (idempotency_key), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_deliveries_idempotency_key ON deliveries (idempotency_key);
CREATE INDEX IF NOT EXISTS ix_deliveries_journal_number ON deliveries (journal_number);
CREATE INDEX IF NOT EXISTS ix_deliveries_recipient_email ON deliveries (recipient_email);
CREATE INDEX IF NOT EXISTS ix_deliveries_run_id ON deliveries (run_id);
CREATE INDEX IF NOT EXISTS ix_deliveries_status ON deliveries (status);

CREATE TABLE IF NOT EXISTS trademark_records (
	id SERIAL NOT NULL, 
	journal_id INTEGER, 
	journal_number VARCHAR(32) NOT NULL, 
	dedupe_key VARCHAR(64) NOT NULL, 
	trademark_number VARCHAR(32) NOT NULL, 
	mark_text VARCHAR(512), 
	mark_type VARCHAR(64), 
	mark_category VARCHAR(64), 
	filing_date DATE, 
	publication_date DATE, 
	applicant_name VARCHAR(512), 
	applicant_country VARCHAR(128), 
	applicant_region VARCHAR(128), 
	applicant_postcode_area VARCHAR(16), 
	nice_classes JSON NOT NULL, 
	goods_text TEXT, 
	goods_text_available BOOLEAN NOT NULL, 
	status VARCHAR(64), 
	series_count INTEGER NOT NULL, 
	source_url VARCHAR(512), 
	source_name VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tm_journal_number UNIQUE (journal_number, trademark_number), 
	FOREIGN KEY(journal_id) REFERENCES journals (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_tm_applicant_journal ON trademark_records (applicant_name, journal_number);
CREATE INDEX IF NOT EXISTS ix_trademark_records_applicant_name ON trademark_records (applicant_name);
CREATE INDEX IF NOT EXISTS ix_trademark_records_dedupe_key ON trademark_records (dedupe_key);
CREATE INDEX IF NOT EXISTS ix_trademark_records_filing_date ON trademark_records (filing_date);
CREATE INDEX IF NOT EXISTS ix_trademark_records_journal_number ON trademark_records (journal_number);
CREATE INDEX IF NOT EXISTS ix_trademark_records_publication_date ON trademark_records (publication_date);
CREATE INDEX IF NOT EXISTS ix_trademark_records_trademark_number ON trademark_records (trademark_number);


COMMIT;

