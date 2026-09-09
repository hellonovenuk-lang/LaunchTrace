# LaunchTrace's own customer prospecting

This directory is for **LaunchTrace selling to suppliers**. It has nothing to
do with the product feed.

**No email is ever sent from this repository, by any code, to any prospect.**
There is no send command, no transport, and no code path from the outreach
module to an email provider — `tests/test_outreach.py::TestNoSendSafeguards`
enforces it. Outreach is a manual act by the owner, on purpose.

## Where the list lives

| What | Where | In git? |
| --- | --- | --- |
| The researched seed list — who these companies are and why they fit | `prospects_seed.csv` | Yes. It is reusable research with no personal data in it. |
| Verified addresses, named contacts, reply notes, outreach dates, funnel position | `prospect_state` in the application database | No. |
| Opt-outs and suppressions, by email, domain and company name | `prospect_suppressions` in the application database | No. |

Live outreach data is personal data about identifiable people at identifiable
businesses. It has a retention period, an opt-out has to be honoured
immediately and permanently, and a commit history makes deletion effectively
impossible. So it accumulates in the database, not in the repository. The
research does not have that problem, and stays where it can be reviewed in a
diff.

Only `src/sales/store.py` reads or writes either half, and the seed file is
append-only from its side: a new prospect adds a research row, and no existing
row is ever rewritten by code.

## Files

| File | What it is |
| --- | --- |
| `prospects_seed.csv` | The researched 60-company seed list. Research only. |
| `prospects_schema.md` | What each column and each database field means |
| `templates/email_1_first_contact.md` | First approach, with three real examples |
| `templates/email_2_full_sample.md` | Sent after interest |
| `templates/email_3_follow_up.md` | One follow-up, then stop |

## The state of the list

**60 real UK companies**, identified by web research: 22 packaging (flexible,
labels, cartons), 21 contract manufacturers and co-packers, and 17 fulfilment,
distribution and brokerage businesses. Each carries a written reason it fits.

Currently: **11 Priority A, 38 B, 10 C, 1 excluded by research** (a duplicate
of another row in the same group). Priority and score are derived from
`config/icp_scoring.json` and computed the first time the seed is loaded, so
they are not checked in — `prospects rescore` recomputes them at any time.

Two things are deliberately incomplete:

* **No prospect has a contact address yet.** An address cannot be verified from
  a search result, and a guessed one is worse than a blank. Open each website,
  find the real `sales@` or `enquiries@`, and record it — about an hour for ten:

  ```bash
  python -m src.admin prospects set-contact --prospect-id P012 \
    --email sales@example.co.uk --source website_verified
  ```
* **Twelve rows carry "Verify website" in `research_notes`** — the company name
  came from a reliable source but the exact URL was not confirmed. Check before
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

Everything that command writes goes to the database.

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

`python -m src.admin backup` exports the live state and the suppression list
to CSV in a dated folder, because they are not in git and the database is the
only copy.

See `docs/LEGITIMATE_INTERESTS_ASSESSMENT.md`, Activity B, for the basis on
which this is done, and `docs/DATA_RETENTION.md` for how long any of it is kept.
