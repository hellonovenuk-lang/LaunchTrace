# Backtesting the LaunchTrace Score

The score claims to pick out food brands that are about to launch. A backtest
checks that claim against what later happened: take past journals, score every
lead **using only what could have been known when it was filed**, label whether
each brand had launched 3 and 6 months after filing, and measure precision and
recall by score band and by indicator. Everything lives in `src/backtest/`;
every rule is in `config/backtest.json`.

```bash
python -m src.pipeline backtest probe                         # which past journals can be downloaded?
python -m src.pipeline backtest ingest --from 2026-010 --to 2026-041   # run them through the pipeline
python -m src.pipeline backtest label   [--as-of YYYY-MM-DD]  # outcome labels at +3 / +6 months
python -m src.pipeline backtest report  [--out reports/backtest] [--note TEXT]
python -m src.pipeline backtest run     # label + report on the current database
```

## 1. Which past journals exist — `backtest probe`

`src/backtest/probe.py` asks ipo.gov.uk for `/t-tmj/tm-journals/<YYYY-NNN>/jnl.xml`
(the URL scheme `src/ingest/ukipo_http.py` downloads from) without downloading
anything: one `HEAD` per journal, or a 1 KB ranged `GET` when the HEAD answer is
ambiguous. It walks back from the newest journal to the first one served, then
binary-searches for the earliest one still served, so a six-year range costs
about a dozen requests rather than three hundred. At least 2.5 s between
requests, a 20 s timeout, the honest `UKIPO_USER_AGENT`, a hard cap of 24
journal requests, a stop after 6 consecutive misses walking back, and a stop
after 3 captcha / 429 answers. ipo.gov.uk answers 403 both for a missing file
and when it is blocking a network, so a 403 is read as "missing" only after the
host has served this session a journal; before that, the ranged GET looks for a
captcha page. The result goes to `reports/backtest/availability.json`.

**Finding (2026-10-09, 16 requests in total):** the journal archive is served
from this sandbox, with no captcha. (The 403s recorded in `HANDOFF.md` §3.1 are
explained by the wrong path and file name the code used then — see commit
767c27d. In this session ipo.gov.uk answered 403 for a journal's missing
`index.html`; for a missing `jnl.xml` a HEAD came back as an HTML page and the
ranged GET as 404, which is why the probe never trusts an HTML answer to a
HEAD.) The newest journal, 2026-041, was served; the **earliest served is
2025-040** (published 2025-10-03); 2025-039 and 2025-038 answer 404. So the IPO
keeps roughly the **last 53 weeks**, a rolling window: each week the oldest
journal disappears. The binary search assumes the archive has no holes inside
that window. The IPO Open Data snapshot (the `open_data` source) is the other
historical route: the file is 63 MB, last modified 13 February 2018, so it
covers filings published up to early 2018 only, without goods and services
text; its four weeks in `data/journals/` are January 2018.

## 2. Running past journals — `backtest ingest`

`src/backtest/ingest.py` processes each journal in the range through
`src.commands._run_one`, exactly as `weekly` does — `journals`,
`trademark_records`, `opportunities`, `brands` and `observations` land in the
same database — with three things off:

| | weekly | backtest ingest |
| --- | --- | --- |
| web search | if configured | **never** (`search_provider none`; a paid API) |
| LLM classifier | if configured | **never** (paid) |
| domain layer (RDAP/DNS/homepage) | on | **off** by default (`--with-domain-layer` turns it on): it describes the present, not the journal's week |
| Companies House | API or bulk index | the same (free) |
| run outputs | `reports/runs/<journal>/` | `CACHE_DIR/backtest_runs/<journal>/` |

A journal already processed for the same source is skipped unless `--force`
(the local and live UKIPO sources share a source name, so a journal processed
either way counts). At most `max_journals_per_invocation` (10) journals per
command — `--max-journals` overrides — and a 15 s pause between journals
downloaded from ipo.gov.uk. Journals beyond the cap are listed as
`deferred_by_cap`: run the same command again.

### The full backtest command

```bash
python -m src.pipeline build-company-index --download      # once: the free Companies House index
python -m src.pipeline backtest ingest --source ukipo_http --from 2026-010 --to 2026-041 --max-journals 32
python -m src.pipeline backtest run
```

32 journals. Each is a 200–230 MB download (2025-040 was 233 MB, 2025-041
200 MB), about 3,400–3,800 records, stripped to ~10 MB in the cache: about
7 GB transferred, ~320 MB of cache (pruned after 30 days by `weekly`), and
roughly 30 s a journal including the pause — **about 15–20 minutes**. Why this
range: labels need observations made *inside* each brand's horizon window
(§4), and only brands whose window is still ahead can get them — a filing in a
journal from about 2026-020 onwards still has its +6-month end in the future,
and from about 2026-034 its +3-month end. Older journals (2025-040 …
2026-009, while the IPO still serves them) add population and applicant
history ("first trade mark" is judged against stored journals) but can only be
labelled from evidence nobody recorded at the time, so they will stay
`unknown`. Where to run it is a decision — see HUMAN_ACTIONS.md, Phase 3 —
backtest.

## 3. Point-in-time policy — `src/backtest/pit.py`

A backtest score may not use the future. For a filing on date F the **cutoff**
is `F + pit_window_days` (0: the filing date itself). The score is built from:

* the **stored trade mark record** as published (mark, classes, goods text,
  applicant) — the subject of the prediction;
* applicant history from **stored journals before this one** (first trade
  mark) and this journal (marks filed the same week);
* observations that are `point_in_time_safe`, have `source_date <= cutoff`
  (`brands.observations_as_of`), and are in `allowed_signals`;

and nothing else. It goes through the real `Pipeline._build_opportunity` and
`LaunchTraceScorer` — the scoring code is not copied. With nothing to drop (no
Companies House match, no search, no domain layer) the PIT score equals the
stored live score exactly; the first real report shows 152 of 152, and a test
proves it on the fixture journal.

| signal | source | PIT-safe | used by a backtest | why |
| --- | --- | --- | --- | --- |
| trade mark record (mark, classes, goods, applicant) | trademark | yes | **yes** | what was published; the subject of the prediction |
| `filing_date`, `publication_date`, `nice_classes` | trademark | yes | yes (dated) | fixed historical facts |
| first trade mark / marks this week | trademark records | yes | **yes** | from stored journals before / of this week |
| `incorporation_date` | companies_house | yes | **yes**, if ≤ cutoff | a dated historical fact |
| `company_match` (number, confidence) | companies_house | no | identity link only (D-503) | needed to attach an incorporation date; status/SIC/accounts never |
| `company_status`, `sic_codes`, `accounts_category` | companies_house | no | **no** — emptied | the register as it is today |
| `website`, `retail_presence`, `marketplace_presence`, … | web_search | no | **no** — scored as "search not run" | today's web; also a paid API |
| `domain_created` | rdap | yes | yes, if ≤ cutoff | RDAP registration date (can only look younger, D-205) |
| `dns_has_*`, `homepage_status`, `site_platform`, `holding_page`, `web_presence_stage`, `domain_expires`, `domain_registrar` | dns / homepage / rdap | no | **no** | the domain as it is today |
| `launchtrace_score`, `launch_stage` | score | no | **no** | our own derivation |

Consequences: web inputs are neutralised exactly as the live pipeline treats a
run without a search provider (evidence scaling, and the score capped at 79),
so a PIT score is never HIGH. These indicators can never fire point-in-time
and are reported as "cannot assess": `early_stage_website`,
`no_major_retail_listings`, `active_launch_signals`, `mature_brand_footprint`,
`widely_distributed`, `already_on_marketplace`, `food_sic_code_match`,
`service_business_sic_only`, `small_company_accounts`, `company_dormant`,
`shop_platform_detected`, `holding_page_detected`, `has_mx_records`. Of the
domain indicators only `domain_registered_recently` can be validated.

Raising `pit_window_days` to the usual filing-to-publication lag (6–8 weeks)
asks "what could we have known when the journal came out?" instead of "…on
the filing date?", and admits e.g. a company incorporated just after filing.

## 4. Outcome labels — `src/backtest/labeller.py`, table `outcomes`

For each brand and horizon (`horizons_months`: 3, 6) from its first filing
date:

* `unknown` if the horizon has not ended on the as-of date;
* `launched` if any available criterion has positive evidence **observed on or
  before** the horizon end;
* `not_launched` only if nothing is positive **and** a criterion has a negative
  check observed between the horizon end and 30 days after it
  (`negative_check_tolerance_days`);
* otherwise `unknown` ("no evidence"). Absence of evidence is never
  `not_launched`.

| criterion | positive | negative | status |
| --- | --- | --- | --- |
| `shop_live` | `site_platform.shop_platform = true`, or `web_presence_stage = live_store` | `web_presence_stage` in no_dns / parked / holding_page / site_no_store | available (needs domain-layer or rescan observations) |
| `ch_accounts_after_filing` | — | — | **unavailable**: the Companies House data held has the accounts category only, no made-up-to date (D-506) |
| `retail_or_marketplace_listing` | `marketplace_presence` / `major_retailer_presence` true, `retail_presence` marketplace / independent / multiple retail | `retail_presence = none_found` | available (from searches already stored) |

The labeller reads stored observations only: it never searches, fetches or
looks anything up. Each row stores `criteria_met`, the observations used as
`evidence`, `as_of_date` and `labeller_version`; relabelling replaces the row
for that version. Labels **mature over time**: the weekly run and the rescan
job add observations, and a brand gets a label once an observation falls inside
its window. Run `backtest run` again monthly.

## 5. The report — `src/backtest/report.py`

`reports/backtest/<YYYY-MM-DD>.md` and `.json`. One row per brand: the
opportunity from its first journal (the highest PIT score if it filed several
marks that week), scored point-in-time, joined to its labels. For each horizon,
`unknown` labels are excluded and counted:

* **by PIT score band** (HIGH/MEDIUM/SUPPRESS from `config/scoring.json`):
  brands, launched, precision (launched rate) with a 95% Wilson interval,
  recall (share of all launches), and cumulative "score at or above this band"
  precision and recall;
* **by indicator**: how often it fired, support among labelled brands,
  launched rate when fired and when not (Wilson intervals), precision, recall
  and lift over the base rate;
* **weight suggestions**: "hold" unless at least 10 labelled brands fired and
  10 did not *and* the two 95% intervals do not overlap; then a direction and
  `round(8 × ln(lift))` points, capped at ±8. Never applied — a human edits
  `config/scoring.json` and reviews the stability snapshot.

A report with fewer than 30 labelled brands at any horizon carries a
**SMALL SAMPLE** banner; one with no labels at all says it measures nothing.

## 6. Caveats

* **Labels need future observations.** A brand from a past journal has no
  observation from inside its horizon window, so it stays `unknown`. Only the
  rescan job, run while windows are open, produces labels; the useful backtest
  grows week by week from now on.
* **"Launched" includes already-launched.** There is no point-in-time record of
  a website before filing, so a brand already trading at filing is labelled
  `launched` like a new launch, although the score is designed to rank it low.
* **Survivorship.** Only filings that became opportunities have brands; records
  rejected earlier (wrong goods, portfolio filers, dissolved companies) are not
  in the population, so recall is recall *among leads*.
* **PIT scores are flatter than live scores**: no web evidence, so never HIGH,
  and many indicators cannot be assessed.
* **History depth.** "First trade mark for this applicant" only knows the
  journals in the database; the oldest ingested journal sees every applicant as
  new. Ingest a few extra weeks before the range you analyse.
* **Today's configuration.** The major-brand-owner list, the taxonomy and the
  scoring weights are today's; the backtest asks how *today's* model would have
  ranked past filings.
* **Rolling archive.** ipo.gov.uk serves about a year of journals; anything
  older must come from Open Data (to early 2018 only) or a copy you keep.
