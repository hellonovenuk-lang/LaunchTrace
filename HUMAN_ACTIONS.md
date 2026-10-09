# Human actions

Everything the structural upgrade could not do because it needs a person: a
secret, an account, a hosting choice, a legal decision or a registration.
The system runs without each of these and degrades as described; nothing here
blocks the code. Final ordering is set in Phase 4.

<!-- Agents: append under the heading for your phase. Say what to do, where,
     and what it unlocks. Do not write legal wording. -->

## Orchestrator

- Decide whether the remote branch should be renamed from
  `claude/launchtrace-structural-upgrade-8umgor` to `structural-upgrade`
  (`git push origin claude/launchtrace-structural-upgrade-8umgor:structural-upgrade`).

## Phase 1 — foundation

- **Create the `DATABASE_URL` repository secret (recommended).** Create a
  Supabase project in the **London (eu-west-2)** region, copy the PostgreSQL
  connection string (Project Settings → Database → Connection string → URI,
  with your database password filled in) and add it in GitHub → Settings →
  Secrets and variables → Actions → New repository secret, named
  `DATABASE_URL`. Then run `DATABASE_URL=... python -m src.pipeline init-db`
  once from a checkout (the first weekly run would also do it). Unlocks:
  durable history — "already processed", known applicants, the monthly search
  budget, brands and observations survive regardless of GitHub's cache. If
  you previously created the schema by pasting `migrations/0001_initial.sql`,
  `init-db` adopts that database in place; do not paste the new file into an
  existing database.
- **Know the limits of running without it.** Without `DATABASE_URL` the
  weekly workflow carries the SQLite file between runs in the GitHub Actions
  cache. GitHub evicts cache entries not accessed for **7 days** and when the
  repository passes its **10 GB** cache limit. A weekly schedule normally
  touches it every week, but one missed week (or a long pause) loses the
  history silently and the next run starts from an empty database. Each run
  therefore also uploads the file as an artifact `launchtrace-db-<run number>`
  kept for 14 days. That copy is for manual recovery and inspection (download
  it and open it locally); nothing restores it automatically. The durable fix
  is to set `DATABASE_URL`.
  The backfill workflow does not carry the SQLite file at all.
- **Optional: `SEARCH_MAX_CALLS_PER_RUN` repository variable** (Settings →
  Secrets and variables → Actions → Variables). Overrides the per-run search
  cap (default 200 in `config/costs.json` → `search_guard`). Check that
  `max_calls_per_run` and `monthly_budget` (900 over 30 days) fit your search
  provider's actual quota and price before enabling a paid plan.
- **Forcing a re-run** of a journal that has already been processed is now a
  manual choice: Actions → Weekly pipeline → Run workflow → tick `force`. It
  spends search calls again.

## Phase 2 — compliance

Nothing below is legal wording, and nothing here was filled in by the build.
Each item is a decision or a fact only the owner (or a solicitor) can supply.

### Placeholders to fill (they are published as-is on `/privacy` and `/terms`)

The website renders `docs/PRIVACY.md`, `docs/TERMS.md` and `docs/ATTRIBUTION.md`
directly (`src/web/app.py` → `_doc_page`), so every placeholder in the first
two is visible on the live site until it is replaced. `ATTRIBUTION.md` has none.
No website or email template contains a bracket placeholder (the outreach
drafts' `[YOUR NAME]`, `[PHONE]`, `[WEBSITE]`, `[N]` tokens are filled per draft
and flagged by `render_draft` warnings).

| File:line | Placeholder | What the owner must supply |
| --- | --- | --- |
| docs/PRIVACY.md:7 | `[DATE]` | Date the notice takes effect ("last updated"). |
| docs/PRIVACY.md:7 | `[YOUR COMPANY NAME]` | Legal entity name of the data controller. |
| docs/PRIVACY.md:7 | `[REGISTERED ADDRESS]` | Registered office address of that entity. |
| docs/PRIVACY.md:8 | `[NUMBER]` | Companies House company number. |
| docs/PRIVACY.md:8, :74, :102 | `[PRIVACY EMAIL]` | A monitored address for privacy requests and opt-outs. |
| docs/PRIVACY.md:54 | `[6]` years | Customer-record retention after a subscription ends (accounting) — confirm with your accountant. Not enforced by `retention` (customers are never touched automatically). |
| docs/PRIVACY.md:69 | `[24]` months | Sample-request / prospect retention from last interaction. Must match `config/retention.json` → `sample_requests.months` and `prospect_contacts.months`. |
| docs/PRIVACY.md:83 | `[Supabase / your database host]`, `[region]` | Actual database host and its region (e.g. Supabase, London). |
| docs/PRIVACY.md:86 | `[Your search API provider]`, `[region]` | Search provider actually enabled (or remove the row if none). |
| docs/PRIVACY.md:87 | `[Your LLM provider]`, `[region]` | LLM provider actually enabled (or remove the row if `LLM_PROVIDER=none`). |
| docs/PRIVACY.md:88 | `[Your website host]`, `[region]` | Where the website is hosted (e.g. Fly.io region). |
| docs/TERMS.md:6 | `[DATE]` | Date the terms take effect. |
| docs/TERMS.md:6 | `[YOUR COMPANY NAME]` | Legal entity name contracting with customers. |
| docs/TERMS.md:7 | `[NUMBER]` | Companies House company number. |
| docs/TERMS.md:7 | `[ADDRESS]` | Registered office address. |
| docs/TERMS.md:111 | `[YOUR CONTACT EMAIL]` | Customer contact address. |
| docs/LEGITIMATE_INTERESTS_ASSESSMENT.md:9 | `[NAME]`, `[DATE]`, `[DATE + 12 months]` | Who assessed the LIA, when, and the review date. |
| docs/LEGITIMATE_INTERESTS_ASSESSMENT.md:167–170 | `[NAME]`, `[DATE]`, `[NAME / not yet reviewed]`, `[DATE]` | Sign-off table: assessor, date, reviewing solicitor, next review. |
| docs/DATA_RETENTION.md:12 | `[24]` months | `trademark_records` period → `config/retention.json` `trademark_records_individual_names.months`. |
| docs/DATA_RETENTION.md:13 | `[24]` months | `company_matches` → `company_matches_individual_names.months`. |
| docs/DATA_RETENTION.md:14 | `[12]` months | `web_enrichment` → `web_enrichment.months`. |
| docs/DATA_RETENTION.md:15 | `[24]` months | `opportunities` → `opportunities_individual_names.months`; also decide whether `opportunities`/`score_events` rows should be **deleted** after the period (today only individuals' names are removed — D-404). |
| docs/DATA_RETENTION.md:16 | `[12]` months | `pipeline_runs`, `errors` → `pipeline_runs.months`, `errors.months`. |
| docs/DATA_RETENTION.md:17 | `[6]` years | Customer records after the subscription ends (not automated). |
| docs/DATA_RETENTION.md:18 | `[24]` months | `deliveries` → `deliveries.months`. |
| docs/DATA_RETENTION.md:19 | `[24]` months | `sample_requests` → `sample_requests.months`. |
| docs/DATA_RETENTION.md:21 | `[24]` months | `webhook_events` → `webhook_events.months`. |
| docs/DATA_RETENTION.md:22 | `[24]` months | `lead_feedback` → `lead_feedback_notes.months` (notes cleared; rows kept — D-406). |
| docs/DATA_RETENTION.md:23 | `[24]` months | `prospect_state` → `prospect_contacts.months`. |
| docs/COMPLIANCE_REVIEW.md:37, :38 | `[24]` months | Same decisions as `lead_feedback` / `prospect_state` above; keep the review record consistent. |
| docs/COMPLIANCE_REVIEW.md:109 | "Every `[BRACKET]` placeholder" | Tracking item; closed when the rows above are done. |

### Decisions and registrations

- **Confirm every retention period** in `config/retention.json`. Each class
  carries `"status": "placeholder pending owner decision"`; the defaults are the
  bracketed values above. Change the number (or set `"enabled": false`), then
  change the status text, and make `docs/DATA_RETENTION.md` and
  `docs/PRIVACY.md` say the same. The weekly workflow now runs
  `python -m src.pipeline retention --apply` after every run, so the defaults
  are **enforced from the first run after merge**. To preview first, run
  `python -m src.pipeline retention` (dry run) against the live database. If
  you want nothing enforced until you have decided, set `"enabled": false` on
  every class before merging.
- **Register with the ICO (data protection fee)** as a controller, before
  processing personal data in earnest: ico.org.uk → "Pay the data protection
  fee" (tier 1, about £40–60/year). Record the registration number where your
  solicitor advises (the privacy notice currently has no slot for it — decide
  whether to add one).
- **Legal review** by a solicitor of `docs/PRIVACY.md`, `docs/TERMS.md` and
  `docs/LEGITIMATE_INTERESTS_ASSESSMENT.md` (and `docs/DATA_MAP.md`, which
  states every lawful basis as "assumed — pending legal review"). Points to
  raise, without this build proposing wording:
  - The privacy notice (§5) says an "individual named in the LaunchTrace feed"
    can ask to be removed. Since this phase the feed and every other output
    never name an individual applicant (D-400); whether the notice should
    change is a legal call.
  - The privacy notice (§4 note) says the LLM is sent the applicant name. That
    is still true for natural-person applicants that reach classification.
  - Whether anonymising (rather than deleting) old `trademark_records`,
    `opportunities` and `company_matches` rows satisfies the retention promise.
- **Public company-level feed and the LIA.** Decide (with the solicitor)
  whether publishing a public, company-level feed (Phase 2 public-feed work,
  `src/feed`) is within the purposes and balancing test in
  `docs/LEGITIMATE_INTERESTS_ASSESSMENT.md`, which today assesses delivery to
  paying subscribers and LaunchTrace's own outreach. Even company-level, a
  sole trader's or one-person company's name can be personal data
  (`docs/DATA_MAP.md`). Until decided, consider not deploying the public feed.
- **GitHub artifacts hold data.** `weekly-report-*` (60 days) contains the run
  CSV/email/QA report (the QA report names applicants only for rejected major
  brand owners, which are corporate). `launchtrace-db-*` (14 days) is the
  whole database, individuals' names included. Confirm those periods are
  acceptable, and who can download them (anyone with read access to the
  repository).
- **Committed validation reports contain individual applicants' names.**
  `reports/validation/**/result.json`, `reports/validation/rejections.csv` and
  `reports/validation/4_week_summary.md` are in the repository and include
  applicant names, some of natural persons (public UKIPO data, but personal
  data all the same); older committed `weekly_email.html` / `opportunities.csv`
  files were produced before this fix. Retention cannot reach the repository
  or its history. Decide whether to remove them (and whether history must be
  rewritten — a force-push this build will not do), or keep them as
  validation evidence and record why.
