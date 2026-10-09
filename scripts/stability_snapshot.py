#!/usr/bin/env python
"""Score-stability snapshot: the evidence that a change did not move any lead.

Runs every journal checked into the repository through the real CLI code path
(``src.commands._run_one``: database read of known applicants, pipeline, and
persistence) with network-free providers -- the fixture Companies House
registry, the fixture search provider for the test journal, and the recorded
web evidence in ``data/web_evidence/`` for the 2026 journals. Each journal is
run twice against the same database, because a re-run is exactly where the
``known_applicant_names`` bug showed itself.

    python scripts/stability_snapshot.py --out before.json
    python scripts/stability_snapshot.py --out after.json
    python scripts/stability_snapshot.py --compare before.json after.json

The snapshot is everything a customer could see move: which leads exist, their
score, band, suppression and the reason keys behind the score. Timestamps and
run ids are left out because they change on every run by design.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# (case name, journal source, journal number or None, publication date or None,
#  recorded-evidence file or None for the fixture search provider)
CASES: list[tuple[str, str, str | None, str | None, str | None]] = [
    ("fixture_2025-050", "fixture", "2025-050", None, None),
    ("local_2026-036", "local", "2026-036", None, "data/web_evidence/2026-036.json"),
    ("local_2026-037", "local", "2026-037", None, "data/web_evidence/2026-037.json"),
    ("open_data_2018-01-05", "open_data", None, "2018-01-05", None),
    ("open_data_2018-01-12", "open_data", None, "2018-01-12", None),
]


def _isolate(workdir: Path) -> None:
    """Point the database and cache at a scratch directory, never the real ones."""
    os.environ["DATABASE_URL"] = f"sqlite:///{workdir / 'stability.sqlite'}"
    os.environ["CACHE_DIR"] = str(workdir / "cache")
    os.environ["SEARCH_PROVIDER"] = "none"
    os.environ["SEARCH_API_KEY"] = ""
    os.environ["LLM_PROVIDER"] = "none"
    os.environ["COMPANIES_HOUSE_API_KEY"] = ""
    os.environ["RESEND_API_KEY"] = ""
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ.pop("JOURNAL_LOCAL_DIR", None)


def _snapshot_result(result: Any) -> dict[str, Any]:
    leads = []
    for opp in sorted(result.opportunities, key=lambda o: (o.trademark_number, o.dedupe_key)):
        leads.append(
            {
                "trademark_number": opp.trademark_number,
                "dedupe_key": opp.dedupe_key,
                "brand": opp.brand_name,
                "score": opp.score.value,
                "band": opp.score.band.value,
                "suppressed": opp.suppressed,
                "suppression_reason": opp.suppression_reason,
                "reason_keys": sorted(r.key for r in opp.score.reasons),
                "negative_keys": sorted(r.key for r in opp.score.negative_reasons),
            }
        )
    return {
        "status": result.status.value,
        "raw_records": result.counts.raw_records,
        "high": result.counts.high,
        "medium": result.counts.medium,
        "deliverable": sorted(o.trademark_number for o in result.deliverable),
        "leads": leads,
    }


def build_snapshot(workdir: Path) -> dict[str, Any]:
    _isolate(workdir)

    from src.settings import get_settings

    get_settings.cache_clear()

    import src.commands as commands
    from replay_validation import RecordedSearchProvider
    from src.classify.pipeline import ProductClassifier
    from src.enrich.companies_house import FixtureCompanyRegistry
    from src.enrich.providers import FixtureSearchProvider
    from src.enrich.web import WebEnricher

    real_pipeline = commands.Pipeline
    snapshot: dict[str, Any] = {}

    for name, source, journal, pub, evidence in CASES:
        provider: Any = (
            RecordedSearchProvider(REPO_ROOT / evidence) if evidence else FixtureSearchProvider()
        )

        def factory(settings: Any, source: Any, _provider: Any = provider) -> Any:
            return real_pipeline(
                settings=settings,
                source=source,
                registry=FixtureCompanyRegistry(),
                classifier=ProductClassifier(settings, llm_provider=None),
                web=WebEnricher(provider=_provider, settings=settings),
                output_dir=workdir / "runs",
            )

        commands.Pipeline = factory  # type: ignore[assignment,misc]
        try:
            for attempt in ("first_run", "re_run"):
                args = SimpleNamespace(
                    source=source, command="weekly", max_records=None, force=True
                )
                result = commands._run_one(
                    args,
                    publication_date=date.fromisoformat(pub) if pub else None,
                    journal_number=journal,
                    write_db=True,
                )
                snapshot[f"{name}/{attempt}"] = _snapshot_result(result)
        finally:
            commands.Pipeline = real_pipeline  # type: ignore[misc]
    return snapshot


def compare(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for case in sorted(set(before) | set(after)):
        b, a = before.get(case), after.get(case)
        if b is None or a is None:
            lines.append(f"{case}: present only in {'after' if b is None else 'before'}")
            continue
        for key in ("status", "raw_records", "high", "medium", "deliverable"):
            if b[key] != a[key]:
                lines.append(f"{case}: {key} {b[key]} -> {a[key]}")
        bl = {lead["dedupe_key"]: lead for lead in b["leads"]}
        al = {lead["dedupe_key"]: lead for lead in a["leads"]}
        for key in sorted(set(bl) | set(al)):
            x, y = bl.get(key), al.get(key)
            if x is None or y is None:
                lines.append(f"{case}: lead {key} only in {'after' if x is None else 'before'}")
                continue
            for field in x:
                if x[field] != y.get(field):
                    lines.append(
                        f"{case}: {x['trademark_number']} ({x['brand']}) {field}: "
                        f"{x[field]} -> {y.get(field)}"
                    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="Write a snapshot to this JSON file")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()

    if args.compare:
        before = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        after = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        diff = compare(before, after)
        print("\n".join(diff) if diff else "IDENTICAL: no lead, score, band or reason changed.")
        return 0

    if not args.out:
        parser.error("give --out or --compare")
    with tempfile.TemporaryDirectory(prefix="lt-stability-") as tmp:
        snapshot = build_snapshot(Path(tmp))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
    total = sum(len(v["leads"]) for v in snapshot.values())
    print(f"Wrote {args.out}: {len(snapshot)} runs, {total} scored leads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
