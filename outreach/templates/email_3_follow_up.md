# Email 3 — one follow-up

Send once, about a week after the previous message. If there is no reply, stop.
Mark `reply_state` as `none`, set the status to `NO_RESPONSE`, and move on.

    python -m src.admin outreach-draft --prospect-id P001 --template email_3

---

**Subject:** Worth keeping the feed going?

Hi {{GREETING}},

Just checking whether any of the brands in the sample were useful to you.

If the feed is worth having each Friday, the founding price is £79/month. If it
isn't useful enough yet, I'd genuinely like to know what would make it useful
for your sales team — different categories, more detail on each brand, contact
routes, or something else entirely.

Either answer is helpful.

Best,
{{OPERATOR_NAME}}
{{OPERATOR_COMPANY}} · [PHONE] · [WEBSITE]

*Reply "no thanks" and I won't contact you again.*

---

## After this

**Stop.** One first contact, one sample, one follow-up. If there is no reply
after this, they are not interested right now, and a fourth email will only cost
you the relationship.

If they say no, record why:

    python -m src.admin prospects set-status --prospect-id P001 --status NOT_NOW \
      --note "what they actually said"

That note is the most valuable output of this whole exercise. "The brands are
too early for us" means something quite different from "we already know about
these", and each points at a different fix.

If they opt out, record it immediately — this adds them to the suppression list
by email, domain and company name, and they can never be re-imported:

    python -m src.admin prospects opt-out --prospect-id P001
