# Email 1 — first contact

The objective is one question: **is this information useful to you?** No meeting
request, no pricing, no product explanation. Two or three real, relevant
opportunities and an offer to send the rest.

Generate the draft rather than filling this in by hand:

    python -m src.admin prospect-preview --prospect-id P001 --draft-email

The placeholders in double braces are replaced automatically. Everything in
square brackets is yours to fill in once, then keep.

---

**Subject:** A few new food brands that may be useful

Hi {{GREETING}},

I've been tracking newly published UK trade mark activity to identify food
brands while they're still relatively early in development.

A few from this week looked potentially relevant to the {{SUPPLIER_SERVICE}}
work you do, so I thought I'd send them over.

{{LEAD_BLOCK}}

I'm testing a weekly feed that does this automatically — verifies the company
against Companies House, checks how established the brand already is, and
filters out the obvious noise.

If this sort of signal is useful for your sales team, I can send you the full
sample from this week.

Best,
{{OPERATOR_NAME}}
{{OPERATOR_COMPANY}} · [PHONE] · [WEBSITE]

*Sources: UK Intellectual Property Office and Companies House, used under the
Open Government Licence v3.0. Reply "no thanks" and I won't contact you again.*

---

## Before you send

- [ ] Every brand is real, from a recent run, and suits **this** prospect's product
- [ ] You have opened at least one source link and checked it says what we say
- [ ] The address is a real one you found on their website, not a guess
- [ ] This company is not suppressed — `prospects show` says so, and `outreach-due` drops anyone blocked
- [ ] Your name, company and an opt-out line are all present

## What not to do

Do not claim these companies are buying anything. You are saying they exist and
are early. That is the whole and sufficient point.

Do not send this with fewer than two examples, and do not pad it to three with a
lead that does not fit. The generator returns fewer than three on purpose.
