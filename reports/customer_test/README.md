# Customer-test workbooks

The customer-facing weekly deliverable, generated for review. These are the
files a subscriber would actually open, built from real LaunchTrace output.

**Generated and not sent.** No outreach of any kind was sent, nothing was
deployed, and no prospect or customer has seen any of this.

## What is here

`2026-037/` — journal week 2026-037, published 11 September 2026.

- `LaunchTrace_Food_Digimock_2026-037.xlsx` — the workbook, for Digimock
  (prospect `P009`, flexible packaging). Three tabs: WEEKLY BRIEF,
  OPPORTUNITIES, HOW TO USE.
- `LaunchTrace_Food_Digimock_2026-037_preview.pdf` — the same workbook printed,
  so it can be skimmed without Excel. The PDF is a preview only; the workbook is
  the deliverable, and the filters, dropdown and links only work there.

## How it was produced

```bash
python3 scripts/generate_customer_workbook.py \
    --prospect P009 \
    --opportunities reports/validation/final/2026-037/opportunities.csv
```

The qualified set is the existing supplier match from `src/sales/matching.py`,
used exactly as it comes back: 9 of the week's 14 opportunities are relevant to
a flexible packaging supplier, and all 9 are in the workbook.

Which of those the brief leads with is a separate, presentation-only decision.
`config/customer_highlights.json` records the reviewed selection for this week
— kWh Coffee, BULLITT and CHAI MAMA — and those three carry TOP MATCH. Any
further card comes from the same qualified set on supplier fit, so it reads as
secondary. Weeks with no reviewed entry fall back to the top of the fit order.

TOP MATCH deliberately does not mean "has a HIGH internal band". Sami's
Superfoods is HIGH and stays a qualified STRONG match, but a five-year-old
company already selling direct is a weaker launch example for a short-run
packaging supplier than a first-run energy bar.

Nothing in the generation path scores, qualifies, re-ranks or filters anything.
`src/deliver/customer_narrative.py` translates stored fields into customer
language and `src/deliver/customer_workbook.py` lays them out; the wording rules
live in `config/customer_report.json`.

## Why it is separate from the validation reports

`reports/validation/` is the evidence that the pipeline works. This directory is
the product a customer receives. The internal QA report and full run record for
the same week stay under `reports/validation/final/2026-037/`, because they are
not customer-facing.
