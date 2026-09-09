# Compliance review

**A record of what was checked, not a legal opinion.** Nothing in this document
is legal advice, and nothing here says LaunchTrace is compliant. It says which
questions were asked, what the drafts already cover, and what still needs a
solicitor or a decision from the owner.

Reviewed: the commercial-readiness pass. The existing drafts were **audited,
not rewritten** — they were sound, and rewriting them would have lost the
thinking already in them.

---

## 1. Coverage check

| Area | Covered in | Verdict |
| --- | --- | --- |
| The commercial dataset (the feed itself) | `PRIVACY.md` §1, `LEGITIMATE_INTERESTS_ASSESSMENT.md` Activity A | Covered |
| Public-source attribution (OGL v3.0) | `ATTRIBUTION.md`, and every sample, email and preview | Covered |
| LaunchTrace's own B2B prospecting | `PRIVACY.md` §3, LIA Activity B | Covered |
| Suppression and opt-out | `PRIVACY.md` §3, `DATA_RETENTION.md` | **Extended this pass** — see §2 |
| Customer data | `PRIVACY.md` §2, `TERMS.md` §9 | Covered |
| Retention | `DATA_RETENTION.md` | **Extended this pass** — see §2 |
| Stripe / payment handling | `TERMS.md` §4, `PRIVACY.md` §4 | Covered. LaunchTrace never sees card details; Stripe is the processor. |
| Sample-request form | `PRIVACY.md` §3 | Covered. Salted IP hash only, never the address. |
| Nature of the information (not purchase intent) | `TERMS.md` §3 | Covered, and the wording is reused verbatim in the product |

---

## 2. What this pass changed

Additive only. No existing clause was weakened.

**`DATA_RETENTION.md`** gained rows for the new stores, because each holds
something the old table did not cover:

* `lead_feedback` — customer opinions about brands, [24] months.
* `outreach/prospects.csv` — the tracker, [24] months from last interaction.
  It can now hold `named_contact` and `decision_maker_role`, which is new
  personal data, so rule 7 was added: **only where the operator already knew
  them; do not go looking.**
* `outreach/suppressions.csv` — indefinite, like the database suppression list.
* Generated drafts, previews and samples — working files, delete freely.

Three new rules were added, each enforced in code rather than only written down:

* **Rule 5, never generate a contact address.** `email_source` records
  provenance, and `prospects audit` reports how many rows have a verified one.
  Nothing in the system fills one in.
* **Rule 6, never re-import a suppressed business.** The store refuses by
  email, domain *and* company name.
* **Rule 7, minimise personal data in the tracker.** Generic addresses
  preferred over named individuals.

The deletion-request procedure gained a second path, for a supplier LaunchTrace
approached, as one command that cannot be half-done.

---

## 3. Assessment of the new capabilities

### The prospect tracker

**Question:** does a richer tracker change the privacy position for Activity B?

**Finding:** marginally, and in the direction of more discipline. The old
schema already held company, website and a contact email. The new one adds
optional `named_contact` and `decision_maker_role` — personal data where used.
Against that, it adds `email_source`, enforced non-generation, one-way
opt-out, and duplicate protection, all of which reduce the chance of contacting
the wrong person or contacting anyone twice.

**Owner decision needed:** whether to use `named_contact` at all. The safest
position is generic addresses only, and the system works entirely without it.

### Prospect-specific previews

**Finding:** no new position. A preview reorders opportunities that have
already qualified; it creates no new data about any brand and reaches no
external service. The brands quoted in an outreach email were already in the
delivered feed.

### The customer feedback loop

**Finding:** a customer writes a note about a *brand*, not about a person. The
identifier is the recipient's own email address, taken from their feed. No new
category of personal data.

**Worth noting:** feedback never changes scoring automatically. That is a
product decision, but it also keeps the score explainable — which matters if a
company ever asks why it appeared.

### The JSON API

**Finding:** same functions as the existing HTML pages, so no new behaviour.
Two properties were deliberately preserved and tested: the opt-out endpoint
returns an identical response whether or not the address was known (so it
cannot be used to test whether an address is on file), and the honeypot answers
as though accepted while storing nothing.

---

## 4. Still requires solicitor or owner review

Unchanged from the previous pass, and none of it was resolved here.

1. **Every `[BRACKET]` placeholder** in `PRIVACY.md` and `TERMS.md` — contact
   address, retention periods, company details. The documents cannot be
   published with them in.
2. **Solicitor review of all four documents.** They are drafts and they say so.
3. **The LIA balancing test for Activity A** turns on whether identifying a
   company as an early-stage brand is within its reasonable expectations. That
   is a judgement a solicitor should confirm, not one this build can settle.
4. **ICO registration** as a data controller, £40–60/year, before processing
   personal data in anger.
5. **Confirm with the IPO** that the weekly Journal XML is OGL v3.0, and record
   the reply in `ATTRIBUTION.md`. The Open Data release is confirmed; the
   weekly journal is assumed on the same basis.
6. **PECR position on the corporate-subscriber exemption.** The templates
   identify the sender and offer an opt-out every time, which is the
   requirement as understood — worth confirming.
7. **Whether to record named contacts at all** (§3 above).

## 5. Deliberately not done

* **Contact-name enrichment for brands in the feed.** The most-requested
  feature and the one that most changes the privacy position. The LIA would
  need redoing before it is built. Recorded in `HANDOFF.md` §6.
* **Automated outreach sending.** Excluded by design, and a compliance
  decision rather than a feature gap.
* **Any storage of directors' home addresses, dates of birth, or PSC records.**
  The Companies House index is built from corporate fields only. This remains
  true after this pass.

---

*This document records an internal review. It is not legal advice and does not
assert compliance. Sections 4 and 5 are the ones to take to a solicitor.*
