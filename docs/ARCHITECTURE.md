# Architecture: the data model across weeks

LaunchTrace processes one UKIPO Trade Marks Journal a week. Until the
structural upgrade, everything it stored was per journal: a trade mark record,
the opportunity scored from it, and a score event per run. This document
describes the layer added on top of that so a brand can be followed from week
to week, and the rules that keep that history honest enough to backtest.

```
journals ── trademark_records                      (source: what UKIPO published)
                │
 weekly run ────┼── company_matches, web_enrichment (derived, latest per record)
                │
                └── opportunities ──┐               (one row per journal × record)
                    score_events ───┤ brand_id      (one row per run × record)
                                    ▼
                                  brands ── observations   (append-only facts)
                                         └─ stage_changes  (launch-stage transitions)
```

Tables, columns and the `src/brands.py` API are the contract later work builds
on (see PLAN.md, "Phase 1 — foundation"). Names do not change.

## Brands

`brands` holds one row per real-world brand or company, across journal weeks.

| column | meaning |
| --- | --- |
| `id` | internal stable id |
| `brand_uid` | public stable id: `b_` + 16 hex of sha256 of the key the brand was **created** with. It never changes, even when the key's basis is upgraded (below). |
| `brand_key` | the dedupe identity (unique) |
| `brand_name` | primary mark text (empty string if the first mark had none) |
| `company_number`, `company_name` | from a confident Companies House match only |
| `applicant_type` | `corporate` / `natural_person` / `unknown` |
| `applicant_key_hash` | sha256 of the normalised applicant name |
| `product_category`, `region`, `website` | latest known; `region` is a region, never finer; `website` is a verified site only |
| `first_seen_journal`, `first_seen_at`, `first_filing_date` | see "first and last seen" |
| `last_seen_journal`, `current_stage`, `current_score`, `current_band` | state as of the newest journal seen |
| `last_checked_at` | last time a web search (later: a rescan) looked at this brand |
| `launched_at` | set by later rescan/outcome work when a launch is detected |
| `created_at`, `updated_at` | row bookkeeping |

### Dedupe rules

`src.brands.brand_key_for(opp)` and the lookup in `upsert_brand_from_opportunity`:

1. **Confident Companies House match** (`opp.company.matched` and a company
   number) → key `ch:<COMPANY NUMBER>`. Looked up first by `company_number`,
   so it also finds a brand that started life as `tm:`.
2. **Otherwise** → key `tm:<sha256(normalise_text(mark) | normalise_company_name(applicant))[:24]>`.
   Case, punctuation, accents and legal suffixes ("Ltd", "Limited") do not
   split a brand.
3. **Upgrade, never re-key.** A brand first seen as `tm:` that later arrives
   with a company number keeps its `id`, `brand_uid` and `brand_key`; it gains
   `company_number`, and from then on a lookup by that number finds it.
4. **One company, one brand.** Several marks from one matched company resolve
   to the same row — the same rule `src/deliver/consolidation.py` uses for the
   customer feed.

Known limitation (D-105): an unmatched applicant's different marks are
separate `tm:` brands, and if two such brands later match the same company only
the first gains the company number; nothing is merged automatically.

### Who gets a brand

Every scored opportunity in `result.opportunities`, **including suppressed
ones**. A backtest needs the leads we rejected as much as those we sent, and a
rescan needs to watch them. Records dropped before scoring (wrong goods,
portfolio filers, dissolved companies) do not get brands.

### Privacy

The applicant's name is **never** stored on `brands`. An applicant may be a
private individual; only `applicant_key_hash` (sha256 of the normalised name)
is kept, which lets a later run recognise the same applicant without holding
the name. Tests assert the raw name appears in no column of any brand row.

### First and last seen

* `first_seen_journal` only ever moves **earlier**: if an older journal is
  back-filled after a newer one, it becomes the older journal's number.
  Journal numbers are compared chronologically (`journal_sort_key`).
* `first_filing_date` is the earliest filing date seen.
* `first_seen_at` is the wall-clock time LaunchTrace first created the row. It
  is when *we* first saw the brand and never changes.
* `last_seen_journal` and `current_*` follow the **newest** journal seen. A
  back-fill of an older week does not roll the brand's current state back.
  Within one run, the opportunity that best represents the company
  (deliverable first, then highest score) sets `current_*`.

### Stage changes

When a brand appears in a **later** journal with a different `launch_stage`,
`sync_brands` appends a `stage_changes` row (`from_stage`, `to_stage`,
`detected_at`, `evidence` with the journal and mark, `run_id`). Two marks of one
company in the same week with different stages are not a transition. Later
work (rescan) records its own transitions with `record_stage_change`.

## Observations

`observations` is an **append-only** log of facts about a brand. There is no
update or delete function: what we saw, and when, is the record. A changed fact
is a new row. (Retention, when it comes, is the only thing that deletes.)

| column | meaning |
| --- | --- |
| `source` | where the fact came from: `trademark`, `companies_house`, `web_search`, `score` today; `rdap`, `dns`, `homepage`, `rescan`, `outcome` reserved |
| `signal` | a name registered in `config/signals.json` — unregistered names are refused |
| `value` | JSON: a scalar or a small object; `null` is meaningful (searched, not found) |
| `observed_at` | when **we** looked |
| `source_date` | when the fact was **true in the world** (filing date, incorporation date, journal publication date) |
| `point_in_time_safe` | see below |
| `run_id`, `journal_number` | provenance |

What `sync_brands` records each run:

| source | signals | `source_date` | PIT-safe |
| --- | --- | --- | --- |
| trademark | `filing_date`, `publication_date`, `nice_classes` | filing date; publication date | yes |
| companies_house (matched only) | `incorporation_date` | incorporation date | yes |
| companies_house (matched only) | `company_match`, `company_status`, `sic_codes`, `accounts_category` | — | no |
| web_search (only if a search ran) | `website`, `website_maturity`, `retail_presence`, `marketplace_presence`, `major_retailer_presence`, `social_presence`, `products_on_sale`, `launch_evidence` | — | no |
| score | `launchtrace_score`, `launch_stage` | — | no |

Re-running a journal appends a fresh set of observations with a new
`observed_at`; that is intended (it records that we looked again). Within one
run an identical fact for the same brand is written once, so a company with
three marks gets one `incorporation_date` observation but three `filing_date`
observations (one per mark).

### Point-in-time safety

A backtest asks "what could we have known on date D?". An observation is
`point_in_time_safe` only if its `source_date` is when the fact became true
**and** that date could have been known then. A filing date is; a company's
SIC codes as read today are not (companies change them), and neither is our
own score (it depends on today's code and on non-PIT inputs).

* The default for each signal comes from `config/signals.json`; a caller may
  override it.
* An observation with no `source_date` is always stored as not PIT-safe — it
  cannot be placed in time.
* `observations_as_of(session, brand_id, cutoff)` returns PIT-safe
  observations with `source_date <= cutoff`. With `pit_safe_only=False` it also
  returns non-PIT observations *observed* on or before the cutoff — the best
  that can be said for an undated fact, and never good enough for a backtest.
* `latest_observations(session, brand_id, as_of=None)` is the newest
  observation per signal by `observed_at` — what LaunchTrace had seen by then.

### The signals registry

`config/signals.json` groups signals by source (`trademark`, `companies_house`,
`web_search`, `score`, and the empty `domain` and `rescan` groups reserved for
later work). Each signal declares `source`, `point_in_time_safe` and
`description`. Names are unique across groups (a test enforces it).

## Weekly run: idempotency and cost

* `weekly` resolves which journal it would process before doing anything. If
  that journal is already `processed` it logs `weekly.already_processed`,
  prints one line and exits 0 — no download, no search spend, no run row.
  `--force` (also on `backfill`) reprocesses. If the journal cannot be
  resolved, the run proceeds and fails closed exactly as before.
* "First trade mark for this applicant" is judged against applicants seen in
  journals **before** the one being processed, so re-running a week gives the
  same scores. Journal numbers are zero-padded `YYYY-NNN` from every source,
  so the SQL string comparison is chronological.
* Every search-provider call goes through a `SearchBudget`
  (`src/enrich/budget.py`), is counted in `FunnelCounts.search_calls` and
  persisted in `pipeline_runs.counts`. The allowance per run is the smaller of
  `config/costs.json` → `search_guard.max_calls_per_run` (env
  `SEARCH_MAX_CALLS_PER_RUN` overrides) and what is left of `monthly_budget`
  over the last `window_days`. Running out stops searching for the rest of the
  run; unsearched records are scored as if no search provider were configured;
  the run completes with a warning.
* At the end of every weekly run, journal download caches older than
  `config/operations.json` → `cache_max_age_days` are pruned. Housekeeping
  never fails a run.

## Database migrations

Alembic owns the schema. The environment lives inside the package at
`src/db/alembic/` (so the Docker image, which copies `src/`, can migrate
itself); `alembic.ini` at the repository root is for the CLI. The database is
`DATABASE_URL`, or the local SQLite file `data/local/launchtrace.sqlite` when
it is unset.

```bash
python -m src.pipeline init-db          # upgrade to head; adopts a pre-Alembic database
alembic upgrade head                    # the same upgrade, from the CLI
alembic current                         # which revision a database is at
alembic revision -m "add outcomes table" --autogenerate   # then review the file
alembic downgrade -1                    # step back one revision
```

* `0001_baseline` — every table as it stood before Alembic.
* `0002_brands` — `brands`, `observations`, `stage_changes`, and nullable
  `brand_id` on `opportunities` and `score_events`. Downgrading removes only
  these; baseline rows are untouched.
* Revision ids are explicit and ordered (`0003_*` next). Parallel branches each
  chain off the current head; whoever merges re-chains `down_revision`.

`init_db()` handles four starting points without ever dropping data: already
at head (one cheap query, nothing else); an empty SQLite file (`create_all` +
`stamp head`, proven identical to the migrations by
`tests/test_migrations.py`); a pre-Alembic database (brought additively up to
the frozen baseline in `src/db/baseline.py`, stamped `0001_baseline`, then
upgraded — or stamped `head` directly if it already has every current table
and column); anything else, including an empty PostgreSQL database
(`upgrade head`).

`migrations/0001_initial.sql` is the full current schema generated from the
models, for pasting into the Supabase SQL editor on a **new** database. CI
checks it is in step with the models, and runs upgrade → downgrade → upgrade on
a real PostgreSQL.
