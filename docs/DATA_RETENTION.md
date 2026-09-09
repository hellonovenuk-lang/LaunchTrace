# Internal data-retention note

**Operational draft. Set the periods you actually want, then make sure the
system enforces them.**

## What we keep and for how long

| Data | Where | Retention | Why |
| --- | --- | --- | --- |
| Raw journal downloads | `data/cache/journals/` | 30 days, then pruned | Re-runs and debugging. There is no operational reason to keep large raw files longer. `KEEP_RAW_JOURNAL_FILES=false` deletes them sooner. |
| Journal metadata (number, date, checksum, record count) | `journals` | Indefinite | Small, and it is what prevents duplicate processing. |
| Parsed trade mark records | `trademark_records` | [24] months | The source layer the pipeline can be re-run against without re-downloading. |
| Company match results | `company_matches` | [24] months | Avoids repeating Companies House lookups; holds the match evidence. |
| Web enrichment | `web_enrichment` | [12] months | Web evidence goes stale quickly; re-check rather than trust an old answer. |
| Opportunities and scores | `opportunities`, `score_events` | [24] months | The delivered product, and the history needed to tune scoring. |
| Pipeline runs and errors | `pipeline_runs`, `errors` | [12] months | Operational diagnosis. |
| Customer records | `customers`, `customer_preferences` | Life of subscription + [6] years | Contract and accounting. |
| Delivery log | `deliveries` | [24] months | Proves what was sent, and prevents double-sending. |
| Sample requests | `sample_requests` | [24] months from last interaction | Following up a business enquiry. |
| Suppression list | `suppression_rules` | **Indefinite** | The only way to guarantee an opted-out contact is never contacted again. Deleting it would defeat its purpose. |
| Stripe webhook events | `webhook_events` | [24] months | Idempotency guard and payment audit trail. |
| Customer lead feedback | `lead_feedback` | [24] months | Evidence for tuning scoring. Free-text notes are written by a customer about a *brand*, not about a person. |
| Supplier prospect tracker | `prospect_state` | [24] months from last interaction | LaunchTrace's own sales pipeline. Holds corporate contact details, plus a named contact and role **only where the operator already knew them** — nothing is inferred or looked up. Deliberately **not** in git, so a retention deletion is a real deletion rather than a commit that still contains the data. |
| Prospect suppression list | `prospect_suppressions` | **Indefinite** | Records opt-outs by email, domain and company name. Insert-only and never deleted, for the same reason as `suppression_rules`. |
| Supplier prospect research | `outreach/prospects_seed.csv`, in git | Kept | Company name, website, sector and why they fit. Business information about a company, not personal data about a person — which is why this half can be committed. |
| Generated drafts, previews and samples | `reports/outreach_drafts/`, `reports/previews/`, `reports/samples/` | Delete freely; regenerate on demand | Working files containing prospect names and lead data. They are outputs, not records — nothing depends on keeping them. |

## Rules that do not change

1. **Never store directors' home addresses**, dates of birth, persons with
   significant control, or officer records. The Companies House bulk index is
   built from corporate fields only.
2. **Never store raw IP addresses.** The sample form stores a salted hash, used
   solely for rate limiting.
3. **Never delete a suppression record** in order to re-contact someone.
4. **Never commit real credentials.** `.env` is git-ignored; `.env.example`
   holds names only.
5. **Never generate a contact address.** A contact email is only ever recorded
   with the source it came from (`email_source`), and "I guessed the pattern"
   is not a source. `python -m src.admin prospects audit` reports how many rows
   have a verified address; it will never fill one in.
6. **Never re-import a suppressed business.** The prospect store refuses, by
   email, domain and company name. See `src/sales/store.py`.
7. **Minimise personal data in the prospect tracker.** A generic `sales@`
   address is preferred over a named individual's. `named_contact` and
   `decision_maker_role` exist for contacts you already deal with; do not go
   looking for them.

## How to enforce it

The cache prunes itself (`FileCache.prune`). Database retention is not yet
automated — that is a deliberate gap while the dataset is tiny.

**Owner action once you are live:** add a monthly job that deletes rows past
their retention period. A single scheduled SQL statement per table is enough at
this scale; do not build a retention framework for a few thousand rows.

## Deletion requests

### From a company in the feed

1. `python -m src.pipeline suppress --type company --value "<name>" --reason "removal request"`
2. Delete their rows from `opportunities`, `company_matches` and
   `web_enrichment`.
3. Keep the suppression record. Confirm to the requester in writing.

### From a supplier we approached

1. `python -m src.admin prospects opt-out --prospect-id <id> --reason "<what they said>"`

   This is one command on purpose. It sets their status to `OPTED_OUT`, and
   adds them to `prospect_suppressions` by email, domain **and** company name,
   so they cannot return through a future import under a different spelling.
2. If they ask for erasure rather than just an opt-out, clear the personal
   fields on their `prospect_state` row — contact address, named contact, role,
   reply notes — but **leave the row and the suppression entry in place**: a
   deleted row is a row that gets re-added. Because this half is not in git,
   clearing it actually removes the data.
3. Confirm to the requester in writing.

## Backups

`python -m src.admin backup` exports the live outreach state and the
suppression list from the database, and copies the prospect research, customer
records, feedback and configuration, into one dated folder. Retention
applies to backups too: do not keep a backup longer than the data in it.

See `docs/BACKUP_AND_RECOVERY.md` for what is source of truth for each file.
