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

## Rules that do not change

1. **Never store directors' home addresses**, dates of birth, persons with
   significant control, or officer records. The Companies House bulk index is
   built from corporate fields only.
2. **Never store raw IP addresses.** The sample form stores a salted hash, used
   solely for rate limiting.
3. **Never delete a suppression record** in order to re-contact someone.
4. **Never commit real credentials.** `.env` is git-ignored; `.env.example`
   holds names only.

## How to enforce it

The cache prunes itself (`FileCache.prune`). Database retention is not yet
automated — that is a deliberate gap while the dataset is tiny.

**Owner action once you are live:** add a monthly job that deletes rows past
their retention period. A single scheduled SQL statement per table is enough at
this scale; do not build a retention framework for a few thousand rows.

## Deletion requests

On a request from a company or individual:

1. `python -m src.pipeline suppress --type company --value "<name>" --reason "removal request"`
2. Delete their rows from `opportunities`, `company_matches` and
   `web_enrichment`.
3. Keep the suppression record. Confirm to the requester in writing.
