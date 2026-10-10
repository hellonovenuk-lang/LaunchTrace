# Ingestion applicant filter

**What it does.** Every parsed trade mark record's applicant is classified by
legal form before anything is stored or enriched. Only limited companies and
LLPs are kept. Sole traders, ordinary partnerships and private individuals are
dropped, and only an aggregate count of them is kept.

**Why.** Under UK GDPR and PECR, sole traders and ordinary partnerships are
treated like individuals; limited companies and LLPs are corporate subscribers.
Holding and profiling individuals as sales leads is the highest-risk thing this
product could do, and the buyers want brands with a trading company behind
them anyway. This is a data-minimisation measure.

Rules live in [`config/ingestion_filter.json`](../config/ingestion_filter.json);
the code is [`src/classify/applicant_filter.py`](../src/classify/applicant_filter.py).

## Where it runs

```
fetch journal -> parse -> volume check -> APPLICANT FILTER -> product filter ->
applicant shape -> LLM -> Companies House -> web -> score -> persist
```

`Pipeline.run` calls `_exclude_non_corporate` straight after the volume check
(which must still see the whole journal, so `raw_records` is unchanged). A
dropped record goes no further: it is not sent to Companies House or web search,
does not appear in `opportunities`, `rejections.csv` or the QA report, and
`commands._run_one` never writes it to `trademark_records`.

**Companies House is not available at this point.** Matching runs at stage 4,
later in the pipeline and only for food candidates. It was deliberately not
moved earlier: that would mean looking up every applicant in the journal,
individuals included, which is the opposite of the goal.

## Classification rules, in priority order

| # | Signal | Result |
| --- | --- | --- |
| 1 | `llp_forms` anywhere in the name (`LLP`, `L.L.P.`, `Limited Liability Partnership`) | `llp` |
| 2 | `limited_partnership_forms` (`LP`, `Limited Partnership`) | `partnership` |
| 3 | `limited_company_forms` anywhere (`Ltd`, `Ltd.`, `L.T.D.`, `Limited`, `PLC`, `P.L.C.`, `Public Limited Company`, `CIC`, `Cyf`, `Cyfyngedig`, `CCC`, `Unlimited`) | `limited_company` |
| 4 | `foreign_corporate_forms` as the **last** word(s) (`GmbH`, `Inc`, `SA`, `AB`, `A/S`, ...) | `limited_company` |
| 5 | `trading_as_patterns` (`t/a`, `T / A`, `t.a.`, `trading as`, `trading-as`) | `sole_trader`, or `partnership` if a partnership marker is present or two parties are joined by "and"/"&" before it |
| 6 | `partnership_words` / `partnership_patterns` (`Partners`, `Partnership`, `& Co`, `and Co.`, `& Sons`, `Bros`) | `partnership` |
| 7 | A title (`Mr`, `Mrs`, `Dr`...) or a bare personal name: 2-4 alphabetic words, none of them a business word | `individual` |
| 8 | Anything else | `unknown` |

Matching is case-, spacing- and punctuation-insensitive (dotted initials such as
`L.L.P.` are closed up first). A legal form always wins over a non-corporate
marker, so `Smith & Co Ltd` and `Crumbs Ltd t/a Crumbs` are limited companies.

"Business words" are `business_words` in this config **plus** the existing
`natural_person_applicant.corporate_suffixes` list in `exclusions.json` (foods,
brands, group, trading...). That list is reused rather than duplicated; it could
not serve as the keep-list itself because it mixes legal forms with descriptive
words, and "Smith Foods" is not a company.

## Config

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | `false` restores the old behaviour exactly: every parsed record is stored and processed, and nothing is written to the drop log |
| `keep_types` | `["limited_company", "llp"]` | Legal forms that are stored |
| `drop_unknown` | `true` | Drop applicants that cannot be classified. `false` keeps them (stored and processed as before) |
| pattern lists | see file | Every suffix, word and regex the classifier uses |

## The dropped-count log

Table `ingestion_drop_counts`: one row per (source, journal, reason) with the
journal's publication date, the count, the run id and when it was recorded.
**No names, hashes or any other identifier are stored.** Reprocessing a journal
replaces its rows rather than adding to them; different journals accumulate.
Rows are written only for completed runs with the filter enabled.

```bash
python -m src.pipeline dropped-stats
python -m src.pipeline dropped-stats --from-date 2026-01-01 --to-date 2026-03-31
python -m src.pipeline dropped-stats --from-journal 2026-001 --to-journal 2026-013
```

prints a per-journal table of counts by reason (`individual`, `sole_trader`,
`partnership`, `unknown`) and a total.

## Trade-off: real companies in `unknown`

Because Companies House is not consulted, a company whose journal entry lacks
its legal suffix ("Maria's Kitchen" for Maria's Kitchen Ltd) is `unknown` and,
by default, dropped. This should be rare: a UK company's registered name must
end in Limited/Ltd/PLC/LLP (or the Welsh forms), and the IPO records the
applicant's legal name. The exceptions are exempt companies (mostly charities),
names the applicant typed informally, and bodies such as councils and
universities, none of which are the target customer.

How to measure and tune it:

1. Run `dropped-stats` over a few weeks. If `unknown` is a small fraction of the
   total, leave the default alone.
2. If it is large, set `drop_unknown` to `false` for one run on a copy of the
   database (`--no-db` also works: nothing is stored), and look at how many of
   the formerly unknown applicants match Companies House in the QA report.
3. If they are mostly real companies, either keep `drop_unknown: false` (and
   accept storing those names) or add the missing legal-form variants to
   `limited_company_forms`.

Some non-company names with no business word ("Golden Spoon") read as personal
names and are counted as `individual`. With the default config that only
changes which reason they are logged under.

## Not covered

- The raw journal file in `data/cache/journals/` still contains every applicant
  until it is pruned (30 days, or sooner with `KEEP_RAW_JOURNAL_FILES=false`).
- Rows stored before this filter existed are not removed automatically.
