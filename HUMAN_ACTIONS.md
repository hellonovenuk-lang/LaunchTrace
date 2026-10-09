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
