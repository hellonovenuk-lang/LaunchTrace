# HANDOFF

What is built, what is waiting on you, and exactly what to do next.

Read section 4 first if you only read one thing.

---

## 1. COMPLETED

Built, tested, and working right now with no external account of any kind.

### The pipeline

| | |
| --- | --- |
| **Journal ingest** | Four interchangeable sources: live ipo.gov.uk, the official IPO Open Data release, a local file you downloaded by hand, and test fixtures. Streaming download, content-addressed cache, SHA-256 integrity, duplicate-processing prevention |
| **Parsing** | Streaming XML parser using `iterparse`, tolerant of namespace and element-name variation, so a 150 MB journal never lands in memory. A second parser for the Open Data format. Malformed records are skipped and counted, not fatal |
| **Food filter** | Configurable taxonomy of 11 product groups. Explicit exclusions for restaurants, retail services, raw agricultural produce, pet food, alcohol, 184 major brand owners, and portfolio filings |
| **Company verification** | Companies House matching with explicit confidence scores and stored evidence. Two official routes: the REST API, and the free monthly bulk snapshot (no key required) |
| **Web enrichment** | Search-provider abstraction over Tavily, Serper and Brave. Detects official website, contact page, marketplace and major-retailer presence, and launch signals |
| **LaunchTrace Score** | Explainable 0–100 with plain-English reasons, calibrated so bands actually discriminate, capped where evidence is thin, and normalised for enrichment that did not run |
| **Buying intent** | Nine supplier categories mapped per product group and adjusted by launch stage. Always phrased as inferred relevance, never as current purchasing |
| **CSV export** | 26 sales-ready columns. Internal debug state deliberately excluded |
| **Email** | Weekly feed, welcome, sample and failure-alert templates, rendered independently of sending |
| **QA report** | Full funnel, rejection reasons, major-brand detections, duplicate and volume checks, and cross-checks of the pipeline's own output |
| **Fail-closed safety** | A run stops rather than delivering when retrieval fails, volume is implausible, enrichment broadly fails, scoring broadly fails, or the email will not render. One bad record is logged and skipped |
| **Review mode** | `SEND_MODE=review` is the default. Nothing reaches a customer without explicit approval |
| **Idempotency** | Journals are never processed twice, opportunities are updated rather than duplicated, and a delivery already made is never repeated |

### Everything else

- **Database.** 14 tables, source data kept separate from derived data. PostgreSQL schema generated from the models and **verified against a real PostgreSQL 16**. Runs on local SQLite with zero setup
- **Website.** Landing page, sample-request form (honeypot, rate limit, disposable-domain rejection, IP stored only as a salted hash), self-service opt-out, legal pages, token-protected operator view, security headers and a content security policy
- **Billing.** Stripe checkout, billing portal, cancellation, signature-verified webhooks with per-event idempotency, and the full subscription state machine. Runs in stub mode without a key so the whole flow is testable
- **Operator CLI.** 20 commands covering runs, approval, sending, CSV regeneration, customers, suppression, errors and diagnostics
- **Container.** Dockerfile **built and run in this session**: 369 MB, non-root, health-checked, serving the site
- **Tests.** 390 tests, 76% line coverage, no test touching a live external API
- **Quality gates.** `ruff check`, `ruff format --check` and `mypy src` all clean
- **CI/CD.** Four workflows: tests, the Friday pipeline with three retry windows, manual backfill and validation, and a deploy workflow that is a build check until you opt in
- **Documentation.** README written for a non-technical owner, plus privacy notice, terms, data-source attribution, legitimate interests assessment and a retention note — all labelled as drafts needing your review
- **Prospecting.** 60 researched UK supplier companies with the reason each one fits, a defined schema, and three editable email templates. **No code in this repository can send any of it**

### The four-week validation

Ran on **real UKIPO data**: four consecutive real journal weeks (5,319 real
trade mark records), matched against a **real Companies House snapshot of
5,689,367 companies**.

| | |
| --- | --- |
| Records parsed | 5,319 |
| Food-class candidates | 614 |
| Packaged-food candidates | 237 |
| Matched to Companies House | 165 |
| Emerging-brand candidates | 173 |
| **Good opportunities (HIGH + MEDIUM)** | **22, an average of 5.5 per week** |
| Band | **QUESTIONABLE** (4–7 per week) |

**Read `reports/validation/4_week_summary.md` before drawing conclusions.** The
number is real but it is a floor, not a ceiling: it was produced with no web
enrichment and no goods-and-services text. Section 4 explains what to do about
that.

---

## 2. WORKING BUT NOT CONNECTED

Complete in code. Each needs one account from you. **The system runs without
every one of them** — each has a working fallback, listed below.

### 2.1 Web search — the highest-value one

| | |
| --- | --- |
| **Provider** | Tavily (recommended), Serper, or Brave Search |
| **URL** | <https://tavily.com/> · <https://serper.dev/> · <https://brave.com/search/api/> |
| **Account** | Free tier. Tavily gives 1,000 searches/month; a weekly run uses under 150 |
| **Credential** | An API key |
| **Where it goes** | `.env` as `SEARCH_PROVIDER=tavily` and `SEARCH_API_KEY=...`; GitHub secret `SEARCH_API_KEY` and variable `SEARCH_PROVIDER` |
| **How to verify** | `python -m src.pipeline check-config` shows `Web enrichment  tavily` |
| **Without it** | The pipeline runs, but cannot tell an early-stage brand from an established one, so every record is capped below the HIGH band. **This is why the validation shows 0 HIGH.** |
| **Cost** | £0 |

### 2.2 Companies House API — optional

| | |
| --- | --- |
| **Provider** | Companies House |
| **URL** | <https://developer.company-information.service.gov.uk/> |
| **Account** | Free. Create an application, then a REST API key |
| **Credential** | An API key |
| **Where it goes** | `.env` as `COMPANIES_HOUSE_API_KEY=...`; GitHub secret of the same name |
| **How to verify** | `check-config` shows `Company registry  companies_house_api` |
| **Without it** | Run `python -m src.pipeline build-company-index --download` once a month. It uses the free bulk snapshot, needs no account, and **this is what produced the validation results above** |
| **Cost** | £0 either way |

### 2.3 Resend — email

| | |
| --- | --- |
| **Provider** | Resend |
| **URL** | <https://resend.com/> |
| **Account** | Free tier: 3,000 emails/month. You must verify your sending domain via DNS |
| **Credential** | An API key with sending access |
| **Where it goes** | `.env` as `RESEND_API_KEY=...` and `EMAIL_FROM=...`; GitHub secret `RESEND_API_KEY`, variable `EMAIL_FROM` |
| **How to verify** | `check-config` shows `Email  Resend (live)`. Then approve a run and send it to yourself first |
| **Without it** | Every email is written to `reports/outbox/` exactly as it would have been sent. Nothing is lost and nothing is sent by accident |
| **Cost** | £0 at this volume |

### 2.4 Stripe — subscriptions

| | |
| --- | --- |
| **Provider** | Stripe |
| **URL** | <https://stripe.com/> |
| **Account** | Free to open. Start in **Test mode** |
| **Credentials** | Secret key, webhook signing secret, and two price IDs |
| **What you must create by hand** | The prices cannot be created without a live key. In **Product catalogue** create `LaunchTrace Food — Founding access` at **£79.00 GBP monthly recurring**, and `LaunchTrace Food — Standard` at **£129.00 GBP monthly recurring**. Copy each price ID (starts `price_`) |
| **Webhook** | **Developers → Webhooks → Add endpoint**, URL `https://your-site/billing/webhook`, events `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`. Copy the signing secret |
| **Where it goes** | `.env` as `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_FOUNDING_PRICE_ID`, `STRIPE_STANDARD_PRICE_ID` |
| **How to verify** | `stripe listen --forward-to localhost:8000/billing/webhook`, then `stripe trigger checkout.session.completed`, then `python -m src.pipeline customers` — the customer should appear |
| **Without it** | The checkout button leads to a page explaining subscriptions are not open yet, rather than to a broken checkout. You can still add customers by hand and invoice manually |
| **Cost** | 1.5% + 20p per transaction. Nothing until you are paid |

### 2.5 Supabase — hosted database

| | |
| --- | --- |
| **Provider** | Supabase (any PostgreSQL works) |
| **URL** | <https://supabase.com/> |
| **Account** | Free tier. Create a project in the **London** region |
| **Credential** | The connection string from **Project Settings → Database → Connection string → URI**, with `[YOUR-PASSWORD]` replaced |
| **Where it goes** | `.env` as `DATABASE_URL=postgresql://...`; GitHub secret `DATABASE_URL` |
| **How to verify** | `python -m src.pipeline init-db` then `check-config` shows `Database  PostgreSQL` |
| **Without it** | A local SQLite file, which is genuinely fine for months. **You need this before the GitHub Actions run is useful**, because a scheduled run has nowhere else to persist |
| **Cost** | £0 |

### 2.6 An LLM — optional accuracy

| | |
| --- | --- |
| **Provider** | Anthropic or OpenAI |
| **URL** | <https://console.anthropic.com/> · <https://platform.openai.com/api-keys> |
| **Credential** | An API key |
| **Where it goes** | `.env` as `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` |
| **How to verify** | `check-config` shows `LLM classifier  anthropic` |
| **Without it** | Rule-only classification, which is more conservative and under-detects some filings |
| **Cost** | Pennies per week on a small model |

### 2.7 Website hosting and a domain

| | |
| --- | --- |
| **Provider** | Fly.io or Render |
| **URL** | <https://fly.io/> · <https://render.com/> |
| **What is ready** | A verified Dockerfile and a `fly.toml`. `fly launch --no-deploy` then `fly deploy` |
| **Also needed** | A domain, pointed at the host, and `SITE_URL` set to it |
| **How to verify** | `https://your-domain/healthz` returns `{"status":"ok"}` |
| **Cost** | £0–7/month plus ~£12/year for the domain |

---

## 3. BLOCKED

One thing could not be completed in this environment, and one is a judgement
call that is yours to make.

### 3.1 Live UKIPO retrieval could not be exercised from this sandbox

**What happened.** Every request to `ipo.gov.uk` from this build environment
returned **HTTP 403 with a bot-protection captcha page**, on every path and
with every user agent tried, including through a real headless browser. Other
government hosts (`gov.uk`, `data.gov.uk`, `assets.publishing.service.gov.uk`,
`download.companieshouse.gov.uk`) all worked normally, so this is IPO's own
protection reacting to a datacentre IP range, not a network fault and not a bug
in the code.

**What this means.** The live ingest path (`JOURNAL_SOURCE=ukipo_http`) is fully
implemented — journal-number arithmetic verified against the IPO's own URL
scheme, index-page link discovery, ten fallback filename patterns, streaming
download, retry with backoff, caching and checksums — but it has **never been
run against the live endpoint**. It may work first time from your machine or
from a GitHub Actions runner. It may need the filename pattern adjusting.

**What was done instead.** The validation used the **official IPO Open Data
release**, which is the same authority publishing the same records under the
same licence, and which is reachable. That is real UKIPO data, not a
substitute.

**What you should do.** On the first Friday, run:

```bash
python -m src.pipeline weekly
```

- If it works, you are done — nothing further is needed.
- If it returns `journal_retrieval_failed` with a 403, download the journal by
  hand from <https://www.ipo.gov.uk/t-tmj.htm>, save it as
  `data/journals/2026-036.xml`, and run
  `JOURNAL_SOURCE=local python -m src.pipeline weekly --journal 2026-036`.
  Then send me — or whoever picks this up next — the actual file, and the
  parser can be tuned to it in minutes. `python -m src.pipeline probe-journal
  <file>` prints exactly what is needed.

The system was built so this cannot stop you: three working sources, and a
fail-closed run that tells you what it tried.

### 3.2 The validation result is genuinely marginal, and that is the finding

**5.5 good opportunities per week falls in the QUESTIONABLE band.** That is not
a failure of the build, and it should not be explained away. It is the number,
and here is what is actually behind it:

- **No web enrichment ran.** Without it, no record can reach the HIGH band at
  all — the 0 HIGH in the report is structural, not a discovery about the
  signal. This is the single change most likely to move the result.
- **No goods and services text.** The Open Data release does not publish it, so
  product categorisation fell back to Nice class, SIC codes and the brand name.
  **The live weekly journal XML does carry goods text**, so a real Friday run
  has strictly more evidence than this validation had.
- **Rule-only classification.** More conservative than the full classifier.
- **The threshold is a choice, not a fact.** The report includes a sensitivity
  table: at a minimum score of 55 the figure is 7.25/week, at 50 it is 8.0/week.
  Lowering the bar raises volume and lowers quality, and that is a commercial
  decision, not a technical one.

**Do not conclude the business is validated, and do not conclude it is dead.**
The next test is not technical. Send `reports/validation/top_opportunities.csv`
to five real suppliers and ask whether these are companies they would want to
reach. If five suppliers say yes to 5 brands a week, you have a business at
£79/month. If they need 20 a week, you have a different product to build.

---

## 4. OWNER ACTIONS

In order. Steps 1–4 take about 30 minutes and get you a real sample you can
show to a supplier. Everything after that is optional until someone says yes.

### Get a sample you can sell with (about 30 minutes)

1. **Open a terminal** and go into the project folder:
   `cd LaunchTrace`
2. **Set it up** (once):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
   cp .env.example .env
   python -m src.pipeline init-db
   ```
3. **Prove it works**: `python -m src.pipeline smoke-test`
   You should see `SMOKE TEST: PASS`.
4. **Read the evidence**: open `reports/validation/4_week_summary.md`, then
   `reports/validation/top_opportunities.csv`. Those 22 brands are real
   companies from real UKIPO journals. **That CSV is your sales sample.**

### Make the feed as good as it can get (about 20 minutes)

5. **Create a Tavily account** at <https://tavily.com/>. Free.
6. Copy your API key from the Tavily dashboard.
7. Open `.env` in a text editor. Find the line `SEARCH_PROVIDER=none` and
   change it to `SEARCH_PROVIDER=tavily`. Find `SEARCH_API_KEY=` and paste your
   key after the `=`. Save.
8. **Build the free company index** (about 10 minutes, downloads ~500 MB):
   `python -m src.pipeline build-company-index --download`
9. **Re-run the validation**: `python -m src.pipeline validate --weeks 4`
10. **Compare** the new `4_week_summary.md` with the old figure of 5.5/week.
    This tells you what the product is really worth.

### Talk to suppliers (this is the actual next step)

11. Open `outreach/prospects.csv`. It has 60 real UK companies with a reason
    each one fits.
12. For the first ten, open their website and find the real `sales@` or
    `enquiries@` address. Paste it into the `generic_contact_email` column.
13. Open `outreach/templates/email_1_first_contact.md`.
14. For each of those ten, pick **three real brands** from
    `top_opportunities.csv` that genuinely suit what that company makes. A film
    converter should see snack and bar brands, not sauces.
15. Send those ten emails **yourself, from your own mailbox**. Nothing in this
    repository will do it for you, on purpose.
16. Record replies in the `status` and `reply_status` columns.

**Stop here until someone replies.** Everything below is for after that.

### Go live (only once a supplier says yes)

17. **Create a Supabase account** at <https://supabase.com/>. New project,
    London region. Write the database password down.
18. Project Settings → Database → Connection string → URI. Copy it, replace
    `[YOUR-PASSWORD]` with your password, and put it in `.env` as
    `DATABASE_URL=`.
19. Run `python -m src.pipeline init-db`.
20. **Create a Resend account** at <https://resend.com/>. Add your domain and
    add the DNS records it gives you wherever your domain is registered.
21. Resend → API Keys → Create API Key, sending access. Copy it into `.env` as
    `RESEND_API_KEY=`.
22. **Create a Stripe account** at <https://stripe.com/>. Stay in Test mode.
23. Stripe → Product catalogue → Add product:
    `LaunchTrace Food — Founding access`, price `79.00 GBP`, Recurring,
    Monthly. Save, click the price, copy the ID starting `price_`.
24. Repeat for `LaunchTrace Food — Standard` at `129.00 GBP` monthly.
25. Stripe → Developers → API keys. Copy the **Secret key**.
26. Put all four into `.env`: `STRIPE_SECRET_KEY`,
    `STRIPE_FOUNDING_PRICE_ID`, `STRIPE_STANDARD_PRICE_ID`.
27. **Deploy the site.** Install `flyctl` from
    <https://fly.io/docs/hands-on/install-flyctl/>, then in the project folder:
    `fly launch --no-deploy` and `fly deploy`.
28. Point your domain at it and set `SITE_URL` in `.env` to your real address.
29. Stripe → Developers → Webhooks → Add endpoint, URL
    `https://your-domain/billing/webhook`, with the six events listed in
    section 2.4. Copy the signing secret into `.env` as
    `STRIPE_WEBHOOK_SECRET`.

### Turn on the Friday schedule

30. In this repository on GitHub: **Settings → Secrets and variables →
    Actions**.
31. Under **Secrets**, add `DATABASE_URL`, `SEARCH_API_KEY`, `RESEND_API_KEY`
    (and `LLM_API_KEY` if you added one), each with the value from your `.env`.
32. Under **Variables**, add `SEND_MODE` = `review`, `SITE_URL`,
    `ADMIN_EMAIL`, `SEARCH_PROVIDER` = `tavily`, `EMAIL_FROM`.
33. **Actions → Weekly pipeline → Run workflow** to test it now rather than
    waiting for Friday.
34. Each Friday: download the run's artefact, read the QA report, then
    `approve` and `send`.
35. **Only after four clean weeks** consider changing the `SEND_MODE` variable
    to `automatic`.

### Before you take real money

36. Read `docs/PRIVACY.md`, `docs/TERMS.md` and
    `docs/LEGITIMATE_INTERESTS_ASSESSMENT.md`. Fill in every `[BRACKET]`.
37. Have a solicitor review them. They are drafts, and they say so.
38. Email the IPO to confirm the weekly Journal XML is Open Government
    Licence v3.0, and record the reply in `docs/ATTRIBUTION.md`.
39. Register with the ICO as a data controller if you are not already:
    <https://ico.org.uk/for-organisations/data-protection-fee/>. It is £40–60 a
    year.
40. Switch Stripe out of Test mode and swap in the live keys.

---

## 5. Publishing state

All work is committed on the branch `claude/launchtrace-food-mvp-7h7bc6` and
pushed to <https://github.com/hellonovenuk-lang/LaunchTrace>.

If a push had failed, the commits would still be here locally and the remaining
action would have been:

```bash
git push -u origin claude/launchtrace-food-mvp-7h7bc6
```

The visible effect of the push: the branch appears on GitHub with the full
implementation, and the four workflows become available under the **Actions**
tab. **The Friday schedule only fires from the default branch**, so merging
this branch into `main` is what actually starts the weekly run.

---

## 6. Ideas deliberately not built

Recorded so they are not lost, and not built because they are out of scope
until LaunchTrace Food has paying customers:

- A customer dashboard (the MVP is deliberately an email and a CSV)
- Additional sectors — cosmetics, pet, household. The architecture supports
  them as configuration; see README §20
- Automated outreach to identified brands (deliberately excluded, and a
  significant compliance decision, not just a feature)
- Contact-name enrichment for identified brands (materially changes the
  privacy position — the LIA would need redoing first)
- Scoring feedback: letting subscribers mark leads good or bad, and tuning the
  weights from it. The `score_events` table already records the history this
  would need
- Alerting between weekly runs when a very high-scoring brand appears
- International trade mark offices (EUIPO, WIPO)
