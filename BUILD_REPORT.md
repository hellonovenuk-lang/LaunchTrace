# BUILD REPORT

An honest account of what was built, what was verified and how, and what was
not.

Built in two sessions: the product pipeline first, then the commercial layer
around it. Every claim below was checked by running it, and anything that could
not be run is marked as such.

**Session 2 (commercial readiness)** is recorded in its own section at the end
of this document, and summarised for the owner in
[`COMMERCIAL_READINESS.md`](COMMERCIAL_READINESS.md).

---

## Overall status

**Complete and working, with one integration unverifiable from the build
environment and one commercially marginal result.**

The full chain runs end to end on real UK government data: official trade mark
records → packaged-food filtering → real Companies House verification →
explainable scoring → supplier buying-intent mapping → CSV, email and QA report.
Around it, the commercial chain also runs end to end: prospect → ICP priority →
prospect-specific preview → drafted email → sample → customer → weekly delivery
→ feedback → metrics, none of it requiring a credential and none of it able to
send anything.

531 tests pass, lint, formatting and type checks are clean, and the container
image was built and served.

The four-week January 2018 historical sanity test produced a real but marginal
number, reported as it came out rather than framed favourably. It is an
engineering and historical check, not the commercial validation. See "The four
January 2018 weeks" below.

## Commits

All on `claude/launchtrace-food-mvp-7h7bc6`, oldest first:

| Commit | What |
| --- | --- |
| `8036832` | Pipeline core: ingest, parse, classify, enrich, score |
| `83869ea` | Billing, website, operator CLI and the four-week January 2018 historical sanity test |
| `bd1ebc5` | Test suite, `.env.example`, lint and type-check clean-up |
| `dfbaf40` | Migrations, GitHub Actions, compliance drafts, prospecting material |
| `eaa9274` | README, container image and deployment configuration |
| *(final)* | Handoff, build report, and the duplicate-parse fix |

About 8,700 lines of Python across 58 modules, plus configuration, docs and
tests.

## Test results

```
390 passed
```

76% line coverage. Coverage is lowest in the thin provider adapters that only
run against a live API (`llm.py`, `http_providers.py`), which is where mocking
would test the mock rather than the code.

What is covered, and why each was worth testing:

| Area | What the tests establish |
| --- | --- |
| XML parsing | Every field extracted; malformed dates become `None`; an invalid Nice class is dropped; a record with no application number is skipped without killing the journal; an empty document raises rather than silently producing nothing |
| Open Data parsing | Parses the real committed journal weeks; absent goods text is reported as absent |
| Food filter | Accepts packaged food; rejects restaurants, retail services, raw agriculture, pet food, major brand owners and non-UK applicants, each with a specific reason; a brand substring does not falsely match ("Marsden" is not "Mars") |
| Company matching | Exact, normalised and token matching; ambiguity *reduces* confidence; an uncertain match returns unmatched rather than a guess; age is measured at filing date so history and live runs agree |
| Scoring | Bands discriminate; caps fire when evidence is thin; missing enrichment does not double-penalise; no reason string ever contains an unfilled placeholder; weights come from configuration |
| Buying intent | Stage modifiers promote and demote correctly and cannot exceed the top band; every product group has a mapping |
| LLM schema | Valid JSON accepted, including inside prose; invalid enum, missing field, out-of-range number, non-JSON and empty responses all rejected; a malformed response falls back to rules and is counted, never silently accepted |
| Pipeline | Funnel counts internally consistent; deduplication; fail-closed on retrieval failure, volume anomaly and unresolvable journal; post-dated company matches discarded |
| Delivery | CSV schema; no internal debug fields leak to customers; email carries attribution and disclaims purchasing intent; review mode blocks; approval unblocks; **delivery is idempotent** |
| Billing | Every Stripe subscription state transition; cancellation stops delivery; a redelivered webhook does not create a second customer |
| Retry | Server errors and rate limits retried with backoff; client errors not retried; no partial file left behind on a failed download |
| Persistence | Reprocessing a journal does not duplicate; score history accumulates; match evidence retained |
| Web | Honeypot, rate limit, disposable domains, IP stored only as a hash, admin token required, webhook signature required |
| Config | Score bands contiguous 0–100; SIC mappings point at real product groups; **every setting appears in `.env.example`**; no credential in any config file; `.env` is not tracked |

## Lint, format and type checks

```
ruff check .           All checks passed!
ruff format --check .  119 files already formatted
mypy src               Success: no issues found in 76 source files
```

All three run in CI on every push, and **the complete workflow has now passed
on a GitHub runner** — see "CI" below.

## Live integrations tested

Genuinely exercised against real external systems during the build:

| Integration | Result |
| --- | --- |
| **IPO Open Data release** | Downloaded the official 63 MB release from `assets.publishing.service.gov.uk`; 1.19 million real records; sliced into four real journal weeks (5,319 records) now committed to `data/journals/` |
| **Companies House bulk data** | Downloaded the real September 2026 snapshot (492 MB) and indexed **5,689,367 companies**. Live matching verified against real applicant names from the 2018 journals |
| **PostgreSQL 16** | The generated migration applied cleanly: 14 tables, 69 indexes. The full pipeline then ran against it and wrote 53 opportunities, 1,456 trade mark records and 53 company matches |
| **Docker** | Image built (369 MB), container started, `/healthz` returned `{"status":"ok"}` and the landing page served correctly |
| **Web search (research)** | Used to identify and verify the 60 real UK supplier companies in `outreach/prospects_seed.csv` |

## Mocked or fixture-tested integrations

| Integration | How | Why not live |
| --- | --- | --- |
| **Live UKIPO journal** | Structural XML fixture in the real ST.66 shape, plus a malformed fixture | `ipo.gov.uk` returns HTTP 403 bot protection to this environment. See "Not verified" |
| **Search API** | Fixture provider driving the full enrichment and maturity logic | No account exists yet |
| **LLM** | Stub providers returning valid, malformed and error responses | No key configured |
| **Resend** | File mode — every email written to `reports/outbox/` as it would have been sent | No account exists yet |
| **Stripe** | Stub checkout plus webhook fixtures covering every state transition | No account exists yet |

## The four January 2018 weeks: what they are, and what they are not

**What they are.** An engineering and historical sanity test: proof that
parsing, company matching, scoring and weekly volume behave correctly across
four consecutive weeks of real UKIPO data, with every dropped record and its
reason recorded.

**What they are not.** They are not the 2026 commercial validation, and
re-running them with web enrichment switched on will not turn them into it.
Current web enrichment applied to a brand published in January 2018 recovers
what that brand became over the following eight years — not what was knowable
about it in the week it was published. That flatters the brands that later
succeeded and says nothing useful about the ones that were genuinely early at
the time, so the resulting number would not measure the product.

**The decisive product test is a current one:** current UKIPO weekly journal +
current goods/services text + current Companies House evidence + current
Tavily/web enrichment + current classification — one live
`python -m src.pipeline weekly`, scored on what is knowable that week.

The historical numbers below are unchanged and reported as they came out.

Four consecutive **real** UKIPO journal weeks (5 to 26 January 2018 — the most
recent complete weeks in the IPO's Open Data release), enriched against the
**real** Companies House September 2026 snapshot.

| Stage | Total across 4 weeks |
| --- | ---: |
| Trade mark applications parsed | 5,319 |
| Food-class candidates | 614 |
| Packaged-food candidates | 237 |
| UK corporate applicants | 159 |
| Matched to Companies House | 165 |
| Emerging-brand candidates | 173 |
| **HIGH** | **0** |
| **MEDIUM** | **22** |
| Suppressed | 151 |

**Average good opportunities per week: 5.5. Band: QUESTIONABLE (4–7/week).**

**The 0 HIGH is structural, not a finding about the signal.** Without a search
provider the pipeline cannot verify whether a brand is already established, so
the score is deliberately capped below the HIGH band. It refuses to present an
unverified record as a strong signal. The way to get the real split is to
connect a search key and run a **current** week — not to re-run these four.

Three further limits, all recorded in the report itself:

- The Open Data release does not publish goods and services text. **The live
  weekly journal XML does**, so a real Friday run has strictly more evidence
  than these four weeks had.
- Classification ran on rules only, which is more conservative than the full
  classifier.
- The threshold is a business choice. The report includes a sensitivity table:
  7.25/week at a minimum score of 55, 8.0/week at 50.

The output is credible on inspection. The top records are real emerging UK food
brands of that period — Bloody Bens, Qima Coffee, Figment Coffee, Seaweed & Co,
The Snaffling Pig — which is the qualitative check that matters most.

**No conclusion is drawn about whether the business is validated**, and none
can be drawn from these four weeks. The evidence is in `reports/validation/`,
including `rejections.csv` with every one of the 5,147 dropped records and the
reason for each. Nothing was cherry-picked. The report files themselves are
unchanged from the run that produced them, and still use the older word
"validation" in their own headings.

## CI

`tests.yml` runs two jobs on every push. Both pass on GitHub, on
`claude/launchrace-pre-credentials-cleanup-fkim4m` at `97d9c97`
([run 34396020152](https://github.com/hellonovenuk-lang/LaunchTrace/actions/runs/34396020152)):

| Job | Step | Result |
| --- | --- | --- |
| `test` | Install dependencies | pass |
| `test` | Lint (`ruff check .`) | pass |
| `test` | Format check (`ruff format --check .`) | pass |
| `test` | Type check (`mypy src`) | pass |
| `test` | Tests (`pytest -q --cov=src`) | pass — 531 tests |
| `test` | Smoke test (whole pipeline on fixture data) | pass |
| `test` | Verify the PostgreSQL migration applies | pass |
| `migration` | Apply the schema to a real PostgreSQL 16 | pass |

Before this session the runner failed before pytest could collect anything, with
`ModuleNotFoundError: No module named 'src'`. The suite passed locally because
`python -m pytest` puts the working directory on `sys.path` and the workflow's
`pytest` does not. The fix is a packaging one, not a working-directory one:

* `pyproject.toml` discovers `src` **and its subpackages**, and declares the
  Jinja templates and static files as package data;
* `requirements-dev.txt` installs the project itself editable (`-e .`), so
  `import src` resolves identically for `pytest`, `mypy`, `python -m
  src.pipeline` and the scripts, on any machine and from any directory;
* `tests/` is a real package, so `from tests.conftest import ...` resolves the
  same way under `pytest` and `python -m pytest`.

## Defects found and fixed during the build

Worth listing, because each was found by the system's own checks rather than by
inspection:

1. **Post-dated company matches.** The QA report flagged companies incorporated
   *after* the trade mark was filed being scored as brand new, which inflated
   scores. Now discarded beyond a configurable window, with the company
   identity cleared so it cannot leak into the CSV.
2. **Records with no brand name.** Figurative-only marks produced unsellable
   rows. Now suppressed.
3. **Score saturation.** Initial weights put every good record at 100, so the
   bands did not discriminate. Recalibrated, with a config test that fails if
   the maximum achievable score drifts out of range.
4. **Double-penalising missing evidence.** A record we never researched scored
   worse than one that failed research. Weight from enrichment that did not run
   is now redistributed, with the band cap communicating the thinner evidence.
5. **Engine cache keyed on `None`.** `get_engine()` and the session factory
   cached on the literal `None` argument, pinning the first engine created and
   silently ignoring a later configuration change. Found by tests sharing a
   database they should not have.
6. **Shared rate limiter.** The sample form's limiter was a module global
   shared by every application instance. Now per-application.
7. **Ref resolution outside the fail-closed handler.** A missing local journal
   file or an unrecognised journal number crashed with a traceback instead of
   recording a blocked run — a scheduled Friday run would have died without
   saying why. Found by running the pipeline inside the container.
8. **Imprecise rejection reasons.** Class-only exclusions ran after the
   food-class check, so a restaurant filing was recorded as "no food class".
   Reordered, because the rejection reasons are the validation evidence.
9. **The journal was parsed twice per run.** The persistence path re-downloaded
   and re-parsed the journal purely to store the source records, doubling the
   cost of every run — on a 150 MB live journal that is the single most
   expensive thing the pipeline does. The run now retains what it parsed.
10. **KP Snacks scoring HIGH.** A major UK snack brand passed the filter because
   its holding company is recent. The major-brand list was expanded from 60 to
   184 entries, and a SIC-based rule now downranks applicants whose registered
   activity is a service business rather than a product brand.

## Security review

| Check | Finding |
| --- | --- |
| Secrets in the repository | None. `.env` is git-ignored; `.env.example` holds names only. A test asserts no config file contains a credential and that `.env` is untracked |
| Webhook verification | Stripe signatures verified before any processing; an unsigned or wrongly signed request is rejected with 400. A missing webhook secret refuses outright rather than trusting the body |
| SQL injection | SQLAlchemy ORM throughout; no string-built SQL. The bulk index builder uses parameterised inserts |
| Input validation | Form input length-clamped and stripped of control characters; email format and disposable domains checked; templates auto-escape |
| Admin protection | `/admin` requires a token compared in constant time, and is disabled entirely when `ADMIN_TOKEN` is unset |
| Abuse protection | Honeypot field, per-IP rate limit, duplicate suppression |
| Personal data | No IP addresses stored — only a salted hash. No officer records, no persons with significant control, **no directors' home addresses** |
| Container | Runs as a non-root user (uid 10001) |
| HTTP headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and a content security policy on every response |
| Dependencies | 15 direct runtime dependencies, all mainstream and pinned. No package with a small maintainer surface |
| Least privilege | Resend key scoped to sending; Companies House key is read-only by design; Stripe secret key is the only broad credential and is needed for checkout |

**Residual risks, stated plainly.** The Stripe secret key is broad by
necessity. The admin token is a single shared secret rather than real
authentication — adequate for a single operator, not for a team. No automated
dependency scanning is configured; adding Dependabot is a two-line change and
worth doing.

## Licensing review

Both primary sources are published under the **Open Government Licence v3.0**,
which permits commercial reuse with acknowledgement. Attribution appears in
every email, every CSV and the website footer.

**Open questions, recorded rather than assumed away** (all in
`docs/ATTRIBUTION.md`):

1. The IPO's OGL notice carries an "except where otherwise stated" carve-out
   that is not itemised. We assume it covers trade mark *images*, and therefore
   redistribute none — version 1 handles word marks only. **This should be
   confirmed with the IPO in writing.**
2. We have not obtained written confirmation that the weekly Journal XML
   carries the same OGL terms as the Open Data release. It almost certainly
   does. It should be confirmed.
3. Whichever search API you connect will have its own terms on commercial use
   of results. We store links and short snippets as evidence, never substantial
   content, which is the conservative reading — but check the specific
   provider's terms.

## Cost per run

| Item | Cost |
| --- | --- |
| UKIPO journal | £0 |
| Companies House | £0 |
| LLM classification (~100 candidates, cheap model) | ~£0.06 |
| Web search (~60 enrichments) | £0 on a free tier |
| Email | £0 on Resend's free tier |
| **Per weekly run** | **under £0.10** |

Fixed monthly: hosting £0–7, database £0, domain ~£1. **Under £10/month before
revenue**, against a £30 target.

The discipline is architectural rather than incidental: expensive stages run
only against records the free filters already accepted, budgets are capped per
run (`LLM_MAX_CANDIDATES_PER_RUN`, `SEARCH_MAX_CANDIDATES_PER_RUN`), and
results are cached.

## Estimated human work remaining

| Task | Time |
| --- | --- |
| Set up locally and read the January 2018 sanity test | 30 minutes |
| Create a Tavily account, rebuild the index, run one current week | 30 minutes |
| Complete the prospect contact addresses | 2 hours |
| Send the first ten outreach emails | 1 hour |
| Supabase, Resend and Stripe accounts | 1 hour |
| Deploy the site and point a domain at it | 1 hour |
| Configure GitHub secrets | 20 minutes |
| Fill in and review the legal drafts | 2 hours, plus solicitor time |
| **Total to first paying customer** | **about 8 hours plus a legal review** |

## What is not verified

Stated plainly rather than buried:

1. **Live UKIPO retrieval has never run against the live endpoint.**
   `ipo.gov.uk` returned HTTP 403 bot protection to every request from this
   environment, including through a real headless browser, while every other
   government host worked. The code is complete and has three fallbacks; it may
   work first time from your machine. See HANDOFF.md §3.1.
2. **No email has been delivered to a real inbox.** Rendering is snapshot-tested
   and file-mode sending is verified; the Resend HTTP call itself has not run.
3. **No real payment has been taken.** The webhook state machine is tested
   against fixtures covering every transition; Stripe's live API has not been
   called.
4. **The score is calibrated, not validated.** The four January 2018 weeks test
   the machinery, not the commercial signal; only a current-data run can do
   that. The weights are reasoned and
   internally consistent, but no supplier has yet said whether a 79 is a better
   lead than a 62. That feedback is what should drive the next tuning pass, and
   `score_events` already records the history to support it.
5. **Two of the four workflows have still not run.** `weekly-pipeline.yml`,
   `backfill.yml` and `deploy.yml` are syntactically valid and their steps were
   each run by hand locally, but only `tests.yml` has executed on a runner.
   That one is now green end to end ([run 34396020152](https://github.com/hellonovenuk-lang/LaunchTrace/actions/runs/34396020152)).

## Recommended immediate next action

**Create a free Tavily account, put the key in `.env`, and run one current
week: `python -m src.pipeline weekly`.**

It takes about ten minutes and it answers the only question that matters right
now. The 5.5/week figure is from four January 2018 weeks with the pipeline's
most important enrichment stage switched off, and it cannot be repaired by
switching enrichment on and re-running those same weeks — current web evidence
about a 2018 brand is not the evidence that existed in 2018. Only a current
week measures the product: current journal, current goods/services text,
current Companies House evidence, current enrichment, current classification.
Until that has run, you do not know whether LaunchTrace Food is a GREEN
business or a FAIL — and every other decision, including whether to spend the
eight hours above, depends on that number.

Then send that week's opportunities to five suppliers from
`outreach/prospects_seed.csv` and ask them one question: *would you want to
reach these companies?*

---

## Session 2: commercial readiness

### What was built

The product could produce a feed. It could not yet be sold. This session built
the path from a supplier prospect to a paying customer, and stopped at every
point where a credential would have been needed.

| Area | Module | Notes |
| --- | --- | --- |
| Prospect model and lifecycle | `src/sales/models.py` | 32 fields, checked status transitions |
| Storage, duplicates, suppression | `src/sales/store.py` | Copy-on-write; a save can never shorten the suppression list |
| ICP scoring | `src/sales/icp.py`, `config/icp_scoring.json` | Explainable keyword model, every score returns its reasons |
| Lead loading | `src/sales/leads.py` | From the database or a delivered CSV; read-only view of the pipeline |
| Prospect-specific matching | `src/sales/matching.py`, `config/supplier_profiles.json` | Ranks qualified leads by supplier fit; never promotes one that did not qualify |
| Preview rendering | `src/sales/preview.py` | Markdown briefing, email block, CSV |
| Outreach drafting and due states | `src/sales/outreach.py` | No send path exists, and a test enforces it |
| Customer sample | `src/deliver/sample_pack.py` | Branded HTML plus a clean CSV |
| Transactional email | `src/deliver/transactional.py` | Five templates |
| Customer lifecycle | `src/customer_lifecycle.py` | Onboarding, past-due grace, recovery, cancellation |
| Feedback | `src/sales/feedback.py` | Seven states; never feeds back into scoring |
| Metrics and cost | `src/sales/metrics.py`, `src/sales/costs.py`, `config/costs.json` | Every assumed price labelled as assumed |
| Website business logic | `src/web/services.py`, `src/web/api.py` | JSON API; HTML pages call the same functions |
| Operator CLI | `src/admin.py`, `src/sales_commands.py` | 12 commands |

### What was verified by running it

* **The whole sales path**, against the real January 2018 historical data:
  prospect list → ICP scoring → preview for four different supplier types →
  drafted email → sample pack → HTML report opened and read → customer created
  → onboarding → payment failure → recovery → cancellation → feedback →
  `business-status`.
* **Different supplier types get genuinely different shortlists.** A label
  printer, a contract manufacturer, a 3PL and a distributor were each run
  against the same 22 opportunities and each received a different ranking, with
  a different lead first.
* **The 60-prospect list**: audited, migrated to the new schema with no research
  lost, scored, and prioritised. One real duplicate found and resolved.
* **The sample report**, rendered in a browser and inspected as a customer
  would see it.
* **531 tests**, `ruff check`, `ruff format --check`, `mypy src`, and the
  end-to-end smoke test.

### Defects found and fixed in this session

Six, all found by running the thing rather than by reading it.

1. **Outbox files overwrote each other.** Onboarding writes three messages to
   one recipient in the same second; `Path.with_suffix` treated the dot in the
   email domain as a file extension and truncated the name, so all three
   collapsed onto one file — and the collision-avoidance loop then spun
   forever. Two of the three messages would have been silently lost. Fixed by
   building the filename explicitly and including the message kind.
2. **The preview age filter emptied every preview.** It measured against
   today, so historical validation data — the only data that exists before the
   first live Friday — was entirely excluded. Now measured against the newest
   lead in the source, with a staleness warning shown instead of silence.
3. **The same applicant appeared twice in one preview.** Two brands from one
   company read as one lead repeated. Company diversity is now a hard rule,
   even when it means returning two leads instead of three.
4. **Template substitution was fragile.** Matching placeholder prose broke when
   a placeholder wrapped across two lines, leaving raw template text in a
   draft. Replaced with explicit `{{TOKEN}}` substitution, plus a check that
   reports any token left unsubstituted.
5. **An existing database could not be upgraded.** `create_all` does not add
   columns to existing tables, so every query failed after the schema changed.
   `init-db` now adds missing columns additively, so an owner who has already
   run it does not have to delete their customers to upgrade.
6. **Two tests were reaching the real development database.** Patching the
   settings accessor did not reach the engine. Fixed by setting the database
   URL through the environment.

### Deliberate decisions worth recording

* **The ICP Priority A threshold was set to 55, not 60.** At 60 only five
  prospects qualified, which is not a working set. This is LaunchTrace's own
  sales prioritisation, not the LaunchTrace Score — no signal threshold was
  touched, and the reasoning is written into `config/icp_scoring.json`.
* **`carton_relevance` and `brokerage_relevance` were added to the delivered
  CSV.** Without them a carton supplier or a broker could not be matched at
  all. Older CSVs lacking the columns read as `UNKNOWN` rather than `NONE`,
  because "not recorded" is not the same claim as "not relevant".
* **Past-due customers keep the feed for 14 days, then stop.** An expired card
  should not lose a customer; a non-paying account should not become an
  indefinite free subscription.
* **Feedback does not change scoring.** Collect it, then change weights
  deliberately with the reasoning written down.

### What this session did not do

No email was sent. No account was created. No domain was bought. The website
was not redesigned. No customer dashboard was built. The core scoring
methodology was not changed, and no threshold was moved to make any number look
better.

### Still not validated

Unchanged by this session, and the part that decides whether there is a
business: **no supplier has seen any of this.** Every conversion rate reported
by `business-status` currently has a denominator of zero or one.

The volume figure of 5.5 good opportunities per week comes from four January
2018 weeks run without web enrichment and without goods-and-services text. It
is a historical sanity check on the machinery, not a measurement of the
product, and it cannot be turned into one by re-running those weeks with
enrichment connected — today's web describes what a 2018 brand became, not what
was knowable about it that week.

The measurement requires **current UKIPO weekly journal + current
goods/services text + current Companies House evidence + current Tavily/web
enrichment + current classification**. See
[`COMMERCIAL_READINESS.md`](COMMERCIAL_READINESS.md) §3.
