# Recorded web evidence

Live web search results, gathered on 11 September 2026 for every journal
2026-036 and 2026-037 candidate that reached the web-enrichment stage with a
score high enough to be able to reach the delivery bands.

They are recorded rather than re-fetched for two reasons. First, the precision
audit in `reports/validation/CURRENT_PRECISION_AUDIT.md` has to be reviewable
later, and a search API returns different results every week. Second, the
before/after comparison in that audit has to isolate the code changes: running
the old and new pipelines against identical evidence is the only way to say
that a record moved band because of the correction rather than because the web
moved underneath it.

Each file maps the exact query the enricher issues to the results it received.
`scripts/replay_validation.py` replays them.

Recorded through a general web search rather than the configured Tavily
provider, because this validation environment holds no `SEARCH_API_KEY`. The
results are genuine and current; the retrieval channel differs, and the audit
says so.
