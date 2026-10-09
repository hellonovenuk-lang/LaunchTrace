#!/usr/bin/env python
"""Check the Phase 1 stability claim against two snapshots.

    python scripts/stability_snapshot.py --out /tmp/after_p1.json
    python scripts/verify_phase1_stability.py reports/stability/baseline.json /tmp/after_p1.json

Claims checked:
1. every ``*/re_run`` entry of AFTER equals its ``*/first_run`` entry;
2. every ``*/first_run`` entry of AFTER equals BASELINE, except the leads
   listed in EXPECTED_FIRST_RUN_CHANGES (DECISIONS.md D-104), and for those
   the only change is gaining ``first_trademark_for_applicant``.
Exit 0 and a statement on success; exit 1 listing every violation otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# (case, trademark number) -> why it may change. See DECISIONS.md D-104.
EXPECTED_FIRST_RUN_CHANGES = {
    ("open_data_2018-01-05/first_run", "UK00003272909"): (
        "Bunk Group Limited also filed in 2026-037, which the harness processes before "
        "2018-001; the old code counted that future filing as 'already known'."
    ),
}


def verify(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    if set(before) != set(after):
        problems.append(f"case sets differ: {sorted(set(before) ^ set(after))}")
    for case in sorted(after):
        if case.endswith("/re_run"):
            first = case.replace("/re_run", "/first_run")
            if after[case] != after.get(first):
                problems.append(f"{case} differs from {first}")
            continue
        b, a = before.get(case), after[case]
        if b == a:
            continue
        for key in ("status", "raw_records", "high", "medium", "deliverable"):
            if b[key] != a[key]:
                problems.append(f"{case}: {key} {b[key]} -> {a[key]}")
        bl = {lead["dedupe_key"]: lead for lead in b["leads"]}
        al = {lead["dedupe_key"]: lead for lead in a["leads"]}
        if set(bl) != set(al):
            problems.append(f"{case}: lead sets differ")
        for key in sorted(set(bl) & set(al)):
            x, y = bl[key], al[key]
            if x == y:
                continue
            allowed = (case, x["trademark_number"]) in EXPECTED_FIRST_RUN_CHANGES
            gained = set(y["reason_keys"]) - set(x["reason_keys"])
            lost = set(x["reason_keys"]) - set(y["reason_keys"])
            other = {f for f in x if f not in ("score", "reason_keys") and x[f] != y[f]}
            if allowed and gained == {"first_trademark_for_applicant"} and not lost and not other:
                notes.append(
                    f"{case}: {x['trademark_number']} ({x['brand']}) gains "
                    f"first_trademark_for_applicant, score {x['score']} -> {y['score']}, "
                    f"band {y['band']} unchanged -- "
                    + EXPECTED_FIRST_RUN_CHANGES[(case, x["trademark_number"])]
                )
            else:
                problems.append(f"{case}: {x['trademark_number']} changed unexpectedly")
    return problems, notes


def main() -> int:
    before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    after = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    problems, notes = verify(before, after)
    if problems:
        print("FAILED:")
        print("\n".join(f"  {p}" for p in problems))
        return 1
    reruns = sorted(c for c in after if c.endswith("/re_run"))
    print(f"VERIFIED: all {len(reruns)} re_run entries are identical to their first_run entries:")
    for case in reruns:
        print(f"  {case} == {case.replace('/re_run', '/first_run')}")
    firsts = sorted(c for c in after if c.endswith("/first_run"))
    unchanged = [c for c in firsts if before[c] == after[c]]
    print(
        f"VERIFIED: {len(unchanged)} of {len(firsts)} first_run entries are identical to the baseline."
    )
    for note in notes:
        print(f"  expected (DECISIONS.md D-104): {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
