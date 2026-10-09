# Rescan: following brands after the week they were filed

A trade mark is filed before the brand trades. The weekly run sees a brand
once, in the week its mark is published; weeks later the holding page becomes a
shop, or the dormant company files trading accounts. That later moment is often
when a brand starts buying packaging. The rescan (enrichment layer 5,
`src/rescan/`) looks again at recently seen brands each Friday, records what
moved, and tells customers in a short weekly digest.

The rescan never runs inside a pipeline run, writes no score and changes no
existing column's meaning, so the stability snapshot is unaffected.

## What it does

```
python -m src.pipeline rescan                 # job, then digest, then cache prune
python -m src.pipeline rescan --dry-run       # list what would be checked; no calls, no writes
python -m src.pipeline rescan --limit 20      # check at most 20 brands
python -m src.pipeline rescan --no-digest     # job only
python -m src.pipeline rescan --json          # summary as JSON
python -m src.pipeline movers-digest          # digest only
```

1. **Select** (`src/rescan/job.py`, `select_brands`). A brand is due when:
   * it was first seen within `window_weeks` (26). The date is the publication
     date of its first journal, else its earliest filing date, else when the
     row was created;
   * `launched_at` is not set;
   * `last_checked_at` is empty or older than `min_days_between_checks` (6).
     The weekly web search also sets it, so a brand searched this week is not
     probed again the same day;
   * it has something to check: a verified website (domain check), a confirmed
     company number (Companies House check), or no website at all while web
     search is on.

   Brands with the oldest check go first, then the newest brands, up to
   `max_brands_per_run` (150). Brands whose leads were suppressed are included:
   a rescan exists to notice brands that were too early the first time.
2. **Check**, each switchable in `config/rescan.json` → `sources`:
   * `domain` (on): the domain layer (`src/enrich/domain`) on the registrable
     domain of `brands.website`, the entity-verified website. It is the live
     prober in production (`DOMAIN_LAYER_ENABLED`, on by default in the
     workflow) and the null prober when the layer is off. At most
     `max_domain_probes_per_run` (60) distinct domains.
   * `companies_house` (on): `lookup_by_number` on the configured registry. With
     the free bulk index this is a local SQLite read (no network, but the data
     is the monthly snapshot); with `COMPANIES_HOUSE_API_KEY` it is one
     `GET /company/{number}`, spaced at least 0.6 s apart.
   * `web_search` (off): for a brand with no website, one search through the
     normal web enricher and entity verifier, so a website is only recorded if
     it is verified as the company's.
3. **Record observations** under the same signals and sources `sync_brands`
   uses, so `latest_observations` compares like with like:
   `web_presence_stage`, `holding_page`, `site_platform`, `dns_has_*`,
   `domain_*` (sources `homepage` / `dns` / `rdap`); `company_status`,
   `sic_codes`, `accounts_category` (source `companies_house`); `website`
   etc. (source `web_search`). One new signal, `company_stage` (source
   `rescan`, registered under `rescan` in `config/signals.json`), holds the
   company stage derived at the rescan. `run_id` is `rescan_<timestamp>_<hex>`.
4. **Detect changes** (`src/rescan/changes.py`) and write `stage_changes`.
5. Set `brands.last_checked_at`, so the second and third Friday attempts find
   nothing due and make no external call.

Every brand runs in its own savepoint. A probe or lookup that raises is counted
in `errors` and the brand's other checks still run; an unexpected error rolls
back that brand's writes only. A brand is marked checked even when its check
failed, so a broken site is retried next week, not on every attempt.
`max_run_seconds` (900) stops the run starting new brands; the rest are first
in line next week.

## Transitions

`config/rescan.json` → `transitions` defines two dimensions, each an ordered
ladder (lowest first):

| dimension | prefix | read from | ladder |
| --- | --- | --- | --- |
| `web_presence` | `web` | `web_presence_stage` observations | `no_domain` < `no_dns` < `parked` < `holding_page` < `site_no_store` < `live_store` |
| `company_status` | `company` | `company_stage` observations (rescan only) | `dissolved` < `in_insolvency` < `dormant` < `no_accounts` < `trading` |

* The previous value is read **before** the rescan writes anything. For the web
  dimension, a `website` observation of null (searched, no site) that is newer
  than the last `web_presence_stage` reads as `no_domain`, which the domain
  layer itself never records.
* `company_stage` comes from the status and latest accounts category
  (`config/rescan.json` → `company_stage`): dissolved / closed → `dissolved`;
  liquidation, administration, receivership → `in_insolvency`; a live company
  with dormant accounts → `dormant`, with none filed → `no_accounts`, with any
  other accounts → `trading`. Anything else is `unknown`. Only the rescan's own
  readings are compared, because the weekly match can come from a provider
  that does not report accounts, which would invent a transition.
* A change is written as `stage_changes(from_stage="web:holding_page",
  to_stage="web:live_store")`. The prefix keeps rescan transitions apart from
  the weekly run's launch-stage transitions in the same table. `evidence`
  holds `detected_by: "rescan"`, the dimension and signal, old and new
  values, their sources and observation times, small details (domain,
  platform, accounts category), and the `meaningful`, `negative` and
  `launched` flags.
* Nothing is written when nothing changed, when either side is not a ladder
  value (`unknown`, a failed check), or on the first reading (a baseline). If
  the brand's most recent change in that dimension is already this exact
  transition, it is not written again.
* **Meaningful** (alert-worthy): moving *up* to `holding_page`,
  `site_no_store` or `live_store`; a company moving up to `trading`; a company
  moving *down* to `dissolved` or `in_insolvency` (negative). Upward moves
  must reach a new high: a site that drops off DNS and comes back is recorded
  both ways but alerts nobody.
* **Launched**: reaching `live_store` sets `brands.launched_at` once. The brand
  then leaves the rescan. `brands.current_stage` is left as the weekly
  pipeline's launch stage (DECISIONS D-602).

## The digest

`src/rescan/digest.py`, template `src/deliver/templates/movers_digest.html.j2`
plus a text part. After the job, each recipient of each eligible customer gets
one email listing the brands that made a meaningful move since that
recipient's previous digest (the first one looks back `lookback_days`, 7).

* **Who** — the weekly feed's rules: `active_customers` (subscription active,
  trialing, or past due within the 14-day grace; `delivery_enabled`). An
  address on the email suppression list (`suppress --type email`, the
  `/unsubscribe` form) gets nothing.
* **Which brands** — company level only: a confirmed Companies House number and
  name and an applicant typed `corporate`. Never a brand matching a company or
  mark suppression rule or an opted-out company (the public feed's
  `Suppressions`). Filtered by the recipient's minimum band, product
  categories and regions, using the brand's current band, category and region.
* **Privacy** — the party shown is `src.privacy.display_party` (the registered
  company name). Brands never store an applicant's name. Both the full send
  path and the bare renderer are in `tests/test_no_individual_names.py`.
* **Content** — "Moved forward" (with a *Launched* badge for a new shop) and,
  if `include_negative`, "Stopped trading". Each card: brand, company,
  category, region, what moved and when, the Companies House link and the
  verified website. Footer: what the signal is not, sources, manage
  subscription, unsubscribe and privacy links. At most
  `max_brands_per_email` (25) cards.
* **Sending** — `SEND_MODE=review` renders to `reports/outbox/` and never
  sends. With `SEND_MODE=automatic` it still only renders until
  `digest.send_enabled` is `true` (default `false`, pending a review of the
  copy). When sending is allowed, the normal `EmailSender` is used, which
  itself falls back to the outbox when there is no `RESEND_API_KEY`.
* **Idempotency** — one `deliveries` row per customer, recipient and ISO week
  (`kind="movers_digest"`, key `movers_digest:<customer>:<YYYY-Www>:<sha256(email)[:16]>`).
  No movers means no email and no row (`send_when_empty: false`).

## Configuration (`config/rescan.json`)

| key | default | meaning |
| --- | --- | --- |
| `window_weeks` | 26 | how far back a brand's first journal may be |
| `min_days_between_checks` | 6 | makes the three Friday attempts idempotent |
| `max_brands_per_run` | 150 | brands checked per run |
| `max_domain_probes_per_run` | 60 | distinct domains probed per run |
| `max_run_seconds` | 900 | stop starting brands after this |
| `sources.domain` / `.companies_house` / `.web_search` | true / true / false | which checks run |
| `web_search_max_calls` | 20 | hard cap; also never above the weekly search guard's allowance |
| `politeness.seconds_between_brands` | 1.0 | pause after a brand that used the network |
| `politeness.companies_house_api_min_interval_seconds` | 0.6 | CH API spacing (limit is 2/s) |
| `transitions` | see above | ladders, meaningful rules, launched stages, labels |
| `company_stage` | see above | status / accounts → company stage |
| `digest.*` | enabled, 7 days, send off, no empty mails, 25 cards, negatives shown | the digest |

## External calls per weekly rescan

Per domain probe (from `config/domain_layer.json`): 1 RDAP request (plus at
most one IANA bootstrap refresh a week, cached 7 days), 3 DNS queries (A, MX,
NS), robots.txt and 1–2 homepage requests (an http fallback, up to 5
redirects). Per Companies House API lookup: 1 GET (up to 3 attempts on 429 /
5xx).

| | typical | ceiling with default caps |
| --- | --- | --- |
| RDAP | ≤ 60 | 60 + 1 bootstrap |
| DNS queries | ≤ 180 | 180 |
| HTTP (robots + homepage) | ~120–240 (2 per domain; 4 when https fails and http is tried) | not a fixed number: each of those requests may also follow up to 5 redirects |
| Companies House, bulk index | 0 (local file) | 0 |
| Companies House, API key | ≤ 150 GET | 150 (450 with every retry) |
| Search | 0 (off) | 20 when enabled, and never above the weekly guard |
| 2nd and 3rd Friday attempt | 0 | 0 |

In practice the domain numbers are far lower: a domain is probed only for a
brand with a *verified* website, and websites are only verified when a search
provider is configured. Without one, the weekly rescan is essentially the
Companies House check. Run time is bounded by `max_run_seconds` (15 min) and
the workflow step's 20-minute timeout; typically a few minutes (1 s between
brands that used the network).

Coverage: with ~150 brands per run and 26 weeks in the window, a large backlog
of checkable brands is cycled oldest-check-first, so each is re-checked every
few weeks rather than weekly. Raise `max_brands_per_run` (cheap with the bulk
index, which makes no network call) if every brand should be checked weekly.

Search calls made by a rescan are recorded as a `pipeline_runs` row with
`mode="rescan"` (only when there were any), so they count against the rolling
monthly budget exactly like the weekly run's.

## Operations

* **Workflow** — `.github/workflows/weekly-pipeline.yml` runs `rescan` after the
  weekly step and before retention and the feed build, `if: always()`,
  `continue-on-error: true`, with the weekly step's environment. Its output is
  in `rescan.log` in the weekly report artifact; digests written to the outbox
  are uploaded as `email-outbox-<n>` (14 days; it holds recipient addresses).
* **Reading results** — `stage_changes` rows with `to_stage LIKE 'web:%'` or
  `'company:%'` and `evidence->>'detected_by' = 'rescan'`; `brands.launched_at`;
  observations with `run_id LIKE 'rescan_%'`.
* **Switching off** — `sources.*: false` per check, `digest.enabled: false`
  for the email, or remove the workflow step.
* **Retention** — the rescan writes only to `brands`, `observations`,
  `stage_changes` (never touched by retention), `deliveries` and, with search
  on, `pipeline_runs` (both under their existing retention periods).
