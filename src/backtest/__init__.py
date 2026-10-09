"""Backtesting LaunchTrace against what later happened to each brand.

* ``probe``    -- which past journals ipo.gov.uk still serves (polite, capped).
* ``ingest``   -- run past journals through the normal pipeline, without paid
  search, the LLM or the live domain layer, into the persistent database.
* ``pit``      -- the point-in-time policy: a backtest score uses only facts that
  were true (and knowable) at filing, through the real ``LaunchTraceScorer``.
* ``labeller`` -- did the brand launch within 3 / 6 months of filing, from
  observations already stored (never a new search).
* ``report``   -- precision and recall by score band and by indicator.

See docs/BACKTEST.md.
"""
