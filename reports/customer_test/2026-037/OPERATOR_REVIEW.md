# Customer test — first five prospects, journal 2026-037

Prepared 11 September 2026. **Nothing has been sent.** No email, no website form,
no Resend call, no Stripe object, no deployment. Five complete outreach packs,
waiting on approval.

Source of every lead: `reports/validation/final/sample/2026-037/opportunities.csv`,
the customer-facing sample from `reports/validation/FINAL_QUALIFICATION_AUDIT.md` —
14 companies, 7 EXCELLENT, 7 GOOD, 0 WEAK, 0 WRONG, 100% measured useful precision
for that week.

## The five, and what each is testing

| Prospect | Category | Use case under test | Leads in Email 1 | Sample rows |
| --- | --- | --- | ---: | ---: |
| P009 Digimock | Flexible packaging | Short-run digital print for newer brands | 3 | 9 |
| P001 National Flexible | Flexible packaging | Reel film for flow-wrap and FFS lines | 3 | 9 |
| P022 Wholebake | Contract manufacturing | Bars, bites and snacks | 2 | 4 |
| P039 Sauce Master | Contract manufacturing | Sauces, dressings and condiments | 3 | 5 |
| P038 Cotswold Fayre | Distribution | Speciality wholesale and new-brand sourcing | 3 | 14 |

Two prospects share a supplier category on purpose and two share a profile in the
matching config, so the interesting comparison is not only "which category
replies" but "which end of a category replies" — short-run versus reel film,
narrow manufacturer versus broad distributor.

## How the leads were chosen

Two stages, and the second one is where the judgement is.

1. `select_for_prospect` — the existing matching logic — qualified and fit-ranked
   the week's 14 opportunities against that supplier's profile. It qualified 9 of
   14 for the two flexible-packaging prospects (the five jarred and bottled sauce
   brands are not a film opportunity) and 14 of 14 for the other three.
2. Within that qualified set, the leads for Email 1 were chosen by hand, per
   supplier, with a written reason each. Nothing outside the qualified set can
   reach a pack: the build asserts it and fails if a chosen trade mark is not in
   the profile's own shortlist.

The second stage exists because the fit score cannot tell two flexible-packaging
suppliers apart. Digimock and National Flexible score the same 9 leads
identically and sell different things, so taking the top three for both would
have sent National Flexible a tea brand's pouch order and Digimock a frozen
fries reel. The fit scores are recorded for every lead in
`selection_record.json`, so the hand-picking can be checked against them.

Wholebake got two leads, not three. That is the rule working, not a shortfall:
nothing else in the week is a bar, ball or bite.

## Framing used throughout

Every pack says the same thing in the same way: LaunchTrace has identified a
commercial signal suggesting this business may soon need this supplier category.
No email claims a brand is buying, is looking for a supplier, or has been
contacted. Each Email 1 says so in a sentence.

No pack shows a LaunchTrace Score, a confidence figure, a Nice class number, a
fit score, an exclusion count or a QA note. Those are in this report and in
`selection_record.json`, which are internal.

## Pre-send checks — read before approving

1. **Sauce Master's Companies House status.** SAUCE MASTER LTD (NI650631) reads
   **"Active — Active proposal to strike off"**. A first Gazette notice for
   compulsory strike-off was published on 30 December 2025 and the action was
   suspended on 20 January 2026, but no accounts have been filed since September
   2024 and no confirmation statement since February 2025. The company is still
   Active, the website is live, and the business proposition in the seed is
   unchanged — so it has **not** been replaced, because "behind on filings" is
   not "no longer trading". Re-check the register the day you send. If it has
   moved to strike-off or dissolution, the closest replacement on the seed list
   is **P040 Cotswold Gold**, white-label dressings, oils, rubs and condiments
   made to retail-ready standard, with **P045 Eastern Country Foods** (private
   label sauces and condiments) behind it.
2. **Wholebake's address is inconsistent on their own page.** The visible text
   reads one address and the link behind it points at another, on a different
   top-level domain; the two published phone numbers differ the same way. The
   link target is what was recorded. Confirm by phone before sending, or use the
   enquiry form on their get-in-touch page instead.
3. **`SITE_URL` must be live.** The reply-stage sample HTML was rendered with
   `SITE_URL=https://launchtrace.co.uk`, the value the deployment docs use. The
   opt-out link in the footer has to actually resolve before a sample goes to
   anyone — an opt-out link that 404s is worse than none.
4. **Fill in `[PHONE]` and `[WEBSITE]`** in every signature block, and check the
   sign-off name reads the way you sign email. Those are the only placeholders
   left in any draft.
5. **Open one source link.** Each sample row links to the public IPO case record
   for that trade mark. Click one and confirm it resolves from your network
   before sending (see the finding below).
6. **Check the suppression list on the day.** `python -m src.admin outreach-due`
   drops anyone blocked even if their row still says READY.

## Findings from preparing this

**The delivered sample linked to a local file, not a public record.** Every row
in `reports/validation/final/sample/2026-037/` — the CSV and the weekly email
alike — carries `source_url = file:///home/user/LaunchTrace/data/journals/2026-037.xml.gz`,
because that week's journal was read from a local file. In the customer-facing
HTML this renders as "Trade mark UK0000…" hyperlinked to a path on the machine
that ran the pipeline. A prospect clicking it gets nothing, and it discloses a
local path. That is a factual error in a customer-facing deliverable, so it is
reported here as instructed.

It was **not** fixed in the engine. The packs in this directory were built with
the source pointed at `IPO_CASE_URL` — the public case-record pattern the
repository already defines in `src/parse/open_data.py` and already uses on the
Open Data ingest path — so these five packs are sendable as they stand. The
underlying run record is untouched. Whether the journal-XML path should carry the
same public URL is a separate decision for a future engineering session, not part
of this customer test.

**Packfill and Digimock are one group.** `packfill.co.uk` and `digimock.com` each
describe PACKFILL as Digimock's own SALSA-accredited co-packing facility. Seed
row P051 has been marked as a duplicate of P009 and set to `SUPPRESSED` in the
database, so the same group cannot be contacted twice. This mirrors how P011 was
consolidated into P012.

**Two presentation notes, neither an error.** CHAI MAMA's registered mark carries
Arabic script (چاى مامه CHAI MAMA); it is written as CHAI MAMA in Digimock's
email so it renders in a mail client, and the preview says so. GOLDEN ROOTS and
TOSS each filed two marks the same week, consolidated by the pipeline into one
company each, which is correct.

**No pipeline preference was acted on.** Nothing in the qualification engine,
scoring, entity matching, commercial-mode logic, taxonomy, enrichment,
thresholds or pipeline architecture was changed, and no lead was excluded from a
pack for being a lead I would rather not send. Leads were excluded from an
individual pack only for not suiting that supplier, with the reason written down.

## What changed on disk

| Path | What |
| --- | --- |
| `reports/customer_test/2026-037/` | The five packs, this report, and the selection record. New. |
| `outreach/prospects_seed.csv` | Six research rows updated: company numbers, corrected website and geography, re-verification notes for the five, and the P051 duplicate exclusion. No contact address — the file has nowhere to put one. |
| Application database | Verified contact addresses and sources, re-verification notes, `READY` status for the five, `SUPPRESSED` for P051, and rescored priorities. Not in git, on purpose. |

No application code was changed. The test suite passes (647 tests).

---

## P009 — Digimock (Digimock Limited, 10648678, Lymm, Cheshire)

**Use case under test** — Short-run flexible packaging for newer brands

### Why we chose them

The clearest test of the "pre-launch brand, first pack" proposition. Digimock
sells mockups, short digital runs and pre-made pouches with low minimums, and its
own site says every brand deserves professional packaging "no matter their volume
requirements". If LaunchTrace is worth anything to a packaging supplier, it is
worth it to the one whose economics work at one SKU. Highest ICP score on the
list (75).

### Verified contact route

A generic sales mailbox published on their own site, verified 11 September 2026
and recorded in the database (`email_source = website_verified`). Their site also
carries a phone number and an enquiry form as fallbacks.

**Named contact** — None. Nothing on their own site names a commercial contact, and the standing rule is not to go looking.

### Email 1

**Subject:** Three new brands that look like short-run pouch work

```
Hi there,

I track newly published UK trade mark filings to pick up food and drink brands
while they're still early — usually before they've settled on packaging.

Three from this week looked like short-run pouch and wrapper work rather than
long reel orders, which is why I thought they were worth sending you rather than
the rest of the list.

kWh Coffee — Morning People Ltd (16660094), York. A speciality roastery: whole
bean, ground, coffee bags and drip bag. Incorporated a year before filing, first
mark we've seen from them, no multiple-retailer listings found. kwhcoffee.com.
A roastery runs several small-batch SKUs at a time, which is digital short-run
work rather than one reel.

BULLITT — Coldharbour Foods Limited (17035779), Buckinghamshire. The filing
covers one product line: "Energy Bar." Company six months old when it filed.
One SKU, one wrapper still to specify.

CHAI MAMA — Frond Foods Ltd (16798042), Liverpool. Black tea, chai, loose leaf
and tea bags. Company incorporated October 2025. A first tea range in pre-made
pouches rarely justifies a printed reel.

All three published in this week's journal. To be clear, these are signals that
a brand exists and looks early — not that anyone has said they're buying. None
of them has been contacted.

I've got the full set for this week: 14 UK food companies, 9 of them in flexible
packaging. Happy to send it over if it's useful.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### The leads chosen, and why each one matches

**kWh Coffee** — UK00004435554 (fit 18.58)

A roastery's pack range is several small-batch SKUs at once — origins, blends, drip bags. That is repeat digital short-run work, not one long reel. It is also the only lead in the week with a website verified against the company, so the first example a stranger clicks holds up.

**BULLITT** — UK00004437089 (fit 14.96)

The filing is one line — "Energy Bar." — from a company six months old. One SKU with one wrapper still to specify is the exact volume at which a short digital run is the only economic option.

**CHAI MAMA** — UK00004435320 (fit 15.27)

A single-category tea range from a ten-month-old company. Loose leaf and tea bags are pre-made pouch formats, and a first tea range rarely justifies a printed reel.

### Prepared reply-stage sample

`reports/customer_test/2026-037/P009/reply_sample/` — `launchtrace_food_sample.csv`
and `launchtrace_food_sample.html`.

9 of the week's 14 companies — the ones where flexible packaging is the relevant spend. The five jarred and bottled sauce brands are left out.

Covering Email 2 (with the £79/month founding offer, no contract, weekly Friday
feed, cancel whenever): `reports/customer_test/2026-037/P009/email_2.md`.

### Follow-up

One only. `reports/customer_test/2026-037/P009/follow_up.md`.

**Subject:** Worth a look?

```
Hi there,

I sent over a few new brands last week — the energy bar and the York roastery in
particular looked like short-run work.

Worth me sending these each Friday, or not the right sort of lead for you? If it's
the wrong stage or the wrong categories, that's useful to know too.

Either answer is genuinely useful — if it isn't relevant I'll stop there.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### CRM state and next action

- **Status** — `READY`, Priority A. Contact address verified and recorded in the
  database; nothing sent, so no `EMAIL_1_SENT` date exists.
- **Next action** — approve or reject this pack. On approval, send Email 1 from
  your own mailbox, then record it:
  `python -m src.admin prospects set-status --prospect-id P009 --status EMAIL_1_SENT`
  (that stamps the follow-up due date seven days out).

---

## P001 — National Flexible (National Flexible Limited, 03486101, Birkenshaw, Bradford)

**Use case under test** — Flexible films on reel for flow-wrap and form-fill-seal lines

### Why we chose them

The opposite end of the same category from Digimock, and that contrast is the
point. National Flexible sells film on reel into running lines, names its food
sectors explicitly (bakery, savoury snacks, sweet snacks, health and nutrition,
frozen), and supplies household brands. If both it and Digimock reply, the
category is validated; if only one does, the reply tells us which end of the
packaging market the feed is actually for.

### Verified contact route

A generic sales mailbox published on their contact page, verified 11 September 2026
and recorded in the database. Note the mailbox sits on a different top-level domain
from the website — it was read off their own page, not assumed from the domain.

**Named contact** — None evidenced on their own site. A marketing mailbox is also published; the sales one is the right route.

### Email 1

**Subject:** Three new UK brands in your snack and nutrition sectors

```
Hi there,

I track newly published UK trade mark filings to pick up food and drink brands
while they're still early — often before the pack format is settled.

Three from this week sat squarely in the sectors you list — savoury snacks, sweet
snacks, health and nutrition — and in formats that run on reel rather than in
jars or bottles. That's why these three rather than the rest of the week.

Sami's Superfoods — Samis Superfoods Ltd (13274599), Sheffield. Roasted, salted,
spiced and candied nuts and fruit-and-nut mixtures. Trading since 2021, first
mark we've seen from them, still selling direct from their own site.
samissuperfoods.com. Nuts on VFFS, and film on reel is the recurring spend.

GOLDEN ROOTS — Heavenly Foods & Beverages Ltd (14547529), Leicester. Sweet potato
and cassava chips, vegetable crisps, plantain and yuca fries, including frozen.
A Leicester wholesaler trading since 2022, with no retailer listings found.
Bagged formats across ambient and frozen.

BULLITT — Coldharbour Foods Limited (17035779), Buckinghamshire. The filing covers
one line: "Energy Bar." Company six months old when it filed. Bar flow-wrap on
HFFS, in your health and nutrition sector.

All three published in this week's journal. These are signals that a brand exists
and looks early, not that anyone has said they're buying. None has been contacted.

The full set for this week is 14 UK food companies, 9 of them in flexible
packaging. Happy to send it over if it's useful.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### The leads chosen, and why each one matches

**Sami's Superfoods** — UK00004435509 (fit 18.11)

A Sheffield importer and packer trading since 2021, now protecting a broad own-brand nut range. Roasted and salted nuts are a vertical form-fill-seal format, and film on reel is the recurring line spend rather than a one-off print.

**GOLDEN ROOTS** — UK00004435549 (fit 14.89)

Sweet potato and cassava chips, vegetable crisps and frozen plantain and yuca fries. Savoury snacks and frozen are two of the sectors National Flexible names, and a range this wide implies bagged formats on reel across both.

**BULLITT** — UK00004437089 (fit 14.96)

Bar flow-wrap is a horizontal form-fill-seal reel format, and health and nutrition is one of their named sectors. Same brand as Digimock's second lead, argued the other way round — reel film rather than short digital run.

### Prepared reply-stage sample

`reports/customer_test/2026-037/P001/reply_sample/` — `launchtrace_food_sample.csv`
and `launchtrace_food_sample.html`.

The same 9 of 14 as Digimock. The category filter is the same; the three Email 1 examples are what differ.

Covering Email 2 (with the £79/month founding offer, no contract, weekly Friday
feed, cancel whenever): `reports/customer_test/2026-037/P001/email_2.md`.

### Follow-up

One only. `reports/customer_test/2026-037/P001/follow_up.md`.

**Subject:** Worth a look?

```
Hi there,

I sent a few new brands over last week — the Sheffield nut packer and the
root-vegetable snack range were the two that looked closest to your sectors.

Is this worth having each Friday, or not the right stage of brand for you? If
they're too early to be useful, that's worth knowing.

Either answer is genuinely useful — if it isn't relevant I'll stop there.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### CRM state and next action

- **Status** — `READY`, Priority A. Contact address verified and recorded in the
  database; nothing sent, so no `EMAIL_1_SENT` date exists.
- **Next action** — approve or reject this pack. On approval, send Email 1 from
  your own mailbox, then record it:
  `python -m src.admin prospects set-status --prospect-id P001 --status EMAIL_1_SENT`
  (that stamps the follow-up due date seven days out).

---

## P022 — Wholebake (Wholebake Limited, 03292581, Wrexham)

**Use case under test** — Contract manufacture of bars, bites and snacks

### Why we chose them

The narrowest supplier in the five, chosen deliberately. Wholebake makes bars,
balls and bites and nothing else, so it measures whether a 14-company week has
enough depth in one category to be worth £79 a month. It produced two relevant
leads, not three, and that is the most informative result in this test: it says
what a specialist manufacturer would actually be buying.

### Verified contact route

A generic information mailbox published on their site, verified 11 September 2026
and recorded in the database, plus an enquiry form on their get-in-touch page.
See the pre-send checks below — their site shows one address in its text and a
different one in the link behind it.

**Named contact** — None. No individual is named on the public site.

### Email 1

**Subject:** Two new bar and snack brands from this week

```
Hi there,

I track newly published UK trade mark filings to pick up food brands while
they're still early — usually before they've chosen who makes the product.

Fourteen UK food companies came through this week. Two of them are brands you
could actually make. The rest are sauces, seasonings, coffee, tea and chilled
dairy, plus a couple that sit just outside a bar line, so I've left those out
rather than pad the list.

BULLITT — Coldharbour Foods Limited (17035779), Buckinghamshire. The filing
covers one line: "Energy Bar." Company incorporated February 2026 and registered
for confectionery wholesale, not manufacture. One SKU, no factory behind it.

Sami's Superfoods — Samis Superfoods Ltd (13274599), Sheffield. Roasted, salted,
spiced and candied nuts and fruit-and-nut mixtures, from a brand already selling
its own chocolate line direct. samissuperfoods.com. Fruit, nut and coated formats
are what a bar and bite line makes.

Both published in this week's journal. These are signals that a brand exists and
looks early — not that either has said it's looking for a manufacturer. Neither
has been contacted.

Happy to send you the fuller picture for the week if it's useful.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### The leads chosen, and why each one matches

**BULLITT** — UK00004437089 (fit 14.96)

A single energy-bar SKU from a six-month-old company registered for confectionery wholesale, not manufacture. This is precisely the case where the bar is made by somebody else.

**Sami's Superfoods** — UK00004435509 (fit 12.10)

A Sheffield snack brand already selling its own chocolate line direct and now protecting a wide nut and fruit-and-nut range. Fruit, nut and coated formats are what a bar and bite line produces. The weaker of the two: a plausible adjacency rather than a stated need, which is why it is second in the email and not the reason to open it.

### Prepared reply-stage sample

`reports/customer_test/2026-037/P022/reply_sample/` — `launchtrace_food_sample.csv`
and `launchtrace_food_sample.html`.

4 of 14 — bars, nuts and snacks and confectionery. The sauces, seasonings, coffee, tea and chilled dairy are left out; two of the four sit slightly wider than a bar line and Email 2 says so.

Covering Email 2 (with the £79/month founding offer, no contract, weekly Friday
feed, cancel whenever): `reports/customer_test/2026-037/P022/email_2.md`.

### Follow-up

One only. `reports/customer_test/2026-037/P022/follow_up.md`.

**Subject:** Worth a look?

```
Hi there,

I sent two new brands over last week — the energy bar in particular looked like
a first-production-run conversation.

Is this worth having each Friday? Two relevant brands in a week is honest volume
for bars specifically, and if that's too thin to be useful I'd rather know.

Either answer is genuinely useful — if it isn't relevant I'll stop there.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### CRM state and next action

- **Status** — `READY`, Priority A. Contact address verified and recorded in the
  database; nothing sent, so no `EMAIL_1_SENT` date exists.
- **Next action** — approve or reject this pack. On approval, send Email 1 from
  your own mailbox, then record it:
  `python -m src.admin prospects set-status --prospect-id P022 --status EMAIL_1_SENT`
  (that stamps the follow-up due date seven days out).

---

## P039 — Sauce Master (Sauce Master Ltd, NI650631, Downpatrick, Northern Ireland)

**Use case under test** — Contract manufacture and private label of sauces, dressings and condiments

### Why we chose them

Sauces and condiments are the largest single category the feed produced this week —
six of fourteen companies — so this tests the category where LaunchTrace has the
most supply. Sauce Master's own site says it works with small artisan producers
as well as large accounts, which is the stage the feed identifies. It is also the
only prospect of the five with a compliance flag against it; see the risk below.

### Verified contact route

A generic information mailbox published on their contact page, verified
11 September 2026 and recorded in the database. Their contact page is a form; the
address is published alongside it.

**Named contact** — None evidenced. The site describes a family business but names no individual.

### Email 1

**Subject:** Three new UK sauce brands from this week

```
Hi there,

I track newly published UK trade mark filings to pick up food brands while
they're still early — usually before they've decided who makes the product.

Three from this week are sauces, dressings and marinades specifically, which is
why I've pulled these out rather than send you the whole list.

Cray Sauces — Cray Sauces Ltd (17383940), London. Mayonnaise-based and savoury
sauces, hot sauce, sriracha, barbecue, ketchup, salsas, sauce mixes. The company
was incorporated on 5 August and filed three weeks later, and is registered for
retail rather than manufacture.

BEAKY — Beaky Foods Ltd (17308892), London. Seasoning marinades, sauce mixes,
savoury sauces and spice rubs. Two-month-old company, registered for food
wholesale, no kitchen of its own on the record.

TOSS — Toss World Limited (17030618), Haslemere. Condiments, dressings and
sauces; the trade press has covered them going into the UK salad dressing aisle.
Already in market, so more of a scale-up conversation than a first batch.

All three published in this week's journal. These are signals that a brand exists
and looks early — not that any of them has said it's looking for a manufacturer.
None has been contacted.

I've got the full set for the week if it's useful — 14 UK food companies, of
which five are sauce, dressing and condiment brands.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### The leads chosen, and why each one matches

**Cray Sauces** — UK00004435936 (fit 14.96)

A sauce brand and nothing else — mayonnaise-based, spicy, barbecue, ketchup, salsas, sriracha, sauce mixes — from a company incorporated on 5 August 2026 and filing three weeks later, registered for retail rather than manufacture. There is no earlier point at which to reach a brand that will need someone to make the product.

**BEAKY** — UK00004436958 (fit 14.96)

Seasoning marinades, sauce mixes and savoury sauces from a two-month-old London company registered for food wholesale. Marinades and sauce mixes are wet production and a wholesaler that young is unlikely to own a kitchen.

**TOSS** — UK00004437777 (fit 14.96)

Dressings are Sauce Master's exact line. Unlike the other two this company does hold a condiments manufacturing code and the trade press has already covered it entering the UK dressing aisle — so it is framed honestly as a second-source or scale-up conversation, not a first batch.

### Prepared reply-stage sample

`reports/customer_test/2026-037/P039/reply_sample/` — `launchtrace_food_sample.csv`
and `launchtrace_food_sample.html`.

5 of 14 — the wet sauce, dressing and condiment brands. The coffee, tea, snack, bar and chilled dairy brands are left out, and so is one dry spice-blending range, which is a different process.

Covering Email 2 (with the £79/month founding offer, no contract, weekly Friday
feed, cancel whenever): `reports/customer_test/2026-037/P039/email_2.md`.

### Follow-up

One only. `reports/customer_test/2026-037/P039/follow_up.md`.

**Subject:** Worth a look?

```
Hi there,

I sent three new sauce and dressing brands over last week — the London one that
incorporated three weeks before it filed was the earliest of them.

Worth having this each Friday, or not the right fit? If the brands are too early
to be worth a call, that's useful to know.

Either answer is genuinely useful — if it isn't relevant I'll stop there.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### CRM state and next action

- **Status** — `READY`, Priority A. Contact address verified and recorded in the
  database; nothing sent, so no `EMAIL_1_SENT` date exists.
- **Next action** — approve or reject this pack. On approval, send Email 1 from
  your own mailbox, then record it:
  `python -m src.admin prospects set-status --prospect-id P039 --status EMAIL_1_SENT`
  (that stamps the follow-up due date seven days out).

---

## P038 — Cotswold Fayre (Cotswold Fayre Limited, 04711000, Theale, Reading)

**Use case under test** — Speciality food distribution and new-brand sourcing

### Why we chose them

The only prospect in the five that is not a supplier of inputs, and the one for
which the whole week is relevant rather than a slice of it. A speciality
wholesaler can plausibly list any of the fourteen, so this measures whether
breadth is worth paying for where the packaging and manufacturing suppliers are
buying depth. It is also the hardest test of the pitch: they already run a
new-brand intake through RangeMe, so the feed has to beat an existing process.

### Verified contact route

A generic sales mailbox published on their contact page (Cloudflare-obfuscated on
the page and read from it directly), verified 11 September 2026 and recorded in
the database, alongside a published phone number and a contact form.

**Named contact** — None. Their "Become A Supplier" route goes to RangeMe, which is for brands
pitching them, not for reaching their buying team, so it is not the right route
for this. The generic sales mailbox is the safe one; expect it to need forwarding
internally, and say so in the follow-up (the draft already asks the RangeMe
question directly).

### Email 1

**Subject:** Three new UK food brands worth a look

```
Hi there,

I track newly published UK trade mark filings to pick up food and drink brands
while they're still early — usually before they have a wholesale route.

Three from this week looked like brands you could actually list, rather than ones
still a year from having product, so those are the ones I've pulled out.

kWh Coffee — Morning People Ltd (16660094), York. A speciality roastery: whole
bean, ground, coffee bags and drip bag. Their site already offers wholesale, and
there are no multiple-retailer listings behind them. kwhcoffee.com.

Sami's Superfoods — Samis Superfoods Ltd (13274599), Sheffield. Roasted, salted
and spiced nuts, fruit-and-nut mixtures and a chocolate line, selling direct from
their own site only. samissuperfoods.com. The product exists; the route into
independents doesn't yet.

TOSS — Toss World Limited (17030618), Haslemere. Condiments and dressings; the
trade press has covered them going into the UK salad dressing aisle with a vegan,
gluten-free range. A finished product rather than an idea.

All three published in this week's journal. These are signals that a brand exists
and looks early — not that any of them is looking for a distributor. None has
been contacted.

The full set for the week is 14 UK food companies across snacks, sauces, coffee,
tea, confectionery and chilled. Happy to send it over if it's useful.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### The leads chosen, and why each one matches

**kWh Coffee** — UK00004435554 (fit 17.08)

Speciality coffee is a core independent-retail category, this is a real roastery with a live verified site, and it already offers wholesale on that site while having no multiple-retailer listings behind it. Ready to list, with the route still open.

**Sami's Superfoods** — UK00004435509 (fit 17.30)

The product exists and is shipping, but direct from its own site only. The route into independents is the part that is missing, which is exactly what Cotswold Fayre sells.

**TOSS** — UK00004437777 (fit 13.96)

A speciality dressing that is already a finished product in the UK dressing aisle per the trade press — the stage a wholesaler can list now rather than wait for.

### Prepared reply-stage sample

`reports/customer_test/2026-037/P038/reply_sample/` — `launchtrace_food_sample.csv`
and `launchtrace_food_sample.html`.

All 14. Nothing was cut, because for a speciality wholesaler every company in the week is a brand that could plausibly want a wholesale route — and Email 2 says that explicitly, in contrast to the filtered samples the other four receive.

Covering Email 2 (with the £79/month founding offer, no contract, weekly Friday
feed, cancel whenever): `reports/customer_test/2026-037/P038/email_2.md`.

### Follow-up

One only. `reports/customer_test/2026-037/P038/follow_up.md`.

**Subject:** Worth a look?

```
Hi there,

I sent three new brands over last week — the York roastery and the Sheffield nut
and chocolate brand were the two already shipping product.

Is a list like this worth having each Friday for your buying team, or are you
already seeing these brands through RangeMe? If it's duplicating what you have,
that's worth knowing.

Either answer is genuinely useful — if it isn't relevant I'll stop there.

Kieran
LaunchTrace · [PHONE] · [WEBSITE]

Sources: UK Intellectual Property Office and Companies House, used under the Open
Government Licence v3.0. Reply "no thanks" and I won't contact you again.
```

### CRM state and next action

- **Status** — `READY`, Priority A. Contact address verified and recorded in the
  database; nothing sent, so no `EMAIL_1_SENT` date exists.
- **Next action** — approve or reject this pack. On approval, send Email 1 from
  your own mailbox, then record it:
  `python -m src.admin prospects set-status --prospect-id P038 --status EMAIL_1_SENT`
  (that stamps the follow-up due date seven days out).

---
## Definition of done

| # | Condition | State |
| --- | --- | --- |
| 1 | All five prospects reverified | Done — Companies House status, website, what they sell, fit with emerging brands |
| 2 | Safe contact routes identified | Done — five verified generic addresses, in the database, none guessed |
| 3 | Each has a tailored 2–3 lead preview | Done — 3, 3, 2, 3, 3 |
| 4 | Every lead genuinely relevant to that supplier type | Done — reason written per lead; Wholebake returned two rather than pad to three |
| 5 | Five concise Email 1 drafts | Done |
| 6 | Positive-reply samples and Email 2 drafts | Done — sample pack plus covering email per prospect |
| 7 | One follow-up per prospect | Done — one only, no sequence |
| 8 | Operator review report | This file |
| 9 | Nothing sent | Confirmed — no email, no form, no Resend, no Stripe, no deploy |
| 10 | No signal-engine changes | Confirmed |
| 11 | No personal operational data in git | Confirmed — addresses are in the database; this report names the mailbox type, not the address |
| 12 | No manufactured code commit | Confirmed — no application code changed |

## Next action

Review the five outreach packs and approve which ones should be sent.
