# Website integration contract

**For whoever builds the replacement LaunchTrace website.**

The current site is deliberately plain and is going to be redesigned. This
document is the contract the new site builds against, so the redesign can throw
away every template without touching billing, sample requests or opt-outs.

The rule: **the front end owns presentation, the backend owns behaviour.** If
the new site needs to make a decision about validation, suppression, rate
limits or subscription state, it is calling the wrong endpoint.

---

## 1. Where the logic lives

| Layer | File | What it is |
| --- | --- | --- |
| Business logic | `src/web/services.py` | Every action the site can take, as plain functions. No HTML, no HTTP. |
| JSON API | `src/web/api.py` | Thin wrappers over those functions, under `/api`. **This is the contract.** |
| Current HTML pages | `src/web/app.py` | Server-rendered pages. Calls the same functions. Replaceable. |

Because both front doors call the same functions, they cannot drift apart.
`tests/test_web_api.py::TestHtmlAndApiAgree` enforces this.

**If you are replacing the site:** use the `/api` endpoints below and delete
`src/web/templates/` when you are ready. Leave `src/web/services.py` and
`src/web/api.py` alone.

---

## 2. Response shape

Every `/api` endpoint returns the same envelope:

```json
{
  "ok": true,
  "code": "accepted",
  "message": "Thanks — we'll send the most recent sample to that address shortly.",
  "data": { }
}
```

| Field | Meaning |
| --- | --- |
| `ok` | Whether the action succeeded. |
| `code` | **Branch on this.** A stable identifier that will not change wording. |
| `message` | A sentence written for a person. You may replace it with your own copy. |
| `data` | Present only when there is something to return. |

Do not branch on `message` and do not invent your own `code` values.

HTTP status codes: `200` success, `400` invalid input, `404` unknown plan,
`429` rate limited. A `429` is not an error to retry immediately.

---

## 3. Endpoints

### `GET /api/health`

Liveness. Returns `{"ok": true, "service": "launchtrace"}`. Use for uptime
monitoring. Does not touch the database.

### `GET /api/config`

Everything the front end needs to render itself correctly. Call it once on load.

```json
{
  "ok": true,
  "data": {
    "site_url": "https://launchtrace.co.uk",
    "plans": [
      {
        "key": "founding_monthly",
        "name": "LaunchTrace Food — Founding access",
        "price_pence": 7900,
        "price_display": "£79",
        "interval": "month",
        "max_recipients": 3,
        "features": ["Full weekly UK LaunchTrace Food feed", "..."],
        "offered_publicly": true
      }
    ],
    "supplier_types": [{"key": "flexible_packaging", "label": "Flexible packaging manufacturer"}],
    "checkout_available": false,
    "sample_requests_open": true,
    "attribution": "UK Intellectual Property Office and Companies House data, Open Government Licence v3.0",
    "legal": {"privacy": "/privacy", "terms": "/terms", "attribution": "/attribution"}
  }
}
```

Two fields decide what you render:

* **`checkout_available`** — `false` means Stripe is not connected. Hide the
  subscribe button or point it at the sample form instead. Do not render a
  checkout button that leads nowhere.
* **`plans[].offered_publicly`** — only show plans where this is `true`. The
  standard plan exists but is not on the public page yet.

Prices come from here, never hard-coded in the front end. A Stripe price id is
never exposed.

### `POST /api/sample-request`

The main conversion action on the site.

```json
{
  "work_email": "sales@example.co.uk",
  "company": "Example Packaging Ltd",
  "contact_name": "",
  "supplier_type": "flexible_packaging",
  "website_url": ""
}
```

`work_email` and `company` are required; the rest are optional.

**`website_url` is a honeypot.** Render it as a real input that is hidden from
people (off-screen, not `type="hidden"`) and leave it empty. A submission with
anything in it is treated as automated: the response looks successful and
nothing is stored.

| `code` | HTTP | Meaning |
| --- | --- | --- |
| `accepted` | 200 | Recorded. Show a thank-you. |
| `already_requested` | 200 | Same address already asked. **Not an error** — show a friendly note. |
| `invalid_email` | 400 | Show `message` next to the field. |
| `missing_company` | 400 | Show `message` next to the field. |
| `rate_limited` | 429 | Five per hour per IP. Ask them to try later. |

Free-mail addresses are accepted but flagged internally for review. The visitor
sees no difference; do not build a client-side blocklist.

### `POST /api/checkout?plan=founding_monthly&email=optional@example.co.uk`

Starts a Stripe subscription.

```json
{"ok": true, "code": "checkout_ready", "data": {"url": "https://checkout.stripe.com/...", "live": true, "plan": "founding_monthly"}}
```

Redirect the browser to `data.url`.

| `code` | HTTP | Meaning |
| --- | --- | --- |
| `checkout_ready` | 200 | Stripe is connected. Redirect. |
| `checkout_stub` | 200 | **Stripe is not connected.** `data.live` is `false` and the URL is a local placeholder. Check `config.checkout_available` first and do not send a customer here. |
| `unknown_plan` | 404 | Bad `plan` value. |

### `POST /api/opt-out`

```json
{"email": "stop@example.co.uk"}
```

Always returns `{"ok": true, "code": "opt_out_recorded"}` — **identical for an
address that was never on any list**, so the endpoint cannot be used to test
whether an address is known to us.

This must be reachable without logging in, from the link in every email. It
suppresses the address and removes it from any delivery list.

### `POST /api/feedback`

Recorded against a delivered opportunity. Linked from each weekly feed.

```json
{"state": "USEFUL", "trademark_number": "UK00003275632", "email": "sales@example.co.uk", "note": "Called them"}
```

`state` must be one of: `USEFUL`, `NOT_RELEVANT`, `ALREADY_KNOWN`,
`TOO_ESTABLISHED`, `TOO_EARLY`, `CONTACTED`, `CONVERTED`. An unknown value
returns `400` with `data.allowed` listing the valid ones — read that list
rather than hard-coding it.

`email` identifies the customer. There is no login, and adding one to collect
four words of feedback would cost more than the feedback is worth.

Rate limited to 30 per hour per IP.

---

## 4. Pages the new site must keep

These are linked from emails already in people's inboxes. Breaking them breaks
promises made to customers.

| Path | Must do |
| --- | --- |
| `/privacy` | Render `docs/PRIVACY.md` |
| `/terms` | Render `docs/TERMS.md` |
| `/attribution` | Render `docs/ATTRIBUTION.md` — the OGL v3.0 attribution is a licence condition, not a nicety |
| `/unsubscribe` | Opt-out form. Must work with no session and no login |
| `/feedback` | Feedback form, accepting `?tm=` and `?email=` |
| `/billing/success` | Where Stripe returns a successful checkout |
| `/billing/cancelled` | Where Stripe returns an abandoned checkout |
| `/billing/manage` | Linked from every customer email |
| `/healthz` | `{"status":"ok"}` for the host's health check |

### The Stripe webhook — do not touch

`POST /billing/webhook` verifies a signature over the **raw request body**. If
your framework parses or re-serialises the body before it reaches this handler,
signature verification fails and subscriptions silently stop being recorded.

If you replace the HTTP layer, keep this endpoint on the existing FastAPI app
or reproduce it exactly, including the raw-body handling in `src/web/app.py`.

---

## 5. The Stripe flow, end to end

```
Visitor clicks Subscribe
  → POST /api/checkout            (front end)
  → redirect to data.url          (browser)
  → customer pays                 (Stripe)
  → POST /billing/webhook         (Stripe → LaunchTrace)
      checkout.session.completed
        → customer record created or matched
        → recipient email recorded
        → subscription_status = active, delivery enabled
        → one onboarding email prepared per recipient
  → redirect to /billing/success  (browser)
```

The success page is **not** where the subscription is recorded. Everything that
matters happens in the webhook, because a customer can close the tab.

Webhook events handled: `checkout.session.completed`,
`customer.subscription.created`, `customer.subscription.updated`,
`customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`.
Every event id is recorded, so a redelivery is a no-op.

**Optional metadata.** If your checkout sets `metadata.prospect_id` and
`metadata.supplier_type` on the session, they are stored on the customer, and
`python -m src.admin business-status` can then reconcile the funnel end to end.

---

## 6. Environment variables the site reads

| Variable | Needed for | Without it |
| --- | --- | --- |
| `DATABASE_URL` | Everything | Falls back to local SQLite |
| `SITE_URL` | Absolute links in emails | Links point at `localhost` |
| `STRIPE_SECRET_KEY` | Live checkout | `checkout_available` is `false`; stub URL returned |
| `STRIPE_WEBHOOK_SECRET` | Verifying webhooks | Webhook rejects everything (correctly) |
| `STRIPE_FOUNDING_PRICE_ID` | The £79 plan | Checkout cannot be created |
| `RESEND_API_KEY` | Sending email | Every message renders to `reports/outbox/` |
| `EMAIL_FROM` | The From address | Uses the default in settings |
| `ADMIN_TOKEN` | `/admin` | The operator view returns 503, which is the safe default |

Full list with descriptions: `.env.example`.

---

## 7. Deployment assumptions

* One Python process serves the site; the pipeline runs separately, on a
  schedule. They share a database.
* `Dockerfile` builds and runs it; `fly.toml` is configured for Fly.io. Any
  host that can run a container works.
* Health check: `GET /healthz`.
* The site is **stateless apart from the database**. Rate limiting is in
  process memory, so behind several instances the limit is per instance — fine
  at this scale, and worth revisiting before it is not.
* Static assets are served from `src/web/static/`. A replacement front end can
  serve its own however it likes.
* Security headers and a content security policy are set in middleware in
  `src/web/app.py`. **If you replace the HTTP layer, carry them over.** The CSP
  currently allows form submission to `checkout.stripe.com`; a new front end
  that loads fonts, scripts or images from anywhere else must widen it
  deliberately rather than removing it.

---

## 8. What the front end must never do

* Decide whether an email address is valid — call the API.
* Keep its own copy of the suppression list.
* Hard-code a price. Read `/api/config`.
* Skip the honeypot field.
* Send any email itself.
* Show a checkout button when `checkout_available` is `false`.
* Claim the feed predicts purchasing. The wording in
  `docs/TERMS.md` §3 is deliberate and is the wording to reuse.

---

## 9. Testing your integration

With no credentials at all:

```bash
python -m src.pipeline init-db
uvicorn src.web.app:app --reload
```

Then:

```bash
curl localhost:8000/api/config
curl -X POST localhost:8000/api/sample-request \
  -H 'content-type: application/json' \
  -d '{"work_email":"you@yourcompany.co.uk","company":"Your Company"}'
curl -X POST localhost:8000/api/checkout?plan=founding_monthly
```

Everything works without Stripe, Resend or a hosted database. Checkout returns
a stub URL, email renders to `reports/outbox/`, and the database is a local
file. Build the whole front end before opening a single account.

`tests/test_web_api.py` is the executable version of this document.
