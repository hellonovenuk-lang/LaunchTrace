# Backup and recovery

Practical, not enterprise. The whole business fits in a few megabytes; what
matters is knowing which copy is authoritative and never losing the one file
that cannot be reconstructed.

---

## 1. Source of truth

**This table is the important part of this document.** When two copies
disagree, the one named here wins.

| Data | Source of truth | Can it be rebuilt? |
| --- | --- | --- |
| **Prospect suppression list** | `prospect_suppressions` table | **No. Never. Losing it means contacting people who asked you not to.** |
| Live outreach state — addresses, contacts, replies, dates, funnel position | `prospect_state` table | No — it is what people told you and what you did. |
| Supplier prospect research | `outreach/prospects_seed.csv`, in git | Yes — git history. It is hand-researched, but it is committed. |
| Customer records and subscription state | `customers` table | Partly: Stripe holds the authoritative subscription state and can be re-synced. The recipient addresses are only here. |
| Stripe subscription and customer ids | Stripe | Yes — Stripe is authoritative. The local copy is a convenience. |
| In-feed suppressions (companies removed on request) | `suppression_rules` table | **No.** Same reason as the prospect list. |
| Customer lead feedback | `lead_feedback` table | No — it is what people told you. |
| Opportunities and scores | `opportunities`, `score_events` | Yes — re-run the pipeline over the journals. |
| Parsed trade mark records | `trademark_records` | Yes — re-download the journals. |
| Journal files | `data/cache/journals/` | Yes — public data, re-downloadable. Not worth backing up. |
| Companies House index | `data/companies_house/` | Yes — free monthly snapshot, ~500 MB. Do not back this up. |
| Configuration and business rules | `config/*.json`, in git | Yes — git history. |
| Generated reports, drafts, samples | `reports/` | Yes — regenerate on demand. Not worth backing up. |
| Credentials | Your password manager | **No.** They are not in this repository and must never be. |

Two lines to remember: **anything derived from public data is disposable;
anything a human wrote or a person told you is not.**

---

## 2. Taking a backup

```bash
python -m src.admin backup
```

Writes a dated folder under `reports/backups/` containing:

* `prospects_seed.csv` — the researched list
* `prospect_state.csv` — the live outreach state, exported from the database
* `prospect_suppressions.csv` — every opt-out and suppression, exported from
  the database. **This is the file that must never be lost.**
* `config/` — every business rule and weighting
* `customers.csv` — companies, plans, subscription status, Stripe ids
* `db_suppression_rules.csv` — in-feed suppressions
* `lead_feedback.csv` — what customers said
* the SQLite database file, when you are running on SQLite

Do it **before anything that edits the list in bulk** and **on the first of
each month**. It takes a second.

### Get it off this machine

The backup is worthless in the same place as the original. Any of these is fine:

* copy the folder to a cloud drive;
* email it to yourself;
* commit `outreach/` to git and push — these two files are already tracked, so
  `git push` after any prospect change is a real backup with full history.

**Git is the easiest option and you are already using it.** After a session of
outreach:

```bash
git add outreach/
git commit -m "Update prospect tracker"
git push
```

---

## 3. Restoring

### The prospect research

It is in git. `git log -- outreach/prospects_seed.csv`, then
`git checkout <commit> -- outreach/prospects_seed.csv`.

### The live outreach state and the suppression list

Both are in the database, so restoring them means restoring the database (see
below) or re-importing `prospect_state.csv` and `prospect_suppressions.csv`
from a dated backup folder.

The suppression list has a second protection: it is **insert-only**. There is
no code path in `src/sales/store.py` that deletes from `prospect_suppressions`,
and it is a separate table from `prospect_state`, so removing a prospect can
never remove the record of their opt-out. A bug or a bad edit cannot silently
shrink it.

### The database

*SQLite:* stop anything using it and copy the file back over
`data/local/launchtrace.sqlite`.

*PostgreSQL / Supabase:* Supabase takes daily backups on every plan, including
free — restore from the dashboard. That is the real backup for hosted data; the
CSV export above is for reading, not restoring.

### Customers, after losing the database

Stripe is authoritative for who is paying. Recreate each customer with
`python -m src.pipeline add-customer`, taking company and email from the Stripe
dashboard, then set the Stripe ids from `customers.csv` in your latest backup.

Recipient addresses that were never in Stripe (the second and third people on a
plan) exist only in your backup — which is the reason to take one.

---

## 4. What would actually hurt

Ordered by how bad it is, which is not the same as how likely.

1. **Losing the suppression list.** You contact people who told you to stop.
   That is a compliance failure and the end of those relationships. Mitigated
   by: copy-on-write, refusal to shrink, git tracking, and the monthly backup.
2. **Losing the prospect tracker.** Weeks of research gone, and worse, no
   record of who you already emailed — so you email them again. Mitigated by:
   copy-on-write and git.
3. **Losing customer recipient lists.** Paying customers stop receiving the
   feed and you do not know it. Mitigated by: the monthly backup and Stripe.
4. **Losing feedback.** You lose the evidence for every future scoring decision.
   Annoying, not fatal.
5. **Losing opportunities and journals.** Re-run the pipeline. An afternoon.

---

## 5. What not to build

No backup service, no replication, no disaster-recovery runbook. At this size a
monthly `python -m src.admin backup` plus `git push` after every prospect
change is genuinely enough, and anything more is time not spent finding a
customer.

Revisit when there are more than about twenty paying customers — at that point
losing a day of data matters enough to justify automating this.
