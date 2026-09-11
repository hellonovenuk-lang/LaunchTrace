#!/usr/bin/env python
"""Run a journal against recorded web evidence.

The weekly pipeline buys its web evidence from a search API, which returns
something different every week. That makes it useless for two things this
project needs: an audit somebody can re-check next month, and a before/after
comparison that isolates a code change from the web moving underneath it.

So this replays ``data/web_evidence/<journal>.json`` -- real results, gathered
once and kept -- instead of calling the provider. Everything else is the
ordinary pipeline.

    python scripts/replay_validation.py 2026-036 --out reports/validation/current/after

Queries are matched exactly. A query with no recorded evidence returns nothing,
which is the honest answer: we did not look that one up.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.enrich.providers.base import SearchProvider, SearchResult  # noqa: E402
from src.enrich.web import WebEnricher  # noqa: E402
from src.parse.normalise import normalise_text  # noqa: E402
from src.pipeline_core import Pipeline  # noqa: E402
from src.settings import get_settings  # noqa: E402


class RecordedSearchProvider(SearchProvider):
    """Replays recorded results, keyed by the exact query the enricher issues."""

    name = "recorded"
    available = True

    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.data = {normalise_text(k): v for k, v in raw.items()}
        self.misses: list[str] = []

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        key = normalise_text(query)
        hits = self.data.get(key)
        if hits is None:
            self.misses.append(query)
            return []
        return [
            SearchResult(
                title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("snippet", "")
            )
            for r in hits[:limit]
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", help="Journal number, e.g. 2026-036")
    parser.add_argument("--out", required=True, help="Directory to write run outputs into")
    parser.add_argument(
        "--evidence", help="Evidence file (default data/web_evidence/<journal>.json)"
    )
    args = parser.parse_args()

    evidence = (
        Path(args.evidence)
        if args.evidence
        else (REPO_ROOT / "data" / "web_evidence" / f"{args.journal}.json")
    )
    if not evidence.exists():
        print(f"No recorded evidence at {evidence}")
        return 1

    settings = get_settings()
    provider = RecordedSearchProvider(evidence)
    pipeline = Pipeline(
        settings=settings,
        web=WebEnricher(provider=provider, settings=settings),
        output_dir=Path(args.out),
    )
    result = pipeline.run(journal_number=args.journal)

    payload = json.loads(result.model_dump_json())
    out = Path(args.out) / args.journal
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")

    counts = result.counts
    print(f"{args.journal}: {result.status.value}")
    print(
        f"  raw {counts.raw_records}  candidates {counts.packaged_food_candidates}  "
        f"CH {counts.company_matched}  enriched {counts.web_enriched}"
    )
    print(f"  HIGH {counts.high}  MEDIUM {counts.medium}  suppressed {counts.suppressed}")
    print(f"  searches with no recorded evidence: {len(provider.misses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
