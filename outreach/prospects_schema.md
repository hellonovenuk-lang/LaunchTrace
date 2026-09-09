# Prospect tracker schema

One row per supplier company. `outreach/prospects.csv` uses exactly these
columns, in this order. It is the **source of truth** for LaunchTrace's own
sales pipeline: git-tracked, editable in a spreadsheet, and written by only one
piece of code (`src/sales/store.py`).

## Columns

### Identity

| Column | Meaning | Values |
| --- | --- | --- |
| `prospect_id` | Stable id, assigned once and never reused | `P001` … |
| `company_name` | Registered or trading name | text |
| `website` | Their site | URL |
| `companies_house_number` | If you have checked it | text, or blank |
| `company_type` | Corporate subscriber status matters under PECR | `ltd`, `llp`, `plc`, `sole_trader`, `partnership`, `unknown` |

### What they do

| Column | Meaning | Values |
| --- | --- | --- |
| `supplier_category` | The main thing they supply. Drives which leads they are shown. | `flexible_packaging`, `labels`, `cartons`, `contract_manufacturing`, `copacking`, `distribution`, `brokerage`, `fulfilment`, `marketing`, `other` |
| `supplier_subcategory` | Free text detail, e.g. "self-adhesive labels, BRCGS certified" | text |
| `geography` | Where they are or where they sell | text |
| `icp_reason` | **The column that matters.** Why LaunchTrace suits *them* specifically, in one concrete line. | text |
| `products_services` | What they sell, if it needs saying beyond the reason | text |
| `buying_intent_categories` | Which LaunchTrace relevance bands matter to them, best first | pipe-separated, e.g. `labels\|cartons\|flexible_packaging` |
| `company_size_hint` | Only if stated outright somewhere. Not a guess. | text |

### How to reach them

| Column | Meaning | Values |
| --- | --- | --- |
| `contact_route` | How to reach the sales team | `generic_email`, `website_form`, `phone`, `linkedin`, `unknown` |
| `generic_contact_email` | Prefer `sales@` or `enquiries@` over a named individual | email, or blank |
| `named_contact` | Only if you already deal with them. Do not go looking. | text |
| `decision_maker_role` | Only if already known | text |
| `email_source` | **Where the address came from.** "I guessed the pattern" is not a source. | `none`, `website_verified`, `companies_house`, `inbound`, `operator_known` |

### Where they stand

| Column | Meaning | Values |
| --- | --- | --- |
| `status` | Position in the funnel | see lifecycle below |
| `priority` | ICP band, set by `prospects rescore` | `A`, `B`, `C`, `SUPPRESS` |
| `icp_score` | 0–100, from `config/icp_scoring.json` | integer |
| `reply_state` | What came back | `none`, `positive`, `neutral`, `negative`, `opt_out` |
| `opted_out` | Asked not to be contacted. **One-way.** | `true` / `false` |
| `suppression_reason` | Why they are off-limits, in their words if possible | text |
| `notes` | Anything useful. No personal data beyond a business contact. | text |

### Dates

`date_added`, `email_1_sent_date`, `sample_requested_date`, `sample_sent_date`,
`offer_sent_date`, `converted_date`, `follow_up_due_date` — all `YYYY-MM-DD` or
blank. Stamped automatically by `prospects set-status`; safe to correct by hand.

`stripe_customer_id` is filled once they subscribe, so the funnel reconciles
against real revenue.

## Lifecycle

```
RESEARCHED → READY → EMAIL_1_SENT → REPLIED_INTERESTED → SAMPLE_REQUESTED
           → SAMPLE_SENT → OFFER_SENT → SUBSCRIBED
```

Exits at any point: `NOT_NOW`, `NO_RESPONSE`, `OPTED_OUT`, `SUPPRESSED`.

| Status | Means |
| --- | --- |
| `RESEARCHED` | On the list, research not finished |
| `READY` | Contact route checked, ready for a first email |
| `EMAIL_1_SENT` | First contact sent |
| `REPLIED_INTERESTED` | They replied and want to see more |
| `SAMPLE_REQUESTED` | They asked for the full sample |
| `SAMPLE_SENT` | Sample sent |
| `OFFER_SENT` | Subscription offered |
| `SUBSCRIBED` | Paying |
| `NOT_NOW` | Declined for now. Record why. |
| `NO_RESPONSE` | Nothing after one follow-up. Stop. |
| `OPTED_OUT` | Asked not to be contacted. **Never reopened.** |
| `SUPPRESSED` | Off-limits: duplicate, wrong fit, not trading |

Transitions are checked, so the funnel numbers mean what they say. Skipping a
step raises an error rather than corrupting the counts. Move a prospect with:

```bash
python -m src.admin prospects set-status --prospect-id P001 --status READY
```

## Rules

1. **Never invent a contact address.** A wrong address costs the prospect and
   damages your sending reputation; a blank one costs five minutes. Nothing in
   this system will generate one for you, and `email_source` exists so a
   guessed address cannot pass as a checked one.
2. **`icp_reason` is the column that matters.** "Makes flexible film for snack
   brands, so the snack and bar opportunities are directly their market" is
   useful. "Packaging company" means you have not checked whether the feed
   suits them, and the first email will show it.
3. **Leave a cell blank rather than guessing.** The ICP score reads this text;
   invented text produces a confident, wrong priority.
4. **An opt-out is permanent.** `python -m src.admin prospects opt-out` records
   it against email, domain *and* company name, so no future import can bring
   them back under a different spelling.
5. **One business, one row.** Adding a duplicate is refused by name, domain,
   company number and email. `prospects audit` finds any that predate the
   check.

## Checking the list

```bash
python -m src.admin prospects audit
```

Reports duplicates, anyone blocked by the suppression list, rows still needing
research, the priority spread, and how many have a verified contact address.
