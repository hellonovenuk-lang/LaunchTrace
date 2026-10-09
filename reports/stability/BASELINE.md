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

## Phase 1 (foundation)

`phase1_diff.txt` is `--compare baseline.json <phase 1 snapshot>`;
`phase1_verified.txt` is the output of
`scripts/verify_phase1_stability.py baseline.json <phase 1 snapshot>`: every
re_run now equals its first_run, and four of five first_run entries are
unchanged. The fifth (open-data 2018-01-05, BUNK 11 → 22, still SUPPRESS) is
the same bug seen through the harness running 2026 journals before 2018 ones;
see DECISIONS.md D-104.
