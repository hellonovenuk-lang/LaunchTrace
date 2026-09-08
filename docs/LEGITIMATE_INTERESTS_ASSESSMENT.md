# Legitimate interests assessment (LIA)

**Operational draft prepared during the build. Not legal advice and not
reviewed by a lawyer. The ICO expects a controller to complete and record its
own LIA — this is a starting structure with our reasoning filled in, for you to
check, amend and sign off.**

Based on the ICO's three-part test (purpose, necessity, balancing). Assessed
by: [NAME]. Date: [DATE]. Review due: [DATE + 12 months].

There are two distinct processing activities. They are assessed separately
because the balance is different for each.

---

# Activity A — Compiling the LaunchTrace Food feed

## 1. Purpose test

**What is the legitimate interest?** Operating a commercial business
information service that helps UK suppliers identify emerging food brands from
official public registers.

**Who benefits?**

- *Us*: it is our product and our revenue.
- *Our subscribers*: manufacturers and suppliers who would otherwise pay staff
  to monitor trade mark filings by hand, or would not find these brands at all.
- *The brands identified*: early-stage food companies genuinely need packaging,
  manufacturing and distribution partners. Being findable by relevant suppliers
  is, more often than not, useful to them.
- *Wider public interest*: the government publishes these registers precisely
  so they can be used, including commercially. The OGL expressly permits it.

**Is the purpose legitimate?** Yes. It is a lawful commercial activity using
data published for reuse under an open licence.

## 2. Necessity test

**Is the processing necessary for that purpose?** Yes. The service is the
identification of newly filed brands; it cannot be delivered without processing
the filings.

**Is there a less intrusive way?** We have taken the less intrusive options
available:

- We process **corporate** data by design. Natural-person applicants are
  downranked rather than developed as leads.
- We never collect officer records, persons with significant control, dates of
  birth, or **directors' home addresses** — even though some are available.
- We collect no individuals' phone numbers or personal email addresses.
- Web enrichment stores **links and short snippets** as evidence, not copies of
  people's content.
- Enrichment runs only against records that already passed cheap filters, so we
  research far fewer companies than we see.

Could we do it with no personal data at all? Not entirely — a sole trader's
name on the public register is personal data and is intrinsic to the record.
But that data is already published by the state, is business-context data, and
we minimise its use as above.

## 3. Balancing test

**Whose data?** Mostly companies (not personal data at all). Where individuals
appear, they are people who have chosen to file a trade mark in a commercial
class — an inherently public, commercial act.

**Would they expect it?** Reasonably, yes. The IPO publishes the Trade Marks
Journal specifically so third parties can inspect new filings, and it is
routinely used commercially by legal, brand and supplier businesses. A person
filing a trade mark for a food product is publicising a commercial intention.

**Is any of it sensitive?** No. We process no special category data, no
criminal offence data, no financial data about individuals, and no data
relating to children.

**What is the impact on them?** Low. The likely outcome is that a relevant UK
supplier gets in touch about a service the brand plausibly needs. No decision
with legal or similarly significant effect is made about anyone; the LaunchTrace
Score is an internal prioritisation signal, not an automated decision about a
person, so Article 22 is not engaged.

**Could it cause harm?** The realistic risks are (a) unwanted contact and
(b) an inaccurate impression from a wrong company match. We mitigate both:

- **Suppression on request.** Any company or individual can ask to be removed
  and is suppressed from all future feeds, permanently.
- **Uncertainty is shown, not hidden.** Every company match carries a
  confidence score and its evidence; low-confidence matches are capped below
  the top band rather than presented as fact.
- **No outreach by us.** LaunchTrace does not email the brands it identifies.
  Subscribers make their own approaches and are contractually responsible for
  doing so lawfully.
- **No claims of purchasing intent.** The product says "relevance", never "is
  buying now".

**Safeguards in place.** Data minimisation as above; retention limits; a
suppression list that is never overridden; access limited to the operator;
credentials held outside source control; and an explicit ban on directors'
home addresses in the codebase configuration.

## 4. Outcome

The legitimate interests basis is considered **appropriate** for Activity A,
subject to the safeguards above being maintained.

**Conditions:** if the product later starts collecting individuals' direct
contact details, or starts contacting identified brands directly, this
assessment must be redone before that change ships.

---

# Activity B — LaunchTrace's own B2B prospecting

## 1. Purpose test

Contacting UK supplier businesses (packaging manufacturers, contract
manufacturers, co-packers, distributors, fulfilment providers) to offer a
directly relevant paid service, and to ask whether the sample is useful.

Legitimate: yes — ordinary B2B selling of a relevant service.

## 2. Necessity test

Necessary to reach the specific, narrow set of businesses this product exists
to serve. Mitigations:

- We prioritise **corporate subscribers** — limited companies and LLPs — for
  whom PECR does not require prior consent for direct marketing email.
- We prefer **generic business addresses** (`sales@`, `enquiries@`) over named
  individuals' addresses wherever one is published.
- The prospect list is small and hand-built (around 60 companies), not scraped
  at volume.
- Follow-up is limited to a short, finite sequence; there is no ongoing drip
  campaign.

## 3. Balancing test

**Expectation.** A packaging manufacturer would reasonably expect approaches
from suppliers of sales-lead services. The message is directly relevant to what
they sell.

**Impact.** Minimal — one or two emails to a business address, with a clear
opt-out.

**Rights protected.**

- Sender identity is stated in every message.
- Every message carries an opt-out and we act on it immediately.
- Opted-out contacts are recorded permanently and **never re-added** — this is
  enforced in code by the suppression list, not by memory.
- No purchased lists and no bulk scraping.

## 4. Outcome

**Appropriate**, on condition that: (a) marketing email goes to corporate
subscribers, preferring generic business addresses; (b) every message carries
sender identity and an opt-out; (c) the suppression list is honoured absolutely;
and (d) no automated cold-emailing is built into the product.

---

## Owner sign-off

| | |
| --- | --- |
| Assessed by | [NAME] |
| Date | [DATE] |
| Reviewed by (solicitor) | [NAME / not yet reviewed] |
| Next review | [DATE] |
