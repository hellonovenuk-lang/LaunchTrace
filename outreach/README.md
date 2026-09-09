# LaunchTrace's own customer prospecting

This directory is for **LaunchTrace selling to suppliers**. It has nothing to
do with the product feed.

**No email is ever sent from this repository, by any code, to any prospect.**
There is no send command, no transport, and no code path from the outreach
module to an email provider — `tests/test_outreach.py::TestNoSendSafeguards`
enforces it. Outreach is a manual act by the owner, on purpose.

## Files

| File | What it is |
| --- | --- |
| `prospects.csv` | The prospect tracker. **Source of truth.** |
| `prospects_schema.md` | What each column means and its allowed values |
| `suppressions.csv` | Anyone who opted out, by email, domain and company name. **Never contact these.** |
| `templates/email_1_first_contact.md` | First approach, with three real examples |
| `templates/email_2_full_sample.md` | Sent after interest |
| `templates/email_3_follow_up.md` | One follow-up, then stop |
| `backups/` | Copy-on-write safety copies. Not tracked in git. |

## The state of the list

**60 real UK companies**, identified by web research: 22 packaging (flexible,
labels, cartons), 21 contract manufacturers and co-packers, and 17 fulfilment,
distribution and brokerage businesses. Each carries a written reason it fits.

Currently: **11 Priority A, 38 B, 10 C, 1 suppressed** (a duplicate of another
row in the same group).

Two things are deliberately incomplete:

* **`generic_contact_email` is empty on every row.** An address cannot be
  verified from a search result, and a guessed one is worse than a blank. Open
  each website, find the real `sales@` or `enquiries@`, paste it in, and set
  `email_source` to `website_verified`. About an hour for ten.
* **Twelve rows carry "Verify website" in `notes`** — the company name came
  from a reliable source but the exact URL was not confirmed. Check before
  contacting. `prospects audit` lists them.

## Daily use

```bash
python -m src.admin outreach-due
```

One command. Who is due which action today, in the order to work through, with
the command for each. Everything else is a detail of that.

```bash
python -m src.admin prospects list --priority A       # who to start with
python -m src.admin prospects show --prospect-id P012 # everything, incl. why it scored
python -m src.admin prospects audit                   # what needs fixing
python -m src.admin prospects rescore                 # after editing the CSV or the weights
```

## Sending one

```bash
python -m src.admin prospect-preview --prospect-id P012 --draft-email
```

Picks the strongest opportunities that genuinely suit *this* supplier, writes a
briefing to read first, and drafts Email 1 with the leads already inserted. The
draft lands in `reports/outreach_drafts/`. Read it, check a source link, then
send it **yourself, from your own mailbox**.

Then record it:

```bash
python -m src.admin prospects set-status --prospect-id P012 --status EMAIL_1_SENT
```

Full sequence: `FIRST_CUSTOMER_PLAYBOOK.md`.

## Rules

- **Check the suppression list before every send.** `outreach-due` does it for
  you and drops anyone blocked, even if their row still says READY.
- **Never invent an email address.** See `prospects_schema.md`, rule 1.
- Prefer generic business addresses over a named individual's.
- Prioritise limited companies and LLPs — under PECR, direct marketing email to
  corporate subscribers does not require prior consent, but you must identify
  yourself and offer an opt-out every time. Every template does both.
- **One follow-up. Then stop.** The due logic will never suggest a second.
- Use real examples from a recent run. Never invent a brand to fill a gap — the
  preview returns fewer than three on purpose when fewer than three fit.
- Record an opt-out the day it arrives:
  `python -m src.admin prospects opt-out --prospect-id P012`

See `docs/LEGITIMATE_INTERESTS_ASSESSMENT.md`, Activity B, for the basis on
which this is done, and `docs/DATA_RETENTION.md` for how long any of it is kept.
