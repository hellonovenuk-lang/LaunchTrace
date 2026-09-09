# LaunchTrace

**Find emerging food brands while they're still building.**

LaunchTrace turns newly published UK trade mark activity into a weekly feed of
sales opportunities for packaging, manufacturing and FMCG suppliers.

The first product is **LaunchTrace Food**. Every Friday it reads the UK
Intellectual Property Office's Trade Marks Journal, keeps the filings that look
like packaged consumer food products, verifies each applicant against Companies
House, researches how established the brand already is, scores the opportunity,
works out what kind of supplier that brand is likely to need, and sends
subscribers an email and a CSV.

**LaunchTrace is not** trade mark legal advice, a trade mark watching service, a
trade mark database, or a CRM. It is a commercial signal, and it says so
everywhere it can be misread.

## Two command lines

| | |
| --- | --- |
| `python -m src.pipeline` | **The product.** Ingest a journal, score it, deliver the feed. Sections 4–21 below. |
| `python -m src.admin` | **The business.** Prospects, outreach, samples, customers, feedback, cost. Section 22. |

Both print `--help`. If you are here to get a customer rather than to run a
pipeline, start at [`FIRST_CUSTOMER_PLAYBOOK.md`](FIRST_CUSTOMER_PLAYBOOK.md).

---

## Contents

1. [What it actually does](#1-what-it-actually-does)
2. [How the pieces fit together](#2-how-the-pieces-fit-together)
3. [Setting it up on your computer](#3-setting-it-up-on-your-computer)
4. [Running the smoke test](#4-running-the-smoke-test)
5. [Processing one journal week](#5-processing-one-journal-week)
6. [Backfilling four weeks](#6-backfilling-four-weeks)
7. [Generating and approving a report](#7-generating-and-approving-a-report)
8. [Managing customers](#8-managing-customers)
9. [Connecting Companies House](#9-connecting-companies-house)
10. [Connecting a database (Supabase)](#10-connecting-a-database-supabase)
11. [Connecting Resend for email](#11-connecting-resend-for-email)
12. [Connecting Stripe for payments](#12-connecting-stripe-for-payments)
13. [Connecting web enrichment](#13-connecting-web-enrichment)
14. [Connecting an LLM](#14-connecting-an-llm)
15. [Deploying the website](#15-deploying-the-website)
16. [Setting up GitHub secrets](#16-setting-up-github-secrets)
17. [How the scheduled runs work](#17-how-the-scheduled-runs-work)
18. [Turning automatic delivery off](#18-turning-automatic-delivery-off)
19. [When something goes wrong](#19-when-something-goes-wrong)
20. [Adding another product category later](#20-adding-another-product-category-later)
21. [What it costs to run](#21-what-it-costs-to-run)
22. [Running the business: prospects, outreach and customers](#22-running-the-business-prospects-outreach-and-customers)

---

## 1. What it actually does

Each week, in order:

1. **Ingest.** Download the UKIPO Trade Marks Journal for that Friday.
2. **Parse.** Read every record: application number, brand name, filing and
   publication dates, applicant, Nice classes, goods description.
3. **Filter.** Keep only what looks like a packaged consumer food product.
   Throw out restaurants, raw agricultural produce, pet food, alcohol,
   service-only filings, and filings from major brand owners.
4. **Verify.** Match each applicant to Companies House and record how confident
   that match is.
5. **Research.** For the survivors, look for the brand's website, whether it is
   selling yet, and whether it is already in the supermarkets.
6. **Score.** Produce a 0–100 LaunchTrace Score with plain-English reasons.
7. **Map.** Work out which supplier categories a brand at that stage typically
   needs.
8. **Deliver.** Write a CSV, render the email, produce a QA report — and, in
   review mode, wait for you to approve it before anything is sent.

Each stage is deliberately cheaper than the next, so the expensive research
only ever runs against records that already earned it.

## 2. How the pieces fit together

```
src/
  ingest/      getting the journal (live UKIPO, IPO Open Data, local file, fixtures)
  parse/       reading journal XML and Open Data into records
  classify/    the packaged-food filter, and the optional LLM classifier
  enrich/      Companies House matching, and web research
  score/       the LaunchTrace Score, launch stage, supplier buying intent
  deliver/     CSV, email templates, the internal QA report
  billing/     Stripe subscriptions and webhooks
  db/          database tables and persistence
  web/         the website, sample form and operator view
  pipeline.py  the command line
config/        all the business rules, as JSON you can edit
data/journals/ four real UKIPO journal weeks (January 2018), for the historical
               sanity test
migrations/    the PostgreSQL schema
reports/       everything the pipeline produces
tests/         the test suite
outreach/      your own prospect *research* and email templates (nothing sends).
               Live contacts, replies, dates and opt-outs are in the database,
               not here
docs/          privacy, terms, attribution, retention, legitimate interests
```

The project is installed rather than picked up from whichever directory you
happen to be in: `pip install -r requirements-dev.txt` installs LaunchTrace
itself editable, which is what makes `import src` behave the same for the
tests, the type checker, `python -m src.pipeline` and CI.

**The rules live in `config/`, not in the code.** To change what counts as a
food product, how the score is weighted, or which suppliers a category maps to,
edit the JSON. You do not need to touch Python.

| File | What it controls |
| --- | --- |
| `config/food_taxonomy.json` | Which filings count as packaged food, and their categories |
| `config/exclusions.json` | What gets thrown out, and the major-brand list |
| `config/scoring.json` | The LaunchTrace Score weights and bands |
| `config/buying_intent.json` | Which suppliers each product category needs |
| `config/customer_plans.json` | Plans, prices and recipient limits |
| `config/validation_bands.json` | The volume bands and safety guardrails |

### Continuous integration

`.github/workflows/tests.yml` runs on every push and is **green on GitHub**:
lint, format check, `mypy src`, 531 tests, the end-to-end smoke test, a check
that `migrations/0001_initial.sql` still matches the models, and a second job
that applies that schema to a real PostgreSQL 16
([run 34396020152](https://github.com/hellonovenuk-lang/LaunchTrace/actions/runs/34396020152)).

## 3. Setting it up on your computer

You need Python 3.11 or newer. To check, open a terminal and type:

```bash
python3 --version
```

If that prints `Python 3.11` or higher, you are ready. If not, install Python
from <https://www.python.org/downloads/>.

Then, in the terminal:

```bash
# 1. Go into the project folder
cd LaunchTrace

# 2. Create an isolated Python environment (keeps this project's
#    libraries separate from everything else on your machine)
python3 -m venv .venv

# 3. Turn it on. You will need to do this every time you open a new terminal.
source .venv/bin/activate          # on macOS or Linux
# .venv\Scripts\activate           # on Windows

# 4. Install what the project needs
pip install -r requirements-dev.txt

# 5. Create your settings file
cp .env.example .env

# 6. Create the database (a local file, no server needed)
python -m src.pipeline init-db
```

To see what is connected and what is not:

```bash
python -m src.pipeline check-config
```

That prints every integration and, for anything not yet connected, what it
would unlock. **Nothing in the list is required to run the system** — every
missing credential has a working fallback.

## 4. Running the smoke test

This runs the entire pipeline on built-in test data. No internet, no accounts,
no API keys. If this works, the software works.

```bash
python -m src.pipeline smoke-test
```

You will see the funnel, then `SMOKE TEST: PASS`. It writes:

- `reports/smoke/2025-050/opportunities.csv` — what a customer would receive
- `reports/smoke/2025-050/weekly_email.html` — open this in a browser to see
  the actual email
- `reports/smoke/2025-050/qa_report.json` — the internal quality report

**Do this first, and do it again any time you change something in `config/`.**

## 5. Processing one journal week

For the most recent journal:

```bash
python -m src.pipeline weekly
```

For a specific one:

```bash
python -m src.pipeline weekly --journal 2026-036
python -m src.pipeline weekly --date 2026-09-04
```

Results go to `reports/runs/<journal number>/`.

**If the live UKIPO download fails**, you have two fallbacks:

```bash
# (a) Use the IPO's Open Data release instead
python -m src.pipeline fetch-open-data --weeks 4
JOURNAL_SOURCE=open_data python -m src.pipeline weekly

# (b) Download the journal by hand from https://www.ipo.gov.uk/t-tmj.htm,
#     save it as data/journals/2026-036.xml, then:
JOURNAL_SOURCE=local python -m src.pipeline weekly --journal 2026-036
```

## 6. Backfilling four weeks

To process the last four journals and store them:

```bash
python -m src.pipeline backfill --weeks 4
```

To re-run the **January 2018 historical sanity test** — the funnel counts, the
rejection reasons and the opportunity totals for each of those four weeks:

```bash
python -m src.pipeline validate --weeks 4
```

This is an engineering and historical check that parsing, matching, scoring and
weekly volume behave correctly on real data. **It is not the commercial
validation, and re-running it with web enrichment switched on does not make it
one** — today's web tells you what a 2018 brand became over eight years, not
what was knowable about it in the week it was published. The decisive product
test is a current one: current UKIPO weekly journal + current goods/services
text + current Companies House evidence + current Tavily/web enrichment +
current classification, from a live `python -m src.pipeline weekly`.

That writes `reports/validation/`:

| File | What it is |
| --- | --- |
| `4_week_summary.md` | The report to read. Funnel per week, rejection reasons, threshold sensitivity |
| `4_week_summary.json` | The same, machine-readable |
| `week_1.csv` … `week_4.csv` | The deliverable opportunities for each week |
| `top_opportunities.csv` | The strongest across all four weeks. Real companies from real journals, and eight years old — see `FIRST_CUSTOMER_PLAYBOOK.md` before showing it to anyone |
| `rejections.csv` | Every record dropped, with the stage and reason |

Read `4_week_summary.md` before you draw any conclusion from these numbers.

## 7. Generating and approving a report

By default `SEND_MODE=review`, which means **nothing is ever emailed to a
customer without you approving it**. The sequence is:

```bash
# 1. Run the week
python -m src.pipeline weekly

# 2. Look at what it produced
python -m src.pipeline status
python -m src.pipeline opportunities --band HIGH
open reports/runs/2026-036/weekly_email.html    # 'start' on Windows

# 3. If you are happy, approve that run (the id comes from 'status')
python -m src.pipeline approve --run-id run_20260904T130000_abc123

# 4. Send it
python -m src.pipeline send --run-id run_20260904T130000_abc123
```

Without a Resend key, step 4 writes each email to `reports/outbox/` instead of
sending it — so you can see exactly what would have gone out.

Other useful commands:

```bash
python -m src.pipeline regenerate-csv --journal 2026-036
python -m src.pipeline suppress --type company --value "Some Company Ltd" --reason "asked to be removed"
python -m src.pipeline errors
```

## 8. Managing customers

```bash
# Add a customer with up to three recipients
python -m src.pipeline add-customer \
  --company "PackCo Ltd" \
  --email sales@packco.co.uk buyers@packco.co.uk \
  --supplier-type flexible_packaging \
  --plan founding_monthly \
  --status active

# See who is subscribed
python -m src.pipeline customers
```

Once Stripe is connected, customers created through checkout are added
automatically and their status is kept in step with their subscription.

## 9. Connecting Companies House

**You may not need to.** LaunchTrace works without an API key by using the free
bulk snapshot Companies House publishes each month:

```bash
python -m src.pipeline build-company-index --download
```

That downloads about 500 MB, builds a local index of every UK company (roughly
5.7 million), and takes about ten minutes. No account, no key, no rate limit,
no cost. Re-run it monthly to stay current.

**If you would rather use the live API** (fresher, but rate-limited to 600
requests per five minutes):

1. Go to <https://developer.company-information.service.gov.uk/>
2. Sign in or create an account
3. Choose **Your applications** → **Create an application**
4. Name it "LaunchTrace", environment **Live**
5. Open the application and choose **Create new key** → **REST API**
6. Copy the key
7. Put it in `.env` as `COMPANIES_HOUSE_API_KEY=...`

To confirm it worked: `python -m src.pipeline check-config` should show
`Company registry  companies_house_api`.

## 10. Connecting a database (Supabase)

LaunchTrace uses a local file database by default, which is fine for months.
When you want a hosted one:

1. Go to <https://supabase.com/> and create a free account
2. **New project**. Name it "launchtrace", pick the London region, and set a
   database password — **write this down**
3. Wait for it to finish setting up
4. Go to **Project Settings** → **Database** → **Connection string** → **URI**
5. Copy it and replace `[YOUR-PASSWORD]` with the password from step 2
6. Put it in `.env` as `DATABASE_URL=postgresql://...`
7. Create the tables:

```bash
python -m src.pipeline init-db
```

Or paste `migrations/0001_initial.sql` into the Supabase SQL editor and run it.

To confirm: `python -m src.pipeline check-config` should show
`Database  PostgreSQL`.

## 11. Connecting Resend for email

1. Go to <https://resend.com/> and create an account
2. **Domains** → **Add Domain** → enter your domain (e.g. `launchtrace.co.uk`)
3. Resend shows you DNS records. Add them wherever your domain is registered.
   Verification usually takes a few minutes
4. **API Keys** → **Create API Key**. Name it "LaunchTrace", permission
   **Sending access**
5. Copy the key (you only see it once)
6. Put it in `.env`:

```
RESEND_API_KEY=re_...
EMAIL_FROM=LaunchTrace <feed@launchtrace.co.uk>
```

**Test it without risking a real send:** leave `SEND_MODE=review` and run
`python -m src.pipeline weekly`. Open the rendered email from
`reports/runs/<journal>/weekly_email.html` first. Only approve when you are
happy with it.

## 12. Connecting Stripe for payments

The prices cannot be created programmatically without a live key, so create
them once by hand.

1. Go to <https://stripe.com/> and create an account
2. **Stay in Test mode** (the toggle at the top right) until everything works
3. **Product catalogue** → **Add product**
   - Name: `LaunchTrace Food — Founding access`
   - Price: `79.00 GBP`, **Recurring**, **Monthly**
   - Save, then click the price and copy its ID (starts `price_`)
4. Repeat for `LaunchTrace Food — Standard` at `129.00 GBP` monthly
5. **Developers** → **API keys**. Copy the **Secret key** (`sk_test_...`)
6. **Developers** → **Webhooks** → **Add endpoint**
   - URL: `https://your-site.co.uk/billing/webhook`
   - Events: `checkout.session.completed`,
     `customer.subscription.created`, `customer.subscription.updated`,
     `customer.subscription.deleted`, `invoice.paid`,
     `invoice.payment_failed`
   - Save, then copy the **Signing secret** (`whsec_...`)
7. Put them in `.env`:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_FOUNDING_PRICE_ID=price_...
STRIPE_STANDARD_PRICE_ID=price_...
```

**Test it locally** with the Stripe CLI (<https://stripe.com/docs/stripe-cli>):

```bash
stripe listen --forward-to localhost:8000/billing/webhook
stripe trigger checkout.session.completed
python -m src.pipeline customers      # the customer should now exist
```

Without any Stripe key the checkout button leads to a page explaining that
subscriptions are not open yet, rather than to a broken checkout.

When you are ready to take real money, repeat steps 3–6 with the Test-mode
toggle **off** and swap the keys for the live ones.

## 13. Connecting web enrichment

**This is the single most valuable credential to add.** Without it the pipeline
cannot tell an early-stage brand from an established one, so every record is
capped below the HIGH band — which is why the January 2018 sanity test shows 0
HIGH. Connecting it and then running one **current** week is the decisive
product test; connecting it and re-running 2018 is not.

Any one of these works. Free tiers are ample — a weekly run makes at most a few
hundred lookups.

| Provider | Where | Free tier |
| --- | --- | --- |
| Tavily | <https://tavily.com/> | 1,000 searches/month |
| Serper | <https://serper.dev/> | 2,500 searches one-off |
| Brave Search | <https://brave.com/search/api/> | 2,000 queries/month |

Sign up, copy the key, and put it in `.env`:

```
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=tvly-...
```

## 14. Connecting an LLM

Optional. The classifier works on rules alone; an LLM makes it more accurate on
filings whose goods description is ambiguous.

- **Anthropic**: <https://console.anthropic.com/> → **API Keys** → **Create Key**
- **OpenAI**: <https://platform.openai.com/api-keys>

```
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001
```

Use a small, cheap model — this runs against every qualified candidate. At a
few hundred candidates a week the cost is pennies.

## 15. Deploying the website

The site is one small Python application. It needs a host that runs Python,
about 512 MB of memory, and roughly £0–7/month.

**Fly.io** (<https://fly.io/>):

```bash
# Install flyctl from https://fly.io/docs/hands-on/install-flyctl/
fly launch --no-deploy      # accept the defaults; it detects Python
fly secrets set DATABASE_URL="..." STRIPE_SECRET_KEY="..." RESEND_API_KEY="..."
fly deploy
```

**Render** (<https://render.com/>): New → Web Service → connect this repository.

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn src.web.app:app --host 0.0.0.0 --port $PORT`
- Add the environment variables from your `.env`

To run it locally first:

```bash
uvicorn src.web.app:app --reload
```

Then open <http://localhost:8000>.

**Point your domain at it** and set `SITE_URL=https://launchtrace.co.uk` so the
links in emails and Stripe redirects are correct.

## 16. Setting up GitHub secrets

For the scheduled Friday run, GitHub needs the same credentials.

In this repository: **Settings** → **Secrets and variables** → **Actions**.

Under **Secrets** → **New repository secret**, add each one you have:

| Secret | Value |
| --- | --- |
| `DATABASE_URL` | Your Supabase connection string |
| `COMPANIES_HOUSE_API_KEY` | Only if you use the API rather than the bulk index |
| `SEARCH_API_KEY` | Your search provider key |
| `LLM_API_KEY` | Your LLM key, if using one |
| `RESEND_API_KEY` | Your Resend key |

Under **Variables** → **New repository variable**:

| Variable | Value |
| --- | --- |
| `SEND_MODE` | `review` — change to `automatic` only when you trust it |
| `SITE_URL` | `https://launchtrace.co.uk` |
| `ADMIN_EMAIL` | Where failure alerts go |
| `SEARCH_PROVIDER` | `tavily`, `serper` or `brave` |
| `LLM_PROVIDER` | `anthropic`, `openai` or `none` |
| `EMAIL_FROM` | `LaunchTrace <feed@launchtrace.co.uk>` |

**Secrets are hidden; variables are visible. Never put a key in a variable.**

## 17. How the scheduled runs work

`.github/workflows/weekly-pipeline.yml` runs at **13:00, 16:00 and 19:00 UTC
every Friday**. Three attempts, because the journal is not always up at the
first one — a journal that has not been published yet is a normal condition,
not a failure.

Every run uploads its report and QA output as a downloadable artefact, kept for
60 days, so you can inspect a run even if you were not watching.

To run it by hand: **Actions** → **Weekly pipeline** → **Run workflow**. You
can give it a specific journal number.

To change the timing, edit the `cron:` lines. They are in UTC — remember
British Summer Time.

## 18. Turning automatic delivery off

It is off by default. `SEND_MODE=review` means a run must be approved before
anything is sent, and that is the state you should stay in for the first month.

To turn it off if it has been turned on:

- **Locally**: set `SEND_MODE=review` in `.env`
- **On GitHub**: Settings → Secrets and variables → Actions → Variables → set
  `SEND_MODE` to `review`

To stop everything immediately: **Actions** → **Weekly pipeline** → **⋯** →
**Disable workflow**.

To stop one customer without cancelling them: set `delivery_enabled` to false on
their row.

## 19. When something goes wrong

The system is built to **fail closed**. If it cannot do its job properly it
stops and tells you, rather than sending a bad report. These stop a run:

- The journal could not be downloaded
- The journal parsed to an implausible number of records
- Companies House enrichment failed across most records
- Scoring failed across most records
- The email would not render

A single record failing is different: it is logged and skipped, and the rest of
the week goes through.

**Start here:**

```bash
python -m src.pipeline status         # what ran, and how it ended
python -m src.pipeline errors         # the recent errors
python -m src.pipeline check-config   # what is connected
```

| Symptom | What it means | What to do |
| --- | --- | --- |
| `journal_not_yet_published` | The journal is not up yet | Nothing. The later attempts will retry |
| `journal_retrieval_failed` with a 403 | ipo.gov.uk is challenging your network | Download by hand and use `JOURNAL_SOURCE=local` (§5) |
| `volume_anomaly` | The record count is far outside normal | Check the journal downloaded fully. Adjust `config/validation_bands.json` if the IPO's volumes have genuinely changed |
| `enrichment_broadly_failed` | Companies House is failing | Check the API key, or rebuild the bulk index |
| No opportunities at all | The filters may be too tight | Look at `rejections.csv` — it names the reason for every dropped record |
| `Delivery not performed: SEND_MODE=review` | Working as designed | Approve the run (§7) |

**To restore after a failure:** re-run the same journal. Every stage is
idempotent — journals are not processed twice, opportunities are updated rather
than duplicated, and a delivery already sent is never sent again.

```bash
python -m src.pipeline weekly --journal 2026-036
```

**To diagnose an unfamiliar journal file** (for instance if the IPO changes its
XML format):

```bash
python -m src.pipeline probe-journal data/journals/2026-036.xml
```

That prints the element names it found, which is what you need to update
`FIELD_ALIASES` in `src/parse/journal_xml.py`.

## 20. Adding another product category later

The architecture was built so a second sector is configuration, not a rewrite.
For cosmetics, pet products or anything else:

1. Copy `config/food_taxonomy.json` to `config/cosmetics_taxonomy.json` and
   change the Nice classes and product groups
2. Copy `config/buying_intent.json` and map the new product groups to the
   suppliers that sector needs
3. Add a `sector` column where you need one — `customer_preferences` already
   has it
4. Point `FoodFilter` at the new taxonomy file

The pipeline, scoring, delivery, billing and website need no changes. **Do not
do this until LaunchTrace Food has paying customers.**

## 21. What it costs to run

Per weekly run, with everything connected:

| Item | Cost |
| --- | --- |
| UKIPO journal | £0 (Open Government Licence) |
| Companies House | £0 (bulk snapshot, or the free API) |
| LLM classification (~100 candidates at a cheap model) | ~£0.06 |
| Web search (~60 enrichments) | £0 on a free tier |
| Email (a handful of subscribers) | £0 on Resend's free tier |
| **Per run** | **under £0.10** |

Fixed monthly:

| Item | Cost |
| --- | --- |
| Website hosting (Fly.io or Render) | £0–7 |
| Database (Supabase free tier) | £0 |
| Domain | ~£1 |
| Stripe | 1.5% + 20p per transaction |
| **Total before revenue** | **under £10/month** |

Well inside the £30 target. The main cost discipline is architectural: web
research and LLM calls run only against records that already survived the free
filters, and results are cached.

---

## 22. Running the business: prospects, outreach and customers

Everything in this section is `python -m src.admin`. **None of it sends an
email to a prospect** — there is no send command and no code path to one. It
drafts, you send.

### The one command to know

```bash
python -m src.admin outreach-due
```

Who is due which sales action today, in the order to work through, with the
command for each. Ten minutes a day covers the whole pipeline.

### Prospects

```bash
python -m src.admin prospects list --priority A   # who to approach first
python -m src.admin prospects ready               # who is ready for a first email
python -m src.admin prospects show --prospect-id P012
python -m src.admin prospects audit               # duplicates, gaps, priority spread
python -m src.admin prospects rescore             # after editing the seed or weights
```

The list has two halves. **The research** — 60 UK suppliers, what they supply
and why LaunchTrace suits them — is in `outreach/prospects_seed.csv` and git,
because it is reusable and contains no personal data. **The live outreach
state** — verified addresses, named contacts, reply notes, milestone dates,
opt-outs and funnel position — is in the `prospect_state` and
`prospect_suppressions` tables, because it is personal data about real people
and does not belong in a commit history.

Prospects are scored into Priority A/B/C by an explainable keyword model in
`config/icp_scoring.json`. Every score comes back with its reasons, so you can
disagree with any of it and edit the weights.

Record a contact address you have checked on the company's own website:

```bash
python -m src.admin prospects set-contact --prospect-id P012 \
  --email sales@example.co.uk --source website_verified
```

Move a prospect along after you have acted:

```bash
python -m src.admin prospects set-status --prospect-id P012 --status EMAIL_1_SENT
```

Transitions are checked, so the funnel numbers mean what they say.

### A prospect-specific preview

```bash
python -m src.admin prospect-preview --prospect-id P012 --draft-email
```

Picks the strongest opportunities that genuinely suit **that** supplier — a
pouch converter sees snack and bar brands, a label printer sees sauces and jars
— then drafts Email 1 with them already inserted.

It returns fewer than three when fewer than three fit, and never pads. It never
shows the same applicant twice. If nothing fits, it says so and tells you not
to send.

### The full sample

```bash
python -m src.admin prepare-sample --prospect-id P012
```

Builds a branded HTML report and a clean CSV, writes the cover email with the
count filled in, and prints what to do next. Read the report as the customer
would before sending it.

### Opt-outs

```bash
python -m src.admin prospects opt-out --prospect-id P012 --reason "asked not to be contacted"
```

One command, immediately and permanently. It records the opt-out against email,
domain *and* company name, so no future import can bring them back under a
different spelling.

### Customers

```bash
python -m src.admin customer-status
python -m src.admin customer-lifecycle --customer-id 1 --event start
```

Once Stripe is connected, onboarding happens on its own when someone pays:
customer created, recipients recorded, delivery enabled, and one onboarding
email per recipient prepared — confirmation, what happens next and the first
Friday date, in a single message. `--event start` does the
same thing by hand for a customer you invoiced yourself.

`customer-status` shows the line that matters: **`Next Friday feed: yes`**.

### Feedback, metrics and cost

```bash
python -m src.admin feedback add --state USEFUL --trademark UK00003275632 --customer-id 1
python -m src.admin feedback summary
python -m src.admin business-status
python -m src.admin costs --customers 10
python -m src.admin backup
```

`business-status` is the whole picture: funnel, conversion, MRR, product volume,
feedback, operations, cost and margin. A rate on fewer than five observations is
labelled as not yet a rate.

Feedback is recorded as evidence and **never changes scoring automatically** —
that stays a deliberate decision with the reasoning written down.

### Where to read more

- [`FIRST_CUSTOMER_PLAYBOOK.md`](FIRST_CUSTOMER_PLAYBOOK.md) — zero to one
  paying customer, step by step
- [`COMMERCIAL_READINESS.md`](COMMERCIAL_READINESS.md) — what is ready, what
  needs a credential, what is still unproven
- [`outreach/README.md`](outreach/README.md) — the prospecting rules
- [`docs/BACKUP_AND_RECOVERY.md`](docs/BACKUP_AND_RECOVERY.md) — what is source
  of truth for each file

---

## Licensing and attribution

Trade mark data from the UK Intellectual Property Office and company data from
Companies House, both used under the Open Government Licence v3.0. LaunchTrace
is not affiliated with or endorsed by either body.

See [`docs/ATTRIBUTION.md`](docs/ATTRIBUTION.md) for the full position,
including the questions still open.

## Further reading

- [`FIRST_CUSTOMER_PLAYBOOK.md`](FIRST_CUSTOMER_PLAYBOOK.md) — the exact
  sequence from zero outreach to a first £79 customer
- [`COMMERCIAL_READINESS.md`](COMMERCIAL_READINESS.md) — what is commercially
  operational, the credential checklist in dependency order, and what is still
  not validated
- [`HANDOFF.md`](HANDOFF.md) — what is built, what is waiting on you, and the
  exact steps to finish it
- [`BUILD_REPORT.md`](BUILD_REPORT.md) — what works, what was tested, and how
- [`reports/validation/4_week_summary.md`](reports/validation/4_week_summary.md)
  — the January 2018 four-week historical sanity test
- [`docs/PRIVACY.md`](docs/PRIVACY.md), [`docs/TERMS.md`](docs/TERMS.md),
  [`docs/LEGITIMATE_INTERESTS_ASSESSMENT.md`](docs/LEGITIMATE_INTERESTS_ASSESSMENT.md),
  [`docs/DATA_RETENTION.md`](docs/DATA_RETENTION.md) — operational drafts for
  your review
- [`docs/COMPLIANCE_REVIEW.md`](docs/COMPLIANCE_REVIEW.md) — what was checked,
  and what still needs a solicitor
- [`docs/DOMAIN_AND_EMAIL_SETUP.md`](docs/DOMAIN_AND_EMAIL_SETUP.md) — buying a
  domain and setting up email, with no DNS knowledge assumed
- [`docs/WEBSITE_INTEGRATION.md`](docs/WEBSITE_INTEGRATION.md) — the contract
  the replacement website builds against
- [`docs/BACKUP_AND_RECOVERY.md`](docs/BACKUP_AND_RECOVERY.md) — what is source
  of truth, and how to get it back
