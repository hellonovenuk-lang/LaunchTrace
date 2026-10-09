# Phase 0 baseline

Recorded 2026-10-09 on Python 3.11.17, commit 81f511e (before any change).

- ruff check: clean · ruff format --check: clean · mypy src: clean (~19s)
- pytest: 647 passed (~17s)
- smoke-test: PASS — 8 raw records, 5 opportunities, 4 HIGH, 1 MEDIUM
- `baseline.json`: `python scripts/stability_snapshot.py --out reports/stability/baseline.json`
  — 10 runs (5 journals × first run + re-run), 550 scored leads.

The baseline re-runs differ from the first runs: every lead loses
`first_trademark_for_applicant` on a re-run (the `known_applicant_names`
bug in `src/commands.py`). That is the one change the Phase 1 fix is expected
to produce.
