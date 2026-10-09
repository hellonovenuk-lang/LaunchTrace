# Data map

Every place LaunchTrace keeps data, what in it is (or may be) personal data,
where it comes from, how long it is kept, and where it ends up. Internal
working document, prepared during the structural upgrade (Phase 2,
compliance). **It is not legal advice.** Every lawful basis below is
**assumed — pending legal review**; the periods are the placeholders in
`config/retention.json` (each marked "placeholder pending owner decision") and
`docs/DATA_RETENTION.md`. Open decisions are in `HUMAN_ACTIONS.md`
("Phase 2 — compliance").

Two facts shape most rows:

* **A trade mark applicant can be a private individual.** UKIPO publishes their
  name. LaunchTrace stores it (it drives the "first trade mark for this
  applicant" signal) but never outputs it: every renderer goes through
  `src/privacy.py`, and `tests/test_no_individual_names.py` checks every
  output (DECISIONS.md D-400). An applicant is treated as an individual when it
  is typed `natural_person` **or** its name has no corporate suffix.
* **A company name can be personal data** when it is a sole trader's or a
  one-person company's own name ("Jane Smith Ltd"). Company names are shown as
  Companies House publishes them; this is a residual risk for the LIA, not
  something the code can detect.

Retention keys refer to `config/retention.json` → `classes.<key>`.
"Never" means retention never touches the store.

## Database tables (`src/db/tables.py`)

| Table | Personal data (or may be) | Lawful basis (assumed — pending legal review) | Source | Retention | Output to |
| --- | --- | --- | --- | --- | --- |
| `journals` | None (journal number, dates, checksum) | n/a | UKIPO | Indefinite; never touched (duplicate-run guard) | `status` CLI |
| `trademark_records` | `applicant_name` when the applicant is an individual; `applicant_region`, `applicant_postcode_area` (area only) for an individual | Legitimate interests (B2B lead generation from a public register) | UKIPO journal / open data | `trademark_records_individual_names`: individual names set to NULL after [24] months; rows and corporate names kept (D-404, D-405) | Internal only (scoring input). Never rendered. |
| `company_matches` | `applicant_name` (individual); `match_evidence` can quote words of the name; `error` can quote a request URL containing it | Legitimate interests | Companies House API / bulk index | `company_matches_individual_names`: name, evidence and error cleared after [24] months for individuals | Internal only |
| `web_enrichment` | `brand_description`, `evidence_urls`, `launch_evidence` may describe a sole trader; `website` may be a personal-name domain | Legitimate interests | Public web search results | `web_enrichment`: deleted [12] months after `enriched_at` | Website URL and evidence URLs in the weekly CSV/email; descriptions internal |
| `opportunities` | `applicant_name` + `applicant_type` (individuals); `company_name` (sole-trader risk) | Legitimate interests | Derived by the pipeline | `opportunities_individual_names`: individual names set to NULL after [24] months; rows kept | Weekly email/CSV, send/regenerate-csv, sample pack, previews, outreach drafts, landing-page examples (confirmed companies only, D-402), `opportunities` CLI — always via `src/privacy.py`, never the individual's name |
| `score_events` | None (scores, reason texts) | n/a | Derived | Not automated (no personal data); doc period [24] months is an owner decision | Internal |
| `customers` | `contact_name`; `company` (sole-trader risk); Stripe ids | Contract (and legal obligation for accounting records) | Customer sign-up / Stripe | Life of subscription + [6] years; **never touched by retention** (owner decision) | Admin page, CLI, transactional emails |
| `customer_preferences` | `recipient_email` | Contract | Customer | With the customer; never touched by retention; removed on opt-out | Delivery |
| `deliveries` | `recipient_email` | Contract / legitimate interests (proof of delivery, no double-send) | Delivery service | `deliveries`: deleted after [24] months, except a non-cancelled customer's | Internal, CLI |
| `suppression_rules` | `value` may be an email address or an applicant/company name | Legal obligation / legitimate interests (honouring objections) | Operator, opt-out form | **Indefinite; never touched** | Internal (filters) |
| `pipeline_runs` | `warnings` may quote record details | Legitimate interests (operations) | Pipeline | `pipeline_runs`: deleted after [12] months | `status` CLI, admin page |
| `errors` | `message` may quote record details | Legitimate interests (operations) | Pipeline | `errors`: deleted after [12] months | `errors` CLI, admin page |
| `sample_requests` | `work_email`, `contact_name`, `company`, `source_ip_hash` (salted hash, never the IP) | Legitimate interests (responding to a business enquiry) | Website form / API | `sample_requests`: deleted [24] months after the later of request and send | Admin page; sample email to the requester |
| `lead_feedback` | `note` (free text by a customer, meant to be about a brand) | Contract / legitimate interests | Feedback form, operator | `lead_feedback_notes`: note cleared after [24] months; state kept | Internal (`feedback` CLI) |
| `prospect_state` | `generic_contact_email`, `named_contact`, `decision_maker_role`, `notes`, `suppression_reason` | Legitimate interests (B2B direct marketing; PECR corporate-subscriber position — confirm) | Operator research / replies | `prospect_contacts`: the four contact fields cleared [24] months after last interaction; row and opt-out flag kept; customers' prospects skipped | Sales CLI, outreach drafts (greeting uses `named_contact`), backups |
| `prospect_suppressions` | `value` (email/domain/company), `company_name` | Legal obligation / legitimate interests | Opt-outs | **Indefinite; never touched** (insert-only) | Internal (blocks imports and drafts) |
| `webhook_events` | `payload_summary` (Stripe ids; no card data) | Contract / legal obligation (payment audit) | Stripe | `webhook_events`: deleted after [24] months | Internal |
| `brands` | None by design: no applicant name, only `applicant_key_hash` (sha256 of the normalised name); `company_name` (sole-trader risk); `region` only | Legitimate interests | Derived (`src/brands.py`) | Never touched (no personal data; a test asserts no name column) | Public feed and movers digest (company-level, confirmed companies only), backtests, rescans |
| `observations` | None by design (values are dates, classes, company number, scores, web flags) | Legitimate interests | Derived | Never touched | Backtests, rescans |
| `stage_changes` | None (stages; evidence holds journal and mark text) | Legitimate interests | Derived (weekly run, rescan) | Never touched | Rescans, movers digest |
| `outcomes` | None (backtest launch labels per brand and horizon; evidence lists observations) | Legitimate interests | Derived (`backtest label`) | Never touched | Backtest reports |

The `applicant_key_hash` on `brands` is pseudonymous, not anonymous: anyone
holding the applicant's name can recompute it. It is kept so a brand can be
recognised across weeks without storing the name.

## Outside the database

| Store | Contents (personal data in bold) | Retention | Notes |
| --- | --- | --- | --- |
| `data/cache/journals/`, `data/cache/opendata/` | Raw journals — **applicant names, incl. individuals** | `config/operations.json` → `cache_max_age_days` (30), pruned every weekly run | Re-downloadable public data |
| `data/cache/` (Companies House API cache, bulk index) | Corporate fields only (no officers, PSCs, addresses of directors) | Own lifecycle (monthly index) | |
| `data/cache/rdap/` | The IANA RDAP bootstrap file (no personal data) | Refreshed every 7 days | |
| `data/cache/backtest_runs/` | Backtest run outputs (CSV, email, QA report), as `reports/runs/` | Not pruned; delete freely | |
| `data/journals/`, `data/web_evidence/` (in git) | Recorded journals and web evidence used by tests/stability — **applicant names** | Kept (test fixtures) | Public data; see HUMAN_ACTIONS on committed files |
| `data/local/launchtrace.sqlite` | The whole database (all of the above) | As the tables | Carried between workflow runs via the Actions cache (evicted after 7 days unused) |
| `reports/runs/<journal>/` | `opportunities.csv`, `weekly_email.html`, `qa_report.json` | Gitignored; delete freely | Customer outputs contain no individual's name. The QA report names applicants only for rejected major brand owners (corporate). |
| `reports/outbox/` | Rendered emails not sent — **recipient addresses**, lead data | Gitignored; delete freely | |
| `reports/previews/`, `reports/samples/`, `reports/outreach_drafts/` | Prospect company names, **named contact greeting** in drafts, lead data (no individual applicant names) | Gitignored; delete freely (`DATA_RETENTION.md`) | |
| `reports/backups/` | Prospect state and suppression exports — **contact fields** | Gitignored; "do not keep a backup longer than the data in it" | Retention does not reach backups; owner deletes |
| `reports/validation/` (in git) | Validation runs — `result.json`, `rejections.csv`, `4_week_summary.md` contain **applicant names, some of individuals** | In git | Owner decision (HUMAN_ACTIONS) |
| `outreach/prospects_seed.csv` (in git) | Company research only; no personal fields by design | Kept | |
| `public/` (public feed output, `src/feed`; also served at `/feed/`) | Company-level only by design | Gitignored build output; owner decision once published | Whether publishing is within the LIA is open (HUMAN_ACTIONS). Covered by `tests/test_no_individual_names.py` (`_feed_renderer`). |
| GitHub artifact `weekly-report-<n>` | `reports/runs/**` + `pipeline.log` | 60 days | Readable by anyone with repository read access |
| GitHub artifact `public-feed-<n>` | The built public feed — company level only | 30 days | Same |
| GitHub artifact `email-outbox-<n>` | `reports/outbox/` — **recipient addresses**, lead data (e.g. the movers digest) | 14 days | Same |
| GitHub artifact `launchtrace-db-<n>` | The whole SQLite database — **all personal data above** | 14 days | Same |
| GitHub artifact `backfill-<n>` | `reports/validation/**`, `reports/runs/**` — **applicant names in validation output** | 90 days | Same |
| Logs (stdout, `pipeline.log`) | See below | With the artifact (60 days) / host log retention | |

## Logs

Production log events were checked for individuals' names (D-408):

* `ch.lookup_failed` (WARNING) — **fixed**: an individual's name is replaced
  and the error text (which can carry the request URL) is reduced to the
  exception type. Corporate names are still logged.
* `email.sent`, `email.send_failed`, `email.rendered_not_sent` (INFO/ERROR) log
  the **recipient address** (customers, sample requesters). Kept: it is the
  delivery audit trail; owner may decide otherwise.
* `sample.requested` (INFO) logs the requester's company name (sole-trader
  risk), not the email address.
* `web.search_failed` (WARNING) logs the search term: brand name or company
  name, not the applicant name.
* No log event writes `named_contact`, prospect emails, or an individual
  applicant's name. The structured `trademark=` fields carry trade mark
  numbers only.
