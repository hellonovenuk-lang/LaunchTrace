# LaunchTrace's own customer prospecting

This directory is for **LaunchTrace selling to suppliers**. It has nothing to
do with the product feed, and nothing here is automated.

**No email is ever sent from this directory by any code in this repository.**
The pipeline does not read it, no workflow touches it, and there is no send
command. Outreach is a manual act by the owner, on purpose.

## Files

| File | What it is |
| --- | --- |
| `prospects.csv` | The supplier prospect list, in the schema below |
| `prospects_schema.md` | What each column means and its allowed values |
| `suppressions.csv` | Anyone who has opted out. **Never contact these.** |
| `templates/email_1_first_contact.md` | First approach, with three real examples |
| `templates/email_2_full_sample.md` | Sent after interest |
| `templates/email_3_follow_up.md` | One follow-up, then stop |

## The state of the list as delivered

`prospects.csv` holds **60 real UK companies**, identified by web research
during the build and split roughly as: 23 packaging (flexible, labels,
cartons), 21 contract manufacturers and co-packers, and 16 fulfilment,
distribution and brokerage businesses.

Two columns are deliberately **empty**:

- `generic_contact_email` — an email address cannot be verified from a search
  result, and a guessed address is worse than a blank one. Open each website,
  find the real `sales@` or `enquiries@` address, and paste it in.
- `sales_contact_route` is set to a best guess of `website_form`; correct it
  when you check.

Some rows carry "Verify website before contacting" in `notes`. Those are
companies whose name came from a reliable source but whose exact URL was not
confirmed. Check them before you send anything.

Budget roughly two hours to complete the list. It is the highest-value two
hours available, because `relevance_reason` is already written for each one.

## How to use it

1. Finish `prospects.csv`. Aim for roughly 30 packaging businesses, 20
   contract manufacturers and co-packers, and 10 fulfilment or distribution
   businesses.
2. Run a week of the pipeline and open `reports/runs/<journal>/opportunities.csv`.
3. For each prospect, pick **three real opportunities that genuinely suit what
   they make**. A packaging converter should see snack and bar brands, not
   sauce brands.
4. Paste them into `templates/email_1_first_contact.md` and send it yourself,
   from your own mailbox.
5. Record what happened in the `status` and `reply_status` columns.

## Rules

- **Check `suppressions.csv` before every send.** Anyone in it is off-limits
  permanently.
- Prefer generic business addresses (`sales@`, `enquiries@`) over a named
  individual's address.
- Prioritise limited companies and LLPs — under PECR, direct marketing email to
  corporate subscribers does not require prior consent, but you must identify
  yourself and offer an opt-out every time.
- Every email must say who you are and how to stop hearing from you.
- One follow-up. Then stop.
- Use real examples. Never invent a brand to make the email look better.

See `docs/LEGITIMATE_INTERESTS_ASSESSMENT.md`, Activity B, for the basis on
which this is done.
