"""Backtest report: does the point-in-time score predict a launch?

For every brand with a stored source record, the brand's first opportunity is
re-scored point-in-time (``src.backtest.pit``) and joined to its outcome labels
(``src.backtest.labeller``). For each horizon, with ``unknown`` labels
excluded (and counted):

* **by score band** -- brands, launched, launched rate (= precision of the
  band) with a Wilson interval, share of all launches (= recall), and the
  cumulative "score at or above this band" precision / recall;
* **by indicator** -- support, launched rate when fired and when not fired
  (Wilson intervals), precision, recall and lift over the base rate;
* **weight suggestions** -- a direction and a capped magnitude from the lift,
  only when both groups have enough support and their intervals do not
  overlap. Suggestions are advice; nothing here changes config/scoring.json.

Writes ``<out>/<YYYY-MM-DD>.md`` and ``.json``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.backtest.labeller import LAUNCHED, NOT_LAUNCHED, UNKNOWN
from src.backtest.pit import PitPolicy, PitScorer
from src.brands import journal_sort_key
from src.db.tables import Brand, Observation, OpportunityRow, Outcome, TrademarkRecordRow
from src.settings import REPO_ROOT, load_config


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a proportion; None when there is no data."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _ratio(a: int, b: int) -> float | None:
    return a / b if b else None


@dataclass
class BacktestRow:
    brand_id: int
    brand_uid: str
    journal_number: str
    trademark_number: str
    filing_date: date
    cutoff: date
    pit_score: int
    pit_band: str
    fired: list[str]
    labels: dict[int, str] = field(default_factory=dict)
    live_score: int | None = None
    live_band: str | None = None


@dataclass
class DatasetStats:
    brands: int = 0
    scored: int = 0
    skipped: dict[str, int] = field(default_factory=dict)


def build_dataset(
    session: Session,
    horizons: list[int],
    labeller_version: str,
    scorer: PitScorer | None = None,
) -> tuple[list[BacktestRow], DatasetStats]:
    """One row per brand: its first opportunity, scored point-in-time, with its labels."""
    scorer = scorer or PitScorer()
    stats = DatasetStats()
    rows: list[BacktestRow] = []

    def skip(reason: str) -> None:
        stats.skipped[reason] = stats.skipped.get(reason, 0) + 1

    for brand in session.execute(select(Brand).order_by(Brand.id)).scalars():
        stats.brands += 1
        opps = list(
            session.execute(
                select(OpportunityRow).where(OpportunityRow.brand_id == brand.id)
            ).scalars()
        )
        if not opps:
            skip("no opportunity linked")
            continue
        first_journal = min((o.journal_number for o in opps), key=journal_sort_key)
        best = None
        best_opp: OpportunityRow | None = None
        for opp in sorted(
            (o for o in opps if o.journal_number == first_journal),
            key=lambda o: o.trademark_number,
        ):
            record = session.execute(
                select(TrademarkRecordRow).where(
                    TrademarkRecordRow.journal_number == opp.journal_number,
                    TrademarkRecordRow.trademark_number == opp.trademark_number,
                )
            ).scalar_one_or_none()
            if record is None:
                continue
            scored = scorer.score(session, brand.id, record)
            if scored is not None and (best is None or scored.value > best.value):
                best, best_opp = scored, opp
        if best is None:
            skip("no stored source record with a filing date")
            continue
        labels: dict[int, str] = {}
        for outcome in session.execute(
            select(Outcome).where(
                Outcome.brand_id == brand.id, Outcome.labeller_version == labeller_version
            )
        ).scalars():
            labels[outcome.horizon_months] = outcome.label
        for h in horizons:
            labels.setdefault(h, UNKNOWN)
        stats.scored += 1
        rows.append(
            BacktestRow(
                brand_id=brand.id,
                brand_uid=brand.brand_uid,
                journal_number=best.journal_number,
                trademark_number=best.trademark_number,
                filing_date=best.filing_date,
                cutoff=best.cutoff,
                pit_score=best.value,
                pit_band=best.band,
                fired=best.fired,
                labels=labels,
                live_score=best_opp.launchtrace_score if best_opp else None,
                live_band=best_opp.score_band if best_opp else None,
            )
        )
    return rows, stats


def _indicator_catalogue(scoring: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for group in ("positive_indicators", "negative_indicators", "domain_indicators"):
        for ind in scoring.get(group, []):
            out.append({"key": ind["key"], "group": group, "weight": int(ind.get("weight", 0))})
    return out


def _suggest(
    ind: dict[str, Any],
    fired: dict[str, Any],
    not_fired: dict[str, Any],
    lift: float | None,
    cfg: dict[str, Any],
    evaluable: bool,
) -> dict[str, Any]:
    min_support = int(cfg.get("min_support_per_indicator", 10))
    if not evaluable:
        return {"action": "cannot assess", "change": 0, "why": "not evaluable point-in-time"}
    if fired["n"] < min_support or not_fired["n"] < min_support:
        return {
            "action": "hold",
            "change": 0,
            "why": f"insufficient support (need {min_support} labelled brands fired and not fired)",
        }
    ci_f, ci_n = fired["ci"], not_fired["ci"]
    if ci_f is None or ci_n is None or not (ci_f[0] > ci_n[1] or ci_n[0] > ci_f[1]):
        return {
            "action": "hold",
            "change": 0,
            "why": "95% intervals overlap: no clear difference",
        }
    if not lift:
        return {
            "action": "decrease",
            "change": -int(cfg.get("max_suggested_change", 8)),
            "why": "no launches when fired",
        }
    cap = int(cfg.get("max_suggested_change", 8))
    change = round(float(cfg.get("points_per_log_lift", 8)) * math.log(lift))
    change = max(-cap, min(cap, change))
    action = "increase" if change > 0 else "decrease" if change < 0 else "hold"
    return {
        "action": action,
        "change": change,
        "why": f"launched rate {fired['rate']:.0%} when fired vs {not_fired['rate']:.0%} when not",
    }


def compute_report(
    rows: list[BacktestRow],
    horizons: list[int],
    *,
    bands: list[dict[str, Any]],
    indicators: list[dict[str, Any]],
    not_evaluable: dict[str, str],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """All the numbers, per horizon. Pure: a synthetic dataset gives known answers."""
    z = float(cfg.get("wilson_z", 1.96))
    min_labelled = int(cfg.get("min_labelled_for_conclusions", 30))
    out: dict[str, Any] = {"horizons": {}}
    ordered_bands = sorted(bands, key=lambda b: int(b["min"]), reverse=True)
    for h in horizons:
        labelled = [r for r in rows if r.labels.get(h) in (LAUNCHED, NOT_LAUNCHED)]
        unknown = sum(1 for r in rows if r.labels.get(h, UNKNOWN) == UNKNOWN)
        positives = [r for r in labelled if r.labels[h] == LAUNCHED]
        n, total_pos = len(labelled), len(positives)
        base_rate = _ratio(total_pos, n)

        band_rows = []
        for band in ordered_bands:
            lo, hi = int(band["min"]), int(band["max"])
            members = [r for r in labelled if lo <= r.pit_score <= hi]
            at_or_above = [r for r in labelled if r.pit_score >= lo]
            launched = sum(1 for r in members if r.labels[h] == LAUNCHED)
            launched_above = sum(1 for r in at_or_above if r.labels[h] == LAUNCHED)
            band_rows.append(
                {
                    "band": band["key"],
                    "range": [lo, hi],
                    "n": len(members),
                    "launched": launched,
                    "precision": _ratio(launched, len(members)),
                    "ci": wilson_interval(launched, len(members), z),
                    "recall": _ratio(launched, total_pos),
                    "at_or_above": {
                        "n": len(at_or_above),
                        "launched": launched_above,
                        "precision": _ratio(launched_above, len(at_or_above)),
                        "recall": _ratio(launched_above, total_pos),
                    },
                }
            )

        ind_rows = []
        for ind in indicators:
            key = ind["key"]
            fired_rows = [r for r in labelled if key in r.fired]
            other = [r for r in labelled if key not in r.fired]
            lf = sum(1 for r in fired_rows if r.labels[h] == LAUNCHED)
            lo_ = sum(1 for r in other if r.labels[h] == LAUNCHED)
            fired: dict[str, Any] = {
                "n": len(fired_rows),
                "launched": lf,
                "rate": _ratio(lf, len(fired_rows)),
                "ci": wilson_interval(lf, len(fired_rows), z),
            }
            not_fired: dict[str, Any] = {
                "n": len(other),
                "launched": lo_,
                "rate": _ratio(lo_, len(other)),
                "ci": wilson_interval(lo_, len(other), z),
            }
            rate_fired = _ratio(lf, len(fired_rows))
            lift = rate_fired / base_rate if rate_fired is not None and base_rate else None
            evaluable = key not in not_evaluable
            ind_rows.append(
                {
                    **ind,
                    "evaluable": evaluable,
                    "not_evaluable_reason": not_evaluable.get(key),
                    "support": len(fired_rows),
                    "fired_all_scored": sum(1 for r in rows if key in r.fired),
                    "fired": fired,
                    "not_fired": not_fired,
                    "precision": fired["rate"],
                    "recall": _ratio(lf, total_pos),
                    "lift": lift,
                    "suggestion": _suggest(ind, fired, not_fired, lift, cfg, evaluable),
                }
            )

        out["horizons"][str(h)] = {
            "brands": len(rows),
            "labelled": n,
            "launched": total_pos,
            "not_launched": n - total_pos,
            "unknown": unknown,
            "base_rate": base_rate,
            "base_rate_ci": wilson_interval(total_pos, n, z),
            "small_sample": n < min_labelled,
            "bands": band_rows,
            "indicators": ind_rows,
        }
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _ci(ci: list[float] | tuple[float, float] | None) -> str:
    return "-" if not ci else f"{ci[0]:.0%}–{ci[1]:.0%}"


def render_markdown(payload: dict[str, Any]) -> str:
    meta = payload["meta"]
    lines = [
        f"# Backtest report — {meta['date']}",
        "",
        f"Generated {meta['generated_at']} from `{meta['database']}`. "
        f"PIT window: {meta['pit_window_days']} day(s) after filing. "
        f"Labeller v{meta['labeller_version']}, labels as of {meta['labels_as_of'] or '-'}.",
        "",
    ]
    horizons = payload["results"]["horizons"]
    small = any(h["small_sample"] for h in horizons.values())
    if small:
        lines += [
            "> **SMALL SAMPLE — DO NOT TUNE WEIGHTS FROM THIS REPORT.** "
            f"Fewer than {meta['min_labelled_for_conclusions']} brands have a known outcome "
            "at one or more horizons. Every rate below has a wide interval (or none), and "
            "every weight suggestion is 'hold'. Labels mature as the weekly run and the "
            "rescan job add observations over time.",
            "",
        ]
    if meta.get("note"):
        lines += ["## About this run", "", str(meta["note"]), ""]
    if all(h["labelled"] == 0 for h in horizons.values()):
        lines += [
            "> **No brand has a known outcome yet, so this report measures nothing.** "
            "Precision, recall and lift are all undefined ('-'). The tables are produced so "
            "the pipeline is proven end to end; read them again once labels have matured.",
            "",
        ]
    lines += [
        "## Data",
        "",
        f"* Journals in the database: {', '.join(meta['journals']) or 'none'}",
        f"* Brands: {payload['dataset']['brands']}; scored point-in-time: "
        f"{payload['dataset']['scored']}"
        + (
            "; skipped: "
            + ", ".join(f"{k} ({v})" for k, v in payload["dataset"]["skipped"].items())
            if payload["dataset"]["skipped"]
            else ""
        ),
        "",
        "| horizon | labelled | launched | not launched | unknown (excluded) | base rate (95% CI) |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for h, r in horizons.items():
        lines.append(
            f"| +{h} months | {r['labelled']} | {r['launched']} | {r['not_launched']} | "
            f"{r['unknown']} | {_pct(r['base_rate'])} ({_ci(r['base_rate_ci'])}) |"
        )
    reasons = meta.get("unknown_reasons") or {}
    if reasons:
        lines += ["", "Why labels are unknown:", ""]
        for h, counts in reasons.items():
            lines.append(f"* +{h} months: " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    lines += [
        "",
        "## Evidence in the database",
        "",
        f"* Brands with a Companies House match: {meta.get('brands_with_company_match', 0)}",
        "",
        "| observation source | observations | point-in-time safe |",
        "| --- | ---: | ---: |",
    ]
    for source, counts in sorted((meta.get("observations_by_source") or {}).items()):
        lines.append(f"| {source} | {counts['observations']} | {counts['pit_safe']} |")
    lines += [
        "",
        "## Score distribution: point-in-time vs as delivered",
        "",
        "The live column is the score the ingest run stored for the same mark (no web "
        "search in a backtest ingest); the PIT column uses only PIT-safe facts.",
        "",
        "| band | PIT score | stored live score |",
        "| --- | ---: | ---: |",
    ]
    live = meta.get("live_band_counts") or {}
    for band, count in meta["pit_band_counts"].items():
        lines.append(f"| {band} | {count} | {live.get(band, 0)} |")

    for h, r in horizons.items():
        lines += [
            "",
            f"## +{h} months: precision and recall by PIT score band",
            "",
            "| band | scores | labelled | launched | precision (95% CI) | recall | "
            "≥ band precision | ≥ band recall |",
            "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |",
        ]
        for b in r["bands"]:
            lines.append(
                f"| {b['band']} | {b['range'][0]}–{b['range'][1]} | {b['n']} | {b['launched']} | "
                f"{_pct(b['precision'])} ({_ci(b['ci'])}) | {_pct(b['recall'])} | "
                f"{_pct(b['at_or_above']['precision'])} | {_pct(b['at_or_above']['recall'])} |"
            )
        lines += [
            "",
            f"## +{h} months: by indicator",
            "",
            "| indicator | weight | fired (all scored) | support (labelled, fired) | "
            "launched when fired (95% CI) | when not fired (95% CI) | precision | recall | lift |",
            "| --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |",
        ]
        for i in r["indicators"]:
            if not i["evaluable"]:
                continue
            lines.append(
                f"| `{i['key']}` | {i['weight']} | {i['fired_all_scored']} | {i['support']} | "
                f"{_pct(i['fired']['rate'])} ({_ci(i['fired']['ci'])}) | "
                f"{_pct(i['not_fired']['rate'])} ({_ci(i['not_fired']['ci'])}) | "
                f"{_pct(i['precision'])} | {_pct(i['recall'])} | "
                f"{_num(i['lift'])} |"
            )
        lines += [
            "",
            f"### Weight suggestions (+{h} months) — advice only, never applied",
            "",
            "| indicator | current weight | suggestion | change | why |",
            "| --- | ---: | --- | ---: | --- |",
        ]
        for i in r["indicators"]:
            if not i["evaluable"]:
                continue
            s = i["suggestion"]
            lines.append(
                f"| `{i['key']}` | {i['weight']} | {s['action']} | {s['change']:+d} | {s['why']} |"
            )
    first = next(iter(horizons.values()), None)
    if first:
        lines += [
            "",
            "## Indicators a point-in-time backtest cannot assess",
            "",
            "They depend on inputs that describe the present (web search, today's "
            "Companies House record, the live website), so the PIT score never fires them.",
            "",
        ]
        for i in first["indicators"]:
            if not i["evaluable"]:
                lines.append(f"* `{i['key']}` — {i['not_evaluable_reason']}")
    lines += ["", "## Caveats", ""] + [f"* {c}" for c in meta["caveats"]]
    return "\n".join(lines) + "\n"


CAVEATS = [
    "Labels come only from observations already stored, observed on or before the "
    "horizon end (positive) or within the tolerance after it (negative). Brands ingested "
    "from past journals have no observations from their horizon windows, so they stay "
    "'unknown' until rescans that fall inside a window accumulate.",
    "'Launched' includes brands that were already trading at filing: no point-in-time "
    "record of a pre-filing website exists, so the label cannot tell 'launched after "
    "filing' from 'already launched'.",
    "Survivorship: only filings that reached the opportunity stage get brands; rejected "
    "records are not in the population.",
    "The PIT score excludes web, current Companies House and live-site inputs, so it is "
    "lower and flatter than a live score (it is capped below HIGH, as any score without "
    "web evidence is).",
    "'First trade mark for this applicant' is judged against the journals in the "
    "database only; the oldest ingested journal sees every applicant as new.",
    "The major-brand-owner list and the product taxonomy are today's configuration.",
]


def generate_report(
    session: Session,
    *,
    out_dir: Path | None = None,
    today: date | None = None,
    database_label: str = "",
    scorer: PitScorer | None = None,
    note: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Build the dataset, compute, and write ``<out>/<date>.md`` and ``.json``."""
    bt = load_config("backtest.json")
    cfg = bt["report"]
    outcomes_cfg = bt["outcomes"]
    scoring = load_config("scoring.json")
    policy = PitPolicy.load(bt["pit"])
    horizons = [int(h) for h in outcomes_cfg.get("horizons_months", [3, 6])]
    version = str(outcomes_cfg.get("labeller_version", "1"))
    today = today or date.today()
    out_dir = out_dir or (REPO_ROOT / str(cfg.get("out_dir", "reports/backtest")))

    rows, stats = build_dataset(
        session, horizons, version, scorer=scorer or PitScorer(policy=policy)
    )
    results = compute_report(
        rows,
        horizons,
        bands=scoring["bands"],
        indicators=_indicator_catalogue(scoring),
        not_evaluable=policy.not_evaluable,
        cfg=cfg,
    )
    outcome_rows = list(
        session.execute(select(Outcome).where(Outcome.labeller_version == version)).scalars()
    )
    reasons: dict[str, dict[str, int]] = {}
    for o in outcome_rows:
        if o.label == UNKNOWN:
            why = str((o.criteria_met or {}).get("_reason", "")).split(" (")[0] or "unknown"
            bucket = reasons.setdefault(str(o.horizon_months), {})
            bucket[why] = bucket.get(why, 0) + 1
    as_of_dates = sorted({o.as_of_date.isoformat() for o in outcome_rows if o.as_of_date})
    band_counts: dict[str, int] = {b["key"]: 0 for b in scoring["bands"]}
    live_counts: dict[str, int] = {b["key"]: 0 for b in scoring["bands"]}
    for r in rows:
        band_counts[r.pit_band] = band_counts.get(r.pit_band, 0) + 1
        if r.live_band:
            live_counts[r.live_band] = live_counts.get(r.live_band, 0) + 1
    evidence: dict[str, dict[str, int]] = {}
    for source, pit, count in session.execute(
        select(Observation.source, Observation.point_in_time_safe, func.count()).group_by(
            Observation.source, Observation.point_in_time_safe
        )
    ):
        bucket = evidence.setdefault(str(source), {"observations": 0, "pit_safe": 0})
        bucket["observations"] += int(count)
        if pit:
            bucket["pit_safe"] += int(count)
    matched_brands = session.execute(
        select(func.count()).select_from(Brand).where(Brand.company_number.is_not(None))
    ).scalar_one()
    journals = sorted(
        {r.journal_number for r in session.execute(select(TrademarkRecordRow.journal_number))},
        key=journal_sort_key,
    )
    payload: dict[str, Any] = {
        "meta": {
            "date": today.isoformat(),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "database": database_label,
            "journals": journals,
            "pit_window_days": policy.window_days,
            "pit_allowed_signals": sorted(policy.allowed_signals),
            "labeller_version": version,
            "labels_as_of": as_of_dates[-1] if as_of_dates else None,
            "horizons_months": horizons,
            "min_labelled_for_conclusions": int(cfg.get("min_labelled_for_conclusions", 30)),
            "unknown_reasons": reasons,
            "pit_band_counts": band_counts,
            "live_band_counts": live_counts,
            "observations_by_source": evidence,
            "brands_with_company_match": int(matched_brands),
            "note": note,
            "caveats": CAVEATS,
            "weights_applied": False,
        },
        "dataset": {"brands": stats.brands, "scored": stats.scored, "skipped": stats.skipped},
        "results": results,
        "rows": [
            {
                "brand_uid": r.brand_uid,
                "journal_number": r.journal_number,
                "trademark_number": r.trademark_number,
                "filing_date": r.filing_date.isoformat(),
                "pit_cutoff": r.cutoff.isoformat(),
                "pit_score": r.pit_score,
                "pit_band": r.pit_band,
                "fired": r.fired,
                "labels": {str(k): v for k, v in r.labels.items()},
            }
            for r in rows
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}.json"
    md_path = out_dir / f"{today.isoformat()}.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(json.loads(json_path.read_text())), encoding="utf-8")
    return md_path, json_path, payload
