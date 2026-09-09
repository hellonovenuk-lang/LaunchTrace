# Commercial readiness

**What is ready, what is waiting on you, and what is still unproven.**

The product engine was built first. This pass built everything around it needed
to turn a signal into revenue: find a supplier, show them something relevant,
send a sample, take money, deliver every Friday, and learn whether it was
useful.

None of it required a credential, and none of it sent anything.

---

## 1. What is now operational

Every item works today, with no account of any kind.

### Finding and prioritising customers

| | |
| --- | --- |
| **Prospect tracker** | 60 researched UK suppliers. The research — who they are, what they supply, why LaunchTrace suits them — is in `outreach/prospects_seed.csv` and git. The live outreach state — verified addresses, named contacts, reply notes, milestone dates, reply state, opt-outs and funnel position — is in `prospect_state` and `prospect_suppressions` in the application database, because it is personal data and does not belong in a commit history. |
| **ICP scoring** | An explainable keyword model over the research text. Every score comes back with the reasons that produced it, and every weight is editable in `config/icp_scoring.json`. Output: Priority A / B / C / Suppress. Currently 11 A, 38 B, 10 C, 1 suppressed. |
| **Duplicate protection** | The same business cannot enter the list twice by name, domain, company number or contact email. This found and resolved a real duplicate in the existing 60. |
| **Suppression** | An opt-out is recorded against email, domain **and** company name, in a file that is copy-on-write and cannot be shortened by a save. A suppressed business cannot be re-imported under a different spelling. |

### Getting to a conversation

| | |
| --- | --- |
| **Prospect-specific previews** | Selects the strongest genuinely relevant opportunities for one supplier. A pouch converter gets snack and bar brands; a label printer gets sauces and jars; a co-packer gets brands that need filling. Returns fewer than three when fewer than three fit, and never pads. Never shows the same applicant twice. |
| **Outreach templates** | Three editable Markdown templates with explicit placeholders, so the lead block, supplier service and greeting are inserted automatically and editing the wording cannot break the substitution. |
| **Due-state logic** | `outreach-due` says who is ready for a first email, who replied, who is due the sample, who is due the single follow-up, who has subscribed and who must not be contacted — in the order to work through, with the command for each. |
| **The no-send guarantee** | There is no code path from the outreach module to any email provider, and a test enforces it. Drafting for an opted-out prospect raises an error rather than producing text. |

### Making the sale

| | |
| --- | --- |
| **Customer sample** | A branded HTML report and a clean CSV. Shows the score, the reasons behind it, the relevance bands and a link to the public source record. Excludes suppressed records, unmatched companies and every internal debugging field. Carries a SAMPLE label. |
| **Sample workflow** | `prepare-sample --prospect-id X` marks the request, builds the package, writes the cover email with the count filled in, and prints exactly what the operator does next. |

### After they pay

| | |
| --- | --- |
| **Onboarding** | A successful subscription creates the customer, records recipients and supplier preferences, enables delivery, includes them in the next Friday run, and prepares **one** onboarding email per recipient: confirmation, what happens next, and the first expected Friday delivery date. Idempotent — a redelivered webhook or a re-run command prepares nothing twice. |
| **Payment failure** | Marks past due, warns once per invoice, and keeps the feed running for a 14-day grace period before pausing it. It is never cancelled by us, and the same invoice is never chased twice. |
| **Cancellation** | Stops delivery immediately and confirms it. |
| **Delivery gating** | One function decides whether a customer receives the next feed, used by both the pipeline and the operator CLI — so what the operator sees is what actually happens. |
| **Preferences** | Supplier category and buying-intent categories are recorded from day one. The MVP still delivers the full food feed to everyone; the fields are there so tailoring later does not mean going back to every customer to ask. |

### Knowing whether it works

| | |
| --- | --- |
| **Feedback** | Seven states, recordable by CLI, CSV import, or a one-question form linked from each weekly email. Stored against the customer and the opportunity. **It never changes scoring automatically** — it is evidence for tuning it deliberately later. |
| **Metrics** | `business-status`: the full funnel, conversion rates, MRR, product volume, feedback, operational state, cost and margin. A rate on fewer than five observations is labelled as not yet a rate. |
| **Cost model** | Estimated cost per weekly run, per active customer, and gross margin at £79. Every figure is editable in `config/costs.json` and every unverified one is marked "assumed" on the line where it appears. |
| **Backup** | One command copies everything that cannot be reconstructed. `docs/BACKUP_AND_RECOVERY.md` says what is source of truth for each file. |

### For the replacement website

Business logic is now separate from the HTML. `src/web/services.py` holds every
action the site can take; `src/web/api.py` exposes them as JSON under `/api`;
the existing pages call the same functions, so the two cannot drift apart. The
whole front end can be replaced without touching billing, sample requests or
opt-outs. Contract: `docs/WEBSITE_INTEGRATION.md`.

### Quality

531 tests pass. `ruff check`, `ruff format --check` and `mypy src` are clean.
The smoke test passes end to end on fixture data with no network access.

All of that also passes **on GitHub**, not just locally: the complete `Tests`
workflow is green — install, lint, format check, type check, tests, smoke test,
the migration freshness check, and a second job applying the schema to a real
PostgreSQL 16 ([run 34396020152](https://github.com/hellonovenuk-lang/LaunchTrace/actions/runs/34396020152)).

---

## 2. What needs a credential

**In dependency order.** Each entry says what it unlocks, what happens without
it, and what it blocks. Nothing here blocks the first ten outreach emails.

### 1. Tavily — web enrichment · **blocks the real validation**

* **What it does:** finds each brand's website and checks how established it
  already is.
* **Free plan enough?** Yes. 1,000 searches/month; a weekly run uses under 150.
* **Credential:** an API key from <https://tavily.com/>.
* **Goes in:** `.env` as `SEARCH_PROVIDER=tavily` and `SEARCH_API_KEY=…`;
  GitHub secret `SEARCH_API_KEY`, variable `SEARCH_PROVIDER`.
* **Test:** `python -m src.pipeline check-config` shows `Web enrichment tavily`.
* **Without it:** the pipeline runs, but cannot tell an early-stage brand from
  an established one, so **no record can reach the HIGH band**. This is why the
  January 2018 historical sanity test shows 0 HIGH.
* **Blocks:** the real validation — a **current** week with enrichment
  connected. Connecting Tavily and re-running the 2018 weeks is not that test
  and must not be read as it. Do this before drawing any conclusion about the
  signal, and before sending a sample you want to be judged on.

### 2. Companies House API — optional · **blocks nothing**

* **What it does:** live company lookups instead of a monthly snapshot.
* **Free plan enough?** Yes, entirely free.
* **Credential:** an API key from
  <https://developer.company-information.service.gov.uk/>.
* **Goes in:** `.env` as `COMPANIES_HOUSE_API_KEY=…`.
* **Test:** `check-config` shows `Company registry companies_house_api`.
* **Without it:** run `python -m src.pipeline build-company-index --download`
  monthly. Free, no account, and this is what produced the January 2018
  historical figures.
* **Blocks:** nothing. Genuinely optional.

### 3. Supabase — hosted database · **blocks the scheduled run**

* **What it does:** somewhere for data to live that is not one laptop.
* **Free plan enough?** Yes, including daily backups.
* **Credential:** the connection string from Project Settings → Database →
  Connection string → URI, with the password substituted.
* **Goes in:** `.env` as `DATABASE_URL=…`; GitHub secret `DATABASE_URL`.
* **Test:** `python -m src.pipeline init-db`, then `check-config` shows
  `Database PostgreSQL`.
* **Without it:** a local SQLite file, which is genuinely fine for months.
* **Blocks:** the GitHub Actions Friday run, which has nowhere to persist
  without it. Not needed while you run weekly by hand.

### 4. Resend — email · **blocks delivery**

* **What it does:** sends the weekly feed and the transactional messages.
* **Free plan enough?** Yes. 3,000/month covers roughly 500 customers.
* **Credential:** an API key with sending access from <https://resend.com/>.
* **Goes in:** `.env` as `RESEND_API_KEY=…` and `EMAIL_FROM=…`.
* **Test:** `check-config` shows `Email Resend (live)`. Then send a run to
  yourself first.
* **Without it:** every message renders to `reports/outbox/` exactly as it
  would have been sent. Nothing is lost and nothing is sent by accident.
* **Blocks:** automated delivery to a paying customer. Requires the domain
  (below) first, for DNS verification.
* **Note:** needed before your *first customer's first Friday*, not before
  outreach. Send your outreach from your own mailbox regardless.

### 5. Stripe — subscriptions · **blocks self-service payment only**

* **What it does:** takes the £79/month.
* **Free plan enough?** Free to open. Start in Test mode. 1.5% + 20p per
  charge, only when you are paid.
* **Credentials:** secret key, webhook signing secret, and two price ids.
* **You must create by hand:** in Product catalogue,
  `LaunchTrace Food — Founding access` at **£79.00 GBP monthly recurring**, and
  `LaunchTrace Food — Standard` at **£129.00 GBP monthly recurring**. Copy each
  price id (starts `price_`).
* **Webhook:** Developers → Webhooks → Add endpoint,
  `https://your-domain/billing/webhook`, events
  `checkout.session.completed`, `customer.subscription.created`,
  `customer.subscription.updated`, `customer.subscription.deleted`,
  `invoice.paid`, `invoice.payment_failed`.
* **Goes in:** `.env` as `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
  `STRIPE_FOUNDING_PRICE_ID`, `STRIPE_STANDARD_PRICE_ID`.
* **Test:** `stripe listen --forward-to localhost:8000/billing/webhook`, then
  `stripe trigger checkout.session.completed`, then
  `python -m src.admin customer-status`.
* **Without it:** the checkout button explains subscriptions are not open yet
  rather than breaking, and you can invoice a first customer by hand — the
  onboarding runs identically via `customer-lifecycle --event start`.
* **Blocks:** self-service payment. Not your first customer.

### 6. A domain · **blocks Resend and the public site**

* **What it does:** an address for the site and a domain to send email from.
* **Cost:** roughly £10–15/year.
* **Goes in:** `SITE_URL` in `.env` and as a GitHub variable.
* **Blocks:** Resend domain verification, the Stripe webhook URL, and the
  public site. Required before automated delivery; not before outreach.
* **Full instructions, no DNS knowledge assumed:**
  `docs/DOMAIN_AND_EMAIL_SETUP.md`.

### 7. Hosting — Fly.io or Render · **blocks the public site**

* **What it does:** runs the website and the webhook endpoint.
* **Cost:** £0–7/month.
* **Ready:** a verified `Dockerfile` and `fly.toml`. `fly launch --no-deploy`
  then `fly deploy`.
* **Test:** `https://your-domain/healthz` returns `{"status":"ok"}`.
* **Blocks:** the sample form and Stripe checkout. A customer you invoice by
  hand needs none of it.

### 8. GitHub Secrets · **blocks the automated Friday run**

Once the above exist, add under Settings → Secrets and variables → Actions:

* **Secrets:** `DATABASE_URL`, `SEARCH_API_KEY`, `RESEND_API_KEY`, and
  `LLM_API_KEY` if you added one.
* **Variables:** `SEND_MODE=review`, `SITE_URL`, `ADMIN_EMAIL`,
  `SEARCH_PROVIDER=tavily`, `EMAIL_FROM`.
* **Blocks:** the scheduled run only. Run weekly by hand until then.

### Optional: an LLM

Improves classification accuracy. Pennies per week. Without it, rule-only
classification, which is more conservative. Blocks nothing.

---

## 3. What is still not validated

**Be honest about this section. It is the part that decides whether there is a
business here.**

### The January 2018 weeks are a sanity test, not the validation

Four real journal weeks — 5 to 26 January 2018 — produced **22 good
opportunities, 5.5 per week**, the QUESTIONABLE band. That number is real and
it is reported unchanged. What it is:

* an engineering and historical check that parsing, company matching, scoring
  and weekly volume behave correctly on real data.

What it is **not**: the 2026 commercial validation. It was produced

* with **no web enrichment**, so nothing could reach the HIGH band at all —
  the 0 HIGH is structural, not a discovery;
* with **no goods-and-services text**, because the Open Data release does not
  publish it. The live weekly journal does, so a real Friday run has strictly
  more evidence than these four weeks had;
* with **rule-only classification**.

**Re-running those four weeks with Tavily connected does not fix this and must
not be treated as the measurement.** Current web enrichment applied to a brand
published in January 2018 recovers what that brand became over the following
eight years, not what was knowable about it in the week it was published — so
it flatters the brands that later succeeded and says nothing about the ones
that were genuinely early at the time.

**The decisive product test is a current one:** current UKIPO weekly journal +
current goods/services text + current Companies House evidence + current
Tavily/web enrichment + current classification — one live
`python -m src.pipeline weekly`, scored on what is knowable that week. Until
that has run, **the volume figure is not known**.

### Nothing has been tested on a real supplier

No email has been sent. No supplier has said whether five brands a week is
worth £79. Every conversion rate in `business-status` is currently a
placeholder with a denominator of zero or one.

**The next test is not technical.** It is ten emails to ten suppliers.

### What a first customer would prove, and what it would not

One customer proves someone will pay once. **Renewal at month two** is the
first real evidence. Three renewing customers is a business.

### Deliberately unproven

* Live UKIPO retrieval has never run — this environment is blocked by IPO's
  bot protection. HANDOFF.md §3.1 has the fallback, which takes two minutes.
* The Stripe webhook has never seen a real Stripe event; it is tested against
  fixtures.
* No email has been sent through Resend.
* The ICP scores are a prioritisation aid derived from text a human wrote.
  They have never been checked against whether anyone actually replies.

---

## 4. What not to build yet

Recorded so the temptation is named rather than acted on. None of these is
worth building before there is a paying customer who renews.

* **A customer dashboard.** The product is an email and a CSV. A dashboard is
  months of work to replace something that already arrives in an inbox.
* **Automated outreach sending.** The manual step is a feature. It is also a
  significant compliance decision, not a convenience one.
* **Contact-name enrichment for the brands in the feed.** The single most
  requested thing, and the one that materially changes the privacy position —
  the legitimate interests assessment would need redoing first.
* **More sectors.** Cosmetics, pet and household are configuration, not code.
  Adding them before food has a customer multiplies the unknowns.
* **A CRM.** A CSV and a CLI is enough for 60 prospects and will be enough for
  600.
* **Scoring changes driven by feedback.** Collect it first. Change weights
  deliberately, with the reasoning written down, once there is enough to see a
  pattern.
* **Retention automation, replication, disaster recovery.** A monthly backup
  command and `git push` genuinely covers it at this size.

---

## 5. Launch sequence

The order that gets to revenue fastest, with the cheapest steps first.

**This week — free, no accounts**

1. Connect Tavily (5 minutes, free).
2. **Run a live week: `python -m src.pipeline weekly`.** Current journal,
   current goods/services text, current Companies House evidence, current
   enrichment, current classification. **This is the measurement. It tells you
   what the product is actually worth.**
3. Read that week's numbers. Do not compare them against 5.5/week as if the two
   were the same measurement — the January 2018 figure is a sanity test of the
   machinery, and re-running those weeks with enrichment on would not make it
   comparable.
4. Read `reports/samples/` output as a customer would.

**Next week — one hour of research, then ten emails**

5. `python -m src.admin prospects list --priority A` — take ten.
6. Find a real contact address for each. Never guess one.
7. Generate previews, read three of them properly, send the ten yourself.
8. Record every reply the day it arrives.

**Stop here until someone replies.** Everything below is for after that.

**When someone asks for a sample**

9. `python -m src.admin prepare-sample --prospect-id X`, read the report, send it.

**When someone says yes**

10. Invoice by hand and use `customer-lifecycle --event start`, *or* set up
    Stripe first if you would rather. Do not make them wait.

**Before their first Friday**

11. Domain → Resend → SPF, DKIM, DMARC (`docs/DOMAIN_AND_EMAIL_SETUP.md`).
12. Supabase, so the scheduled run has somewhere to persist.
13. Deploy the site; add the Stripe webhook.
14. GitHub secrets; turn on the Friday workflow.

**Before you take real money**

15. Fill in every `[BRACKET]` in `docs/PRIVACY.md` and `docs/TERMS.md`.
16. Have a solicitor read them. They are drafts and they say so.
17. Register with the ICO (£40–60/year).
18. Switch Stripe out of Test mode.

---

## 6. Where to look

| Question | Document |
| --- | --- |
| How do I get my first customer? | `FIRST_CUSTOMER_PLAYBOOK.md` |
| What do I need to set up, exactly? | `HANDOFF.md` §4 |
| How do I buy a domain and set up email? | `docs/DOMAIN_AND_EMAIL_SETUP.md` |
| How does the new website plug in? | `docs/WEBSITE_INTEGRATION.md` |
| What happens if I lose the laptop? | `docs/BACKUP_AND_RECOVERY.md` |
| What was built and why? | `BUILD_REPORT.md` |
| What does each command do? | `python -m src.admin --help` |
| Does the machinery work on real data? | `reports/validation/4_week_summary.md` (January 2018) |
| Is the signal real? | Not yet answered. Connect Tavily, run `python -m src.pipeline weekly`, read that week. |
