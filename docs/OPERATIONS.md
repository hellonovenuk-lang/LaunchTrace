# Operations

How to run LaunchTrace week to week: the database, the Friday workflow, re-runs,
the cost guard, publishing the public feed, the backtest, retention, the
stability check, and what the weekly run asks of outside services.

Every number here is taken from `config/*.json` or `src/settings.py` as they
stand; if you change a config value, the corresponding figure here changes
with it.

---

## 1. The database

LaunchTrace reads `DATABASE_URL`. When it is set, that database is used
(PostgreSQL; Supabase is what the docs assume). When it is unset, the local
SQLite file `data/local/launchtrace.sqlite` is used.

### Setting `DATABASE_URL` (recommended for the scheduled run)

1. Create a Supabase project in the **London (eu-west-2)** region and set a
   database password.
2. Project Settings → Database → Connection string → **URI**. Replace
   `[YOUR-PASSWORD]` with the password.
3. Locally: put it in `.env` as `DATABASE_URL=postgresql://...`.
   On GitHub: Settings → Secrets and variables → Actions → **New repository
   secret**, name `DATABASE_URL`.
4. Create the schema once from a checkout:

   ```bash
   DATABASE_URL=postgresql://... python -m src.pipeline init-db
   ```

   (The first workflow run would also do it.) Confirm with
   `python -m src.pipeline check-config`, which should show
   `Database  PostgreSQL`.

If you created a database earlier by pasting `migrations/0001_initial.sql`,
`init-db` adopts it in place. Do not paste the current SQL file into a
database that already has tables.

### Migrations (Alembic)

Alembic owns the schema. Its environment is inside the package
(`src/db/alembic/`, so the Docker image can migrate itself); `alembic.ini` at
the repository root is for the command line and takes the database from
`DATABASE_URL` (or the local SQLite file).

```bash
python -m src.pipeline init-db   # upgrade to the latest revision; also adopts a pre-Alembic database
alembic upgrade head             # the same upgrade, from the Alembic CLI
alembic current                  # which revision the database is at
alembic downgrade -1             # step back one revision
```

Revisions today: `0001_baseline` (every table as it stood before Alembic),
`0002_brands` (`brands`, `observations`, `stage_changes`, and `brand_id` on
`opportunities` and `score_events`), `0003_outcomes` (backtest labels).
Downgrading a revision removes only what it added.

`init-db` never drops data. It handles an up-to-date database (one query), a
brand-new SQLite file (creates the tables and stamps the latest revision), a
pre-Alembic database (brought up to the baseline additively, stamped at the
newest revision whose tables it already has, then upgraded), and an empty
PostgreSQL database (replays every revision).

**After upgrading a database that already held leads** to `0002_brands` or
later, run `python -m src.pipeline backfill-brands` once. Leads stored before
the brand tables existed have no brand, so the rescan, the public feed and the
backtest cannot see them; the command links them (journal by journal, oldest
first) and records the observations their stored rows support. It never
changes a score, and a second run does nothing.

**Adding a revision** (a schema change):

1. Change the models in `src/db/tables.py`.
2. Generate the revision, using the explicit-id convention the existing ones
   use:

   ```bash
   alembic revision --autogenerate -m "describe the change" --rev-id 0004_short_name
   ```

3. Read the generated file in `src/db/alembic/versions/`, fix anything
   autogenerate got wrong, and make sure `down_revision` is the previous head
   and `downgrade()` removes only what `upgrade()` added.
4. Regenerate the paste-into-Supabase SQL, which CI checks against the models:

   ```bash
   python scripts/generate_migration.py > migrations/0001_initial.sql
   ```

5. Run `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head`
   on a scratch database, then the test suite. CI repeats the round trip on a
   real PostgreSQL 16.

### Without `DATABASE_URL`: how the workflow keeps the SQLite file

A GitHub runner starts empty, so the weekly workflow carries the SQLite file
from one run to the next:

* **Restore** from the Actions cache before the run (key `lt-db-<run id>`,
  falling back to the most recent `lt-db-*` entry).
* After all steps, **checkpoint** the write-ahead log into the file and
  **save** it to the cache under this run's key.
* **Upload** the file as the artefact `launchtrace-db-<run number>`, kept 14
  days.

The limits are real:

* GitHub evicts a cache entry not used for **7 days**, and evicts old entries
  once the repository passes **10 GB** of cache. When the cache misses, the
  workflow restores the newest unexpired `launchtrace-db-*` artefact (step
  "Recover the database if the cache missed"). If there is none (artefacts
  last 14 days) it **stops** with an error and a note in the job summary,
  rather than start from an empty database (journals unprocessed, every
  applicant new, the search budget reset). Only the workflow's first ever run,
  or a manual run with the `fresh_database` input ticked, starts empty, and
  the job summary then says "History lost" (DECISIONS.md D-702).
* The website (Fly.io / Render) cannot see the runner's SQLite file, so the
  `/feed` routes and the operator view need a shared database anyway.
* The backfill workflow does not carry the SQLite file at all.

Setting `DATABASE_URL` removes all of this.

---

## 2. The Friday workflow

`.github/workflows/weekly-pipeline.yml` runs at 13:00, 16:00 and 19:00 UTC
every Friday (and by hand from Actions → Weekly pipeline → Run workflow). One
run at a time (`concurrency: weekly-pipeline`). Steps, in order:

| # | Step | Runs when | Can fail the job? |
| --- | --- | --- | --- |
| 1 | Restore (or build, on a month's first run) the Companies House bulk index, cached per month | unless `company_index` is unticked | — |
| 2 | Restore the SQLite database from the cache | only without `DATABASE_URL` | — |
| 3 | `check-config`, then `weekly` | always | **yes** (via the last step) |
| 4 | `rescan` (job, then the movers digest, then cache prune) | always, 20-minute timeout | no |
| 5 | `retention --apply` | always | no |
| 6 | `build-feed --out public/`, upload `public-feed-<n>` | always | no |
| 7 | Checkpoint, save and back up the SQLite database | only without `DATABASE_URL` | — |
| 8 | Upload `email-outbox-<n>` (14 days) and `weekly-report-<n>` (60 days) | always | — |
| 9 | Fail the job if step 3 failed | — | yes |

What each step guarantees when it runs again (the second and third Friday
attempt, or a manual re-run):

* **weekly** — resolves the journal first; if the database already records it
  as processed, prints `Journal … has already been processed; nothing to do`,
  exits 0, and makes no download, search, LLM call or run row. A blocked or
  failed attempt does not mark the journal processed, so the next attempt
  retries it. Opportunities are updated, not duplicated. Journal download
  caches older than `cache_max_age_days` (30, `config/operations.json`) are
  pruned at the end of every invocation, including the early exit. An
  already-processed journal is never re-sent by `weekly --send`; use
  `send --run-id`.
* **rescan** — a brand checked less than `min_days_between_checks` (6) days
  ago is skipped, so the later attempts find nothing due and make no external
  call. A brand is marked checked even if its check failed, so a broken site is
  retried next week, not three times today. The digest is one per customer,
  recipient and ISO week (a `deliveries` row with an idempotency key); no
  movers means no email and no row.
* **retention** — a dry run and an apply count the same rows, and each class
  runs in its own transaction, so a second apply changes nothing.
* **build-feed** — a pure function of the database and the config: building
  twice gives byte-identical files. The public delay is measured against the
  newest journal in the database, not the clock.

### Forcing a re-run

Scheduled runs never force. To process a journal again on purpose:

* GitHub: Actions → Weekly pipeline → Run workflow → enter the journal number
  and tick **force**.
* Locally: `python -m src.pipeline weekly --journal 2026-036 --force`
  (`backfill` takes `--force` too).

A forced run spends search calls again (counted against the monthly budget).
`--no-db` runs never write and are never skipped.

---

## 3. The search cost guard

Every call to the search provider passes through a budget
(`src/enrich/budget.py`) configured in `config/costs.json` → `search_guard`:

| key | default | meaning |
| --- | --- | --- |
| `max_calls_per_run` | 200 | provider calls per run; the `SEARCH_MAX_CALLS_PER_RUN` variable overrides it (blank = unset) |
| `monthly_budget` | 900 | provider calls across all runs started in the last `window_days`, read from `pipeline_runs.counts.search_calls` |
| `window_days` | 30 | the rolling window |

The run's allowance is the smaller of the per-run cap and what is left of the
monthly budget. A candidate takes one call, or two when its company name
differs from its brand name; a candidate is searched completely or not at
all. When the allowance runs out, web enrichment stops for the rest of that
run: remaining candidates are scored as if no search provider were configured
(capped below HIGH), a warning is recorded (`pipeline.search_budget_exhausted`
in the log), and the run completes. It never blocks a run. Separately,
`SEARCH_MAX_CANDIDATES_PER_RUN` (150, `src/settings.py`) caps how many
candidates are searched at all.

Rescan searches (off by default) are capped at `web_search_max_calls` (20) and
never above the weekly guard's allowance, and are recorded as a
`pipeline_runs` row with mode `rescan` so they count against the monthly
budget.

Before moving to a paid search plan, check that 200 per run and 900 per 30
days fit its quota and price.

---

## 4. Publishing the public feed

Nothing is published automatically. Two options (docs/PUBLIC_FEED.md):

* **The website's own routes.** Wherever the FastAPI app runs, `/feed/`,
  `/feed/<journal>.html`, `/feed.xml`, `/feed/rss.xml` and `/feed.json` are
  generated from the database on each request. Nothing to deploy, but the app
  must use the same database as the weekly run (another reason for
  `DATABASE_URL`).
* **GitHub Pages from the static build.**
  1. Set `public_base_url` in `config/public_feed.json` to the Pages address
     (e.g. `https://<owner>.github.io/<repo>/`) and make sure the `SITE_URL`
     repository variable is set (footer links and the call to action are
     built from it).
  2. Settings → Pages → Source: **GitHub Actions**.
  3. Add a deploy job after the feed build: `actions/upload-pages-artifact`
     with `path: public/`, then `actions/deploy-pages`, with `pages: write`
     and `id-token: write` permissions. Or download a `public-feed-<n>`
     artefact and publish it by hand the first few times.

Locally: `python -m src.pipeline build-feed --out public/` (or
`--journal 2026-036` to rebuild one week). Review a built feed by hand, and
settle the privacy questions in HUMAN_ACTIONS.md, before the first
publication.

---

## 5. Running the full historical backtest

The commands, from docs/BACKTEST.md:

```bash
python -m src.pipeline build-company-index --download      # once: the free Companies House index
python -m src.pipeline backtest probe                      # optional: which journals the IPO still serves
python -m src.pipeline backtest ingest --source ukipo_http --from 2026-010 --to 2026-041 --max-journals 32
python -m src.pipeline backtest run                        # label + report into reports/backtest/
```

32 journals of 200–230 MB each (about 7 GB transferred, ~10 MB each once
cached), with a 15 s pause between journals: about 15–20 minutes. Without
`--max-journals` an invocation stops after 10; run it again to continue. The
ingest never calls web search or the LLM, and the domain layer is off unless
`--with-domain-layer`. A journal already processed (by `weekly` or an earlier
ingest) is skipped unless `--force`. Without the company index every
applicant is unmatched and every lead suppressed.

The IPO serves a rolling archive of roughly 53 weeks (on 2026-10-09 the
earliest was 2025-040). Anything older must be ingested before it disappears.

**Production database or a separate one (DECISIONS D-508).** Ingesting into
the database the weekly run uses is what lets the rescan follow these brands,
so their labels can mature. It also has side effects:

* the weekly run gains applicant history, so `first_trademark_for_applicant`
  fires less often for repeat filers — a live score change caused by data;
* past weeks with a MEDIUM-or-better lead appear in the public feed, which
  publishes every week in the database after the delay;
* `backtest-ingest` runs join the run history the weekly volume checks read.

If any of that is unwanted, point `DATABASE_URL` at a separate database for
the backtest; labels then mature only if the rescan is also run against that
database. This is an owner decision (HUMAN_ACTIONS.md, Phase 3 — backtest).

Re-run `backtest run` monthly; labels appear only once observations inside
each brand's +3/+6-month window exist. Weight suggestions are never applied.

---

## 6. Retention

`config/retention.json` holds one class per kind of data; every period is a
placeholder pending your decision, and the workflow applies them every
Friday. Before relying on that, look first:

```bash
python -m src.pipeline retention            # dry run: rows per class that would change
python -m src.pipeline retention --json     # the same, machine-readable
python -m src.pipeline retention --apply    # make the changes
```

Run the dry run against the real database (`DATABASE_URL` set). To stop a
class being touched, set its `"enabled": false`; to stop everything, set it on
every class. Individual applicants' names are removed from old
`trademark_records`, `opportunities` and `company_matches` rows rather than the
rows being deleted (D-404). Suppression lists, customers and their
preferences, journal metadata, brands, observations and stage changes are never
touched. Details: docs/DATA_RETENTION.md, docs/DATA_MAP.md.

---

## 7. The stability harness

`scripts/stability_snapshot.py` answers one question: did a code change move
any lead? It runs every journal in the repository (the fixture week; 2026-036
and 2026-037 replayed against the recorded web evidence in
`data/web_evidence/`; open-data 2018-01-05 and 2018-01-12) twice each through
the real `src.commands._run_one` path, in a scratch database, with
network-free providers, and records every lead's score, band, suppression and
reason keys.

```bash
python scripts/stability_snapshot.py --out /tmp/after.json
python scripts/stability_snapshot.py --compare reports/stability/baseline.json /tmp/after.json
```

`--compare` prints `IDENTICAL: …` or every change. Against the Phase 0
baseline the expected output is exactly `reports/stability/phase1_diff.txt`
(the Phase 1 bug fix); anything else means a score moved and must be
explained.

`reports/stability/` holds:

| file | what |
| --- | --- |
| `baseline.json` | the Phase 0 snapshot (10 runs, 550 scored leads) |
| `BASELINE.md` | how and when it was recorded, and what the diffs mean |
| `phase1_diff.txt` | baseline vs. after the Phase 1 fix — the one accepted change |
| `phase1_verified.txt` | the check that every re-run now equals its first run |
| `phase2_diff.txt` | baseline vs. after the domain layer — identical to `phase1_diff.txt` |

Run it before merging anything that touches the pipeline or the scoring
config.

---

## 8. Weekly external-call budget

What one Friday asks of outside services, with the default config. The second
and third Friday attempts make **none** of these calls (the journal is already
processed; no brand is due for a rescan).

| Service | Weekly run | Rescan | Limit that enforces it |
| --- | --- | --- | --- |
| UKIPO journal | 1 download (~150–230 MB) | — | one journal per run |
| Tavily (or other search) | typically 1–2 calls per emerging candidate, up to 150 candidates (`SEARCH_MAX_CANDIDATES_PER_RUN`); **≤ 200 per run** | 0 (off; ≤ 20 when enabled) | `search_guard`: 200 per run, **≤ 900 per rolling 30 days** |
| RDAP | ≤ 60 (+1 IANA bootstrap refresh at most weekly; one retry on a timeout or 5xx) | ≤ 60 | `max_domains_per_run` 60; `max_domain_probes_per_run` 60 |
| DNS queries (A, MX, NS) | ≤ 180 | ≤ 180 | 3 per domain |
| HTTP to brand sites (robots.txt + homepage) | ~120–240 (2 per domain, up to 4 when https fails and http is tried) | ~120–240 | each request follows at most 5 redirects |
| Companies House, bulk index | 0 (local file; the ~500 MB snapshot is downloaded once a month) | 0 | — |
| Companies House, API key | one `/search/companies` per uncached applicant name (`config/costs.json` assumes ~250) | ≤ 150 `GET /company/{number}` (up to 3 attempts each on 429/5xx) | `max_brands_per_run` 150; 0.6 s spacing |
| LLM (if configured) | ≤ 400 candidates (`LLM_MAX_CANDIDATES_PER_RUN`) | 0 | — |

Monthly, at 4–5 Fridays, the search guard keeps Tavily at or below 900 calls,
inside its 1,000-a-month free tier; every other service in the table is free.
The domain numbers apply only to brands whose website was **verified**, which
needs a search provider; without one, the domain and rescan columns are close
to zero apart from the Companies House check. RDAP and homepage requests are
spaced at least 1 s apart per server, and the rescan pauses 1 s after each
brand that used the network.
