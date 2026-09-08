# Prospect list schema

One row per supplier company. `outreach/prospects.csv` uses exactly these
columns, in this order.

| Column | Meaning | Values |
| --- | --- | --- |
| `company_name` | Registered or trading name | text |
| `website` | Their site | URL |
| `supplier_type` | What they supply | `flexible_packaging`, `labels`, `cartons`, `contract_manufacturing`, `copacking`, `distribution`, `brokerage`, `fulfilment`, `marketing`, `other` |
| `relevance_reason` | Why LaunchTrace suits *them* specifically. One line, concrete. | text |
| `generic_contact_email` | Prefer `sales@` or `enquiries@` over a named individual | email |
| `sales_contact_route` | How to reach the sales team | `website_form`, `generic_email`, `phone`, `linkedin`, `unknown` |
| `company_type` | Corporate subscriber status matters for PECR | `ltd`, `llp`, `plc`, `sole_trader`, `partnership`, `unknown` |
| `status` | Where this prospect stands | `not_contacted`, `contacted`, `interested`, `sample_sent`, `subscribed`, `declined`, `no_response`, `suppressed` |
| `sample_sent` | Date the full sample went | `YYYY-MM-DD` or blank |
| `reply_status` | What came back | `none`, `positive`, `neutral`, `negative`, `opt_out` |
| `sample_requested` | Did they ask for it, or did we offer? | `yes`, `no` |
| `subscription_status` | Mirror of the billing state once they convert | `none`, `trialing`, `active`, `cancelled` |
| `notes` | Anything useful. No personal data beyond a business contact name. | text |

## Filling it in

`relevance_reason` is the column that matters. "Makes flexible film for snack
brands, so the snack and bar opportunities in the feed are directly their
market" is useful. "Packaging company" is not — it means you have not checked
whether the feed actually suits them, and the first email will show it.

Leave a cell blank rather than guessing. A wrong email address wastes a
prospect; an empty one just needs five minutes of research.
