# First customer playbook

**How to get from zero outreach to one paying customer at £79/month.**

Written for a non-technical owner. Every command can be copied and pasted. No
step requires you to understand the code.

**It is not a sales manual.** It is the specific sequence for this product,
with the points where you should stop and think marked as such.

---

## Before you start

Two things must be true. Neither takes long, and skipping either wastes the
outreach.

### 1. You have run a real week

The examples in your first email must be brands from a recent journal, not from
the January 2018 historical sanity test. A supplier who checks a date and finds
2018 will stop reading, correctly. Those four weeks show the machinery works;
they cannot show what the product is worth now, because current web enrichment
applied to a 2018 brand describes what it became, not what was knowable then.
The number that matters comes from a current run: current journal, current
goods/services text, current Companies House evidence, current Tavily
enrichment, current classification.

```bash
python -m src.pipeline weekly
```

If that fails, HANDOFF.md §3.1 explains why and what to do instead.

**Connect Tavily first if you have not.** Without web enrichment nothing can
reach the HIGH band at all, so your sample is weaker than the product actually
is. It is free and takes five minutes — HANDOFF.md §2.1.

### 2. You can be replied to

You need an email address you actually read, and a phone number in your
signature. That is all. You do not need the website, Stripe, or a domain to
send the first ten emails — you need those before someone can *pay* you, not
before they can say yes.

☐ A real week has run
☐ You can receive replies

---

## The sequence

### 1. Pick your first ten

```bash
python -m src.admin prospects list --priority A
```

Eleven companies come back, best fit first. Take ten.

These are scored by how likely they are to value the feed, from the research
already in the list. See exactly why any one of them scored as it did:

```bash
python -m src.admin prospects show --prospect-id P012
```

If a reason looks wrong, it probably is — the score reads text a human wrote.
Edit the `icp_reason` in `outreach/prospects_seed.csv` and re-run
`python -m src.admin prospects rescore`.

### 2. Find a real contact address for each

**This is the only genuinely tedious step, and nothing can do it for you.**
About an hour for ten.

For each company: open their website, find the real `sales@` or `enquiries@`
address, and record it. It goes into the database, not into git — contact
details are personal data and do not belong in a commit history.

```bash
python -m src.admin prospects set-contact --prospect-id P012 \
  --email sales@example.co.uk --source website_verified
```

**Never guess an address.** A pattern like `firstname@company.co.uk` that
happens to be wrong costs you the prospect and damages your sending
reputation. A blank is fine; a wrong one is not. Nothing in this system will
ever generate one for you.

If there is only a contact form, that is fine — leave the email blank and use
the form. Note it in `notes`.

Then mark each one ready:

```bash
python -m src.admin prospects set-status --prospect-id P012 --status READY
```

### 3. Generate a preview for each

```bash
python -m src.admin prospect-preview --prospect-id P012 --draft-email
```

This picks the three strongest opportunities **for that specific supplier** — a
pouch converter sees snack and bar brands, a label printer sees sauces and
jars — and writes a draft email with them already in it.

It may return fewer than three. That is deliberate and correct: three examples
where one does not fit is worse than two that do.

If it returns none, do not send. Wait for a better week.

### 4. Read the preview before you send anything

**Do not skip this.** For at least the first three prospects:

* open a source link and check the brand is what we say it is;
* ask yourself honestly whether this supplier would care about these three
  companies.

If the answer is no, the problem is either the prospect (wrong fit — change
their status) or the product (the feed is not surfacing the right thing). Both
are worth knowing before you send ten emails, and neither is fixed by sending
them.

### 5. Send it — yourself, from your own mailbox

The draft is at `reports/outreach_drafts/P012_email_1.md`. Fill in `[PHONE]`
and `[WEBSITE]`, copy it into your email client, and send.

**Nothing in LaunchTrace sends it for you.** That is deliberate: cold email
should be a decision a person makes each time.

Then record it:

```bash
python -m src.admin prospects set-status --prospect-id P012 --status EMAIL_1_SENT
```

Send all ten across two or three days rather than in one batch, so you can
adjust the wording if the first few land badly.

### 6. Record every reply the day it arrives

```bash
# They want to see more
python -m src.admin prospects set-status --prospect-id P012 --status REPLIED_INTERESTED

# Not right now
python -m src.admin prospects set-status --prospect-id P012 --status NOT_NOW \
  --note "what they actually said, in their words"

# Stop contacting them
python -m src.admin prospects opt-out --prospect-id P012 --reason "asked not to be contacted"
```

**Record the exact words** in `--note`. "The brands are too early for us" and
"we already know about these" mean completely different things and point at
different fixes. This is the most valuable output of the whole exercise.

An opt-out is acted on immediately and permanently — it adds them to the
suppression list by email, domain and company name, and they can never come
back through a future import.

### 7. Send the full sample

```bash
python -m src.admin prepare-sample --prospect-id P012
```

Produces:

* a clean CSV to attach;
* an HTML report you can open and read as they would;
* a cover email.

**Open the HTML report and read it.** It is the thing that decides whether they
pay. If it does not look worth £79 to you, it will not to them either — and
that is information, not a reason to send it anyway.

Send it, attach the CSV, and record it:

```bash
python -m src.admin prospects set-status --prospect-id P012 --status SAMPLE_SENT
```

### 8. Follow up once, after about a week

```bash
python -m src.admin outreach-due
```

Tells you who is due what, in the order to work through. When someone is due a
follow-up:

```bash
python -m src.admin outreach-draft --prospect-id P012 --template email_3
```

It asks whether the sample was useful and offers the subscription at £79.

**One follow-up. Then stop.** A fourth email costs you the relationship and
gains nothing. Mark them `NO_RESPONSE` and move on.

### 9. When someone says yes

Record the offer, then send them the link:

```bash
python -m src.admin prospects set-status --prospect-id P012 --status OFFER_SENT
```

*If Stripe is connected:* send them
`https://your-domain/billing/checkout?plan=founding_monthly`. When they pay,
everything in step 10 happens on its own.

*If Stripe is not connected yet:* do not make them wait. Invoice them by hand
however you normally would, then set them up manually:

```bash
python -m src.pipeline add-customer --company "Their Company" \
  --email their@address.co.uk --supplier-type labels --status active

python -m src.admin customer-lifecycle --customer-id 1 --event start
```

That records the customer, enables delivery, and prepares their onboarding
email — one message carrying the confirmation, what happens next and the first
Friday date. Connect Stripe properly afterwards —
a paying customer is a much better reason to open the account than a hypothesis.

Then:

```bash
python -m src.admin prospects set-status --prospect-id P012 --status SUBSCRIBED
```

### 10. Onboarding — check it, do not do it

When payment succeeds, this happens automatically:

* the customer record is created and the recipient address stored;
* subscription status becomes active and delivery is enabled;
* one onboarding email per recipient is prepared — confirmation, what happens
  next, and the first Friday date;
* they are included in the next Friday run.

Confirm it:

```bash
python -m src.admin customer-status
```

`Next Friday feed: yes` is the line that matters. If Resend is not connected,
the three emails are in `reports/outbox/` — send them by hand.

### 11. Their first Friday

```bash
python -m src.pipeline weekly
python -m src.pipeline status          # note the run id
python -m src.pipeline approve --run-id <run-id>
python -m src.pipeline send --run-id <run-id>
```

Nothing sends without that approval, by design. Keep it that way for at least
four weeks — read the QA report each time before approving.

**Email them separately the first Friday.** One line: "first one's just gone
out, tell me if anything's off." It costs nothing and it is the difference
between a subscriber and a customer.

### 12. Ask what was useful, and record it

After two or three weeks, ask which leads were worth anything. Then:

```bash
python -m src.admin feedback add --state USEFUL \
  --trademark UK00003275632 --customer-id 1 --note "called them, sending samples"

python -m src.admin feedback summary
```

States: `USEFUL`, `CONTACTED`, `CONVERTED`, `NOT_RELEVANT`, `ALREADY_KNOWN`,
`TOO_ESTABLISHED`, `TOO_EARLY`.

This does not change scoring, deliberately. It builds the evidence to change it
later, on purpose, rather than reacting to one person's opinion.

### 13. Check where you actually are

```bash
python -m src.admin business-status
```

The whole funnel, conversion rates, MRR, cost and margin in one screen. A rate
built on fewer than five observations is labelled as such, because it is not a
rate yet.

---

## Stop and think

Set these thresholds now, while you have no emotional investment in the answer.

### After 30 qualified prospects with no sample requests

**Stop sending.** More of the same will not work.

The proposition or the data is wrong. Ask three of the people who ignored you
what would have made them reply — a direct question after a non-reply gets an
answer surprisingly often. Consider whether the examples were genuinely
relevant (re-read three previews) or whether "brands that just filed a trade
mark" is simply not a moment your buyer cares about.

### Samples requested but nobody pays

The signal is interesting but not yet worth £79. Ask each of them directly what
it would take. Common answers and what each means:

* *"We need contact details for the brands"* — a significant product and
  compliance decision, not a small feature. See HANDOFF.md §6.
* *"Five a week is not enough"* — a volume problem. Web enrichment and the live
  journal both raise the number; check what a real week gives before concluding.
* *"They are too early for us"* — you may be selling to the wrong supplier
  type. Distributors and 3PLs want later-stage brands than packaging converters.

### One paying customer

**Do not scale yet.** Get them to a second month. Renewal is the only evidence
that matters, and everything you learn in that month changes who you approach
next.

### Three paying customers who renew

Now it is a business. Work through the rest of the Priority A and B list, and
revisit HANDOFF.md §6 for what to build next.

---

## When to give up on this approach

If, after 30 well-qualified prospects and honest previews, nobody wants to see
a sample — the answer is not more emails or better wording. Something in the
premise is wrong, and finding out which part is worth more than another fifty
sends.

That is a real possible outcome and it is worth naming in advance, because in
the moment it will feel like a reason to try harder.

---

## Daily routine, once running

```bash
python -m src.admin outreach-due
```

One command. It tells you who is due what, in the order to do it, and prints
the command for each. Ten minutes a day.

Once a week:

```bash
python -m src.admin business-status
python -m src.admin backup
```
