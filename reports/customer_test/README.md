# Customer test packs

Outreach prepared for the owner to review and approve. **Nothing here has been
sent, and nothing in this repository can send it** — there is no transport in the
outreach module, and `tests/test_outreach.py::TestNoSendSafeguards` enforces it.

| Path | What |
| --- | --- |
| `2026-037/OPERATOR_REVIEW.md` | Start here. All five packs, why each prospect and each lead was chosen, the pre-send checks, and the next action. |
| `2026-037/<PROSPECT_ID>/preview.md` | The prospect-specific sales intelligence behind that pack |
| `2026-037/<PROSPECT_ID>/email_1.md` | First contact, ready to send by hand |
| `2026-037/<PROSPECT_ID>/email_2.md` | Covering email if they reply asking for more, with the founding offer |
| `2026-037/<PROSPECT_ID>/follow_up.md` | One follow-up. There is no second. |
| `2026-037/<PROSPECT_ID>/reply_sample/` | The supplier-relevant sample to attach at the reply stage |
| `2026-037/selection_record.json` | Which leads qualified for each supplier profile, their fit scores, and which were chosen. Internal. |

No contact address appears anywhere in this directory. Verified addresses live in
`prospect_state` in the application database, with the source recorded against
each one — see `outreach/prospects_schema.md`.

Leads come from `reports/validation/final/sample/2026-037/opportunities.csv`.
Nothing in the pipeline, the scoring or the qualification rules was changed to
produce any of this.
