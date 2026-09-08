# LaunchTrace Food — four-week historical validation

Generated 2026-09-08T14:44:38.327902+00:00.

## The question this answers

Does UK trade mark activity reliably surface genuinely emerging food brands, early enough and in enough volume, that suppliers would value receiving them as weekly sales opportunities?

## Headline

- Journal weeks processed: **4** (4 completed)
- Good opportunities (HIGH + MEDIUM) in total: **22**
- Average per week: **5.5**
- HIGH: **0** · MEDIUM: **22**
- Band under the currently configured assumptions: **QUESTIONABLE**

> 4-7 per week — thin. Either widen the qualification rules or reconsider the weekly cadence.

This is the evidence, not a verdict. The bands in `config/validation_bands.json` are business assumptions we have not yet tested against a paying supplier, and the run below was made with the enrichment that was actually available.

## Enrichment available for this run

| Stage | Provider |
| --- | --- |
| Company verification | companies_house_bulk |
| Product classification | none |
| Web enrichment | none |

Credentials not present for this run:

- `COMPANIES_HOUSE_API_KEY` — Live Companies House API lookups (the free bulk snapshot provider is used instead)
- `LLM_API_KEY` — LLM-assisted product classification (rule-only mode is used)
- `SEARCH_API_KEY` — Web enrichment (websites, retail presence, maturity)
- `RESEND_API_KEY` — Sending email (HTML is rendered to disk instead)
- `STRIPE_SECRET_KEY` — Live subscription checkout
- `DATABASE_URL` — Hosted PostgreSQL / Supabase (local SQLite is used)

## What limits this result

- Web enrichment did not run (no search provider configured). Without it the pipeline cannot verify whether a brand is already established, so every record is capped below the HIGH band. The HIGH counts here are therefore structurally zero, not a finding about the signal. Connect SEARCH_PROVIDER and SEARCH_API_KEY and re-run to get the real HIGH/MEDIUM split.
- Product classification ran on deterministic rules only (no LLM key configured). That is more conservative than the full classifier and will under-detect some packaged-food filings.
- These weeks came from the IPO Open Data release rather than the weekly journal XML. The Open Data release does not publish goods and services text, so product categorisation relied on Nice class, Companies House SIC codes and the brand name. The weekly journal XML does carry goods text, so live weekly runs have strictly more evidence than this validation did.

## Week by week

| Week | Journal | Published | Parsed | Food class | Packaged food | UK corporate | CH matched | Emerging | HIGH | MEDIUM | Suppressed |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2018-001 | 2018-01-05 | 857 | 100 | 41 | 25 | 28 | 29 | 0 | 3 | 26 |
| 2 | 2018-002 | 2018-01-12 | 1333 | 161 | 65 | 47 | 40 | 50 | 0 | 6 | 44 |
| 3 | 2018-003 | 2018-01-19 | 1627 | 177 | 60 | 36 | 44 | 41 | 0 | 7 | 34 |
| 4 | 2018-004 | 2018-01-26 | 1502 | 176 | 71 | 51 | 53 | 53 | 0 | 6 | 47 |

## Is the signal thin, or is the threshold high?

The pipeline scored every emerging-brand candidate; the delivery threshold then decides how many reach a customer. This table separates those two things. If volume needs to increase, the honest lever is the threshold in `config/scoring.json`, and the cost of lowering it is weaker leads.

Score distribution across all scored candidates:

| Score band | Records |
| --- | ---: |
| 80+ | 0 |
| 70-79 | 10 |
| 60-69 | 12 |
| 50-59 | 10 |
| 40-49 | 11 |
| below 40 | 130 |

Opportunities per week at different delivery thresholds:

| Minimum score | Total over the period | Per week |
| ---: | ---: | ---: |
| 75 | 7 | 1.75 |
| 70 | 10 | 2.5 |
| 65 | 14 | 3.5 |
| 60 | 22 | 5.5  ← current MEDIUM threshold |
| 55 | 29 | 7.25 |
| 50 | 32 | 8.0 |
| 45 | 35 | 8.75 |
| 40 | 43 | 10.75 |

## Why records were rejected

**Week 1 — 2018-001**

- `no_food_class`: 676
- `supporting_class_only`: 41
- `service_only_retail`: 30
- `score_below_band`: 26
- `no_brand_name`: 21
- `service_only_hospitality`: 17
- `pharma_supplement_only`: 15
- `non_uk_applicant`: 12

**Week 2 — 2018-002**

- `no_food_class`: 1025
- `supporting_class_only`: 68
- `service_only_retail`: 53
- `no_brand_name`: 45
- `score_below_band`: 44
- `service_only_hospitality`: 29
- `pharma_supplement_only`: 24
- `company_too_established`: 15

**Week 3 — 2018-003**

- `no_food_class`: 1302
- `supporting_class_only`: 61
- `service_only_retail`: 45
- `service_only_hospitality`: 42
- `no_brand_name`: 38
- `score_below_band`: 34
- `non_uk_applicant`: 28
- `pharma_supplement_only`: 23

**Week 4 — 2018-004**

- `no_food_class`: 1169
- `supporting_class_only`: 71
- `no_brand_name`: 54
- `service_only_retail`: 49
- `score_below_band`: 47
- `pharma_supplement_only`: 30
- `service_only_hospitality`: 29
- `company_too_established`: 18

## Files

- `week_1.csv` … `week_N.csv` — the deliverable opportunities for each journal week
- `top_opportunities.csv` — the strongest opportunities across all weeks, sorted by score
- `rejections.csv` — every record the funnel dropped, with the stage and reason
- `4_week_summary.json` — the machine-readable version of this report
- `runs/<journal>/qa_report.json` — the per-week QA report

## How to read this

The funnel is deliberately narrow. Most of the weekly journal is not food, most food-class filings are not packaged consumer products, and most packaged-food filings come from companies that are already established. The number that matters commercially is the last column pair: how many brands a supplier's sales team could actually act on in a week.

Nothing here should be read as proof that suppliers will pay. It shows whether the raw material exists. The next test is qualitative: send `top_opportunities.csv` to real suppliers and ask whether these are companies they would want to reach.
