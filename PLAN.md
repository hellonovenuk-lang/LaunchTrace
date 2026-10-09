# Structural upgrade plan

Integration branch: `structural-upgrade` (local), pushed to
`claude/launchtrace-structural-upgrade-8umgor` (the session's designated remote
branch — see DECISIONS.md D-001). Nothing is merged into or pushed to `main`.

## Baseline (Phase 0, recorded 2026-10-09, Python 3.11.17)

| Check | Result | Time |
| --- | --- | --- |
| `ruff check .` | clean | <1s |
| `ruff format --check .` | clean | <1s |
| `mypy src` | clean | ~19s |
| `pytest` | **647 passed** | ~17s |
| `python -m src.pipeline smoke-test` | PASS (8 raw, 5 opportunities, 4 HIGH, 1 MEDIUM) | ~0.6s |
| `scripts/stability_snapshot.py` | `reports/stability/baseline.json`: 10 runs, 550 scored leads | ~8s |

The stability harness runs five journals (fixture 2025-050; local 2026-036 and
2026-037 replayed against recorded web evidence; open-data 2018-01-05 and
2018-01-12) through the real `src.commands._run_one` path, **twice each** in
one database. The baseline already shows the `known_applicant_names` bug: every
re-run loses `first_trademark_for_applicant` (e.g. BRINEHOUSE 97 → 89; fixture
week HIGH 4 → 3). After the Phase 1 fix the expected diff is: `re_run` entries
become identical to `first_run`; `first_run` entries do not change.

Every merge must pass: `ruff check .`, `ruff format --check .`, `mypy src`,
`pytest`, `python -m src.pipeline smoke-test`, and
`python scripts/stability_snapshot.py --out /tmp/x.json && python scripts/stability_snapshot.py --compare reports/stability/baseline.json /tmp/x.json`
showing only the documented bug-fix change.

## Phases

| Phase | Agents | Branch(es) | Depends on |
| --- | --- | --- | --- |
| 0 | orchestrator | `structural-upgrade` | — |
| 1 | foundation | `phase1-foundation` (worktree) | 0 |
| 2 | domain-layer, public-feed, compliance (parallel) | `phase2-domain`, `phase2-feed`, `phase2-compliance` | 1 |
| 3 | backtest, rescan (parallel) | `phase3-backtest`, `phase3-rescan` | 2 |
| 4 | orchestrator + fresh reviewer | `structural-upgrade` | 3 |

Each agent works in its own git worktree, creates its own virtualenv there
(`python3.11 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt`)
so `import src` resolves to the worktree and not to another checkout, commits
on its branch, and does not push. The orchestrator merges.

## Rules every agent follows

The nine hard rules from the brief, restated as checks:

1. Own worktree/branch; never touch `main`; never force-push; do not push at all.
2. Suite green at hand-back: ruff check, ruff format --check, mypy src, pytest, smoke-test.
3. Every feature has tests; tests are network-free (an autouse guard from Phase 1 blocks outbound sockets).
4. No real API calls with keys, no secrets, no paid services. New external access: free, rate-limited, time-outed, fail-soft.
5. New signals enter `config/scoring.json` with weight 0 (or a feature flag). The stability snapshot must be unchanged by your work.
6. Business rules in `config/*.json`.
7. `DATABASE_URL` → Postgres; absent → persistent SQLite file `data/local/launchtrace.sqlite`. Schema changes are Alembic migrations.
8. Anything needing a human → append to `HUMAN_ACTIONS.md`.
9. Ambiguity → most conservative option, appended to `DECISIONS.md` (next free `D-` number in your own range, see below), continue.

Decision-number ranges, to avoid merge collisions: orchestrator D-001–D-099,
foundation D-100–D-199, domain-layer D-200–D-299, public-feed D-300–D-399,
compliance D-400–D-499, backtest D-500–D-599, rescan D-600–D-699,
reviewer fixes D-700+.

## Phase 1 — foundation: the contract later phases build on

### Persistence and migrations
- Alembic, with the environment inside the package (`src/db/alembic/`) so the
  Docker image (which copies only `src/`, `config/`, …) can migrate itself.
  A root `alembic.ini` is provided for CLI convenience.
- Revision `0001_baseline` = every existing table exactly as in `src/db/tables.py` today.
  Revision `0002_brands` = `brands`, `observations`, `stage_changes`, plus
  `brand_id` on `opportunities` and `score_events`.
- `init_db()` runs `alembic upgrade head`. A pre-Alembic database (tables
  exist, no `alembic_version`) is brought up to the baseline additively, stamped
  `0001_baseline`, then upgraded. Never drops data.
- `migrations/0001_initial.sql` stays generated from the models (CI diff check
  kept) as the "paste into Supabase" convenience; Alembic is canonical.
- Later phases add revisions `0003_*` onward. Parallel agents each chain off the
  current head; the orchestrator re-chains `down_revision` linearly at merge.

### Tables (names and columns are the interface — do not rename later)

`brands` — one row per real-world brand/company across weeks.
| column | type | notes |
| --- | --- | --- |
| id | int pk | internal stable id |
| brand_uid | str(32) unique | public stable id, deterministic from the first dedupe identity (`b_` + 16 hex) |
| brand_key | str(128) unique | dedupe identity, see below |
| brand_name | str(512) | primary mark text |
| company_number | str(16) null, index | confirmed CH match only |
| company_name | str(512) null | |
| applicant_type | str(32) | `corporate` / `natural_person` / `unknown` (from Opportunity) |
| applicant_key_hash | str(64) null, index | sha256 of normalised applicant name — never the name itself |
| product_category | str(64) null | |
| region | str(128) null | region only, never finer |
| website | str(512) null | verified website, if any |
| first_seen_journal | str(32) | |
| first_seen_at | datetime | |
| first_filing_date | date null | |
| last_seen_journal | str(32) | |
| current_stage | str(32) | starts as the Opportunity `launch_stage` value |
| current_score | int | latest LaunchTrace score |
| current_band | str(16) | |
| last_checked_at | datetime null | last enrichment/rescan touching this brand |
| launched_at | datetime null | set by rescan/outcomes when a "launched" transition is detected |
| created_at / updated_at | datetime | |

Dedupe rules (documented in `docs/ARCHITECTURE.md`): look up in order
(1) `company_number` of a confident CH match → key `ch:<company_number>`;
(2) else key `tm:<sha256(normalised mark text | normalised applicant name)[:24]>`.
If a brand first seen as `tm:` later gains a company number, the row keeps its
id/uid/key and gets `company_number` filled in; a later lookup by that company
number finds it. Multiple marks of one company → one brand (matches the
existing consolidation rule).

`observations` — append-only facts. No update/delete API outside retention.
| column | type | notes |
| --- | --- | --- |
| id | int pk | |
| brand_id | fk brands.id (cascade) index | |
| source | str(32) | `trademark`, `companies_house`, `web_search`, `rdap`, `dns`, `homepage`, `rescan`, `outcome` |
| signal | str(64) index | name registered in `config/signals.json` |
| value | JSON | scalar or small object |
| observed_at | datetime index | when we looked |
| source_date | date null | when the fact was true in the world (filing date, incorporation date, RDAP registration date) |
| point_in_time_safe | bool | true only if `source_date` is trustworthy for backtests |
| run_id | str(64) null | |
| journal_number | str(32) null | |

`stage_changes` — `id`, `brand_id` fk, `from_stage`, `to_stage`,
`detected_at`, `evidence` (JSON), `run_id` null.

`opportunities.brand_id`, `score_events.brand_id` — nullable fk to `brands.id`.

### Python API (`src/brands.py`)
```python
brand_key_for(opp: Opportunity) -> str
upsert_brand_from_opportunity(session, opp, run_id: str) -> Brand
sync_brands(session, result: PipelineResult) -> int   # called by _run_one after save_opportunities
record_observation(session, brand_id, source, signal, value, *, observed_at=None,
                   source_date=None, point_in_time_safe=None, run_id=None,
                   journal_number=None) -> Observation   # PIT default comes from config/signals.json
latest_observations(session, brand_id, as_of: datetime | None = None) -> dict[str, Observation]
observations_as_of(session, brand_id, cutoff: date, pit_safe_only: bool = True) -> list[Observation]
record_stage_change(session, brand_id, from_stage, to_stage, evidence, run_id=None) -> StageChange
```
`sync_brands` writes observations for the facts already in hand:
trademark (`filing_date` PIT-safe), Companies House (`incorporation_date`
PIT-safe; `company_status`, `sic_codes`, `accounts_category` not PIT-safe),
web search (`website`, `retail_presence`, `marketplace_presence`… not PIT-safe),
and score (`launchtrace_score`, not PIT-safe — it is our own derivation).

`config/signals.json` — registry: `{ "<signal>": {"source": ..., "point_in_time_safe": bool, "description": ...} }`
grouped with top-level keys per source so parallel agents append to different
sections.

### Idempotency and cost
- `weekly` resolves the journal ref, and if `journal_already_processed` → logs
  `weekly.already_processed`, prints a line, exits 0 **before** any download or
  search. `--force` re-processes.
- Search calls are counted through a budget wrapper around the search provider;
  `FunnelCounts.search_calls` records them; `pipeline_runs.counts` persists them.
- Cost guard in `config/costs.json` → `search_guard`: `max_calls_per_run`
  (default 200) and `monthly_budget` (default 900, computed from persisted runs
  in the last 30 days). On exceeding either, web enrichment stops for the rest
  of the run (remaining records `attempted=False`, warning recorded) — never a
  blocked run. Env `SEARCH_MAX_CALLS_PER_RUN` overrides the per-run cap.
- `FileCache.prune` runs at the end of every `weekly` (and `rescan`), max age
  from `config/operations.json` → `cache_max_age_days` (default 30).

### Workflow
- `DATABASE_URL` secret used when present. Without it, the SQLite file is
  restored from `actions/cache` (`key: lt-db-${{ github.run_id }}`,
  `restore-keys: lt-db-`) and saved after the run, and also uploaded as a
  short-retention artifact as a backup.
- Remove nothing that exists (company index cache, artifacts).

## Phase 2 briefs (summary; full briefs are given to each agent)

**domain-layer** — `src/enrich/domain/` (`rdap.py`, `dns_check.py`,
`homepage.py`, `platforms.py`, `layer.py`). `DomainSignals` model attached to
`Opportunity.domain`; pipeline stage after web enrichment via an injectable
`DomainProber` (null prober when `DOMAIN_LAYER_ENABLED=false`, fixture prober in
tests). Rules in `config/domain_layer.json`. Signals registered in
`config/signals.json` under `domain`. `sync_brands` extension writes them as
observations. Scoring indicators added with weight 0. CSV columns appended.

**public-feed** — `src/feed/` (`build.py`, `render.py`, templates), CLI
`build-feed --out public/`, FastAPI routes under `/feed`. Config
`config/public_feed.json` (top N, delay weeks, min band). Company-level only.
CI artifact step.

**compliance** — template fix, cross-output "no individual names" test,
`retention` command + `config/retention.json`, placeholder inventory into
`HUMAN_ACTIONS.md`, `docs/DATA_MAP.md`.

## Phase 3 briefs (summary)

**backtest** — `src/backtest/` (`probe.py`, `ingest.py`, `pit.py`, `labeller.py`,
`report.py`), `outcomes` table (migration), config `config/backtest.json`,
reports in `reports/backtest/`.

**rescan** — `src/rescan/` (`job.py`, `changes.py`, `digest.py`), config
`config/rescan.json` (window, check interval, transitions), `rescan` CLI,
workflow step after weekly.

## Phase 4
Integration run, cost estimate, docs (README, HANDOFF, docs/OPERATIONS.md),
fresh independent reviewer, fixes, final report.
