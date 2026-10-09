"""Report maths on a synthetic dataset with known answers, and the written files."""

from __future__ import annotations

import json
import math
from datetime import date

import pytest

from src.backtest.labeller import LAUNCHED, NOT_LAUNCHED, UNKNOWN, label_brands
from src.backtest.report import (
    BacktestRow,
    compute_report,
    generate_report,
    render_markdown,
    wilson_interval,
)
from tests.backtest_helpers import make_brand, make_source_record, matched_company

BANDS = [
    {"key": "HIGH", "min": 80, "max": 100},
    {"key": "MEDIUM", "min": 60, "max": 79},
    {"key": "SUPPRESS", "min": 0, "max": 59},
]
INDICATORS = [
    {"key": "x_fires_on_high", "group": "positive_indicators", "weight": 5},
    {"key": "never_fires", "group": "positive_indicators", "weight": 3},
    {"key": "web_only", "group": "positive_indicators", "weight": 7},
]
CFG = {
    "min_labelled_for_conclusions": 30,
    "min_support_per_indicator": 10,
    "wilson_z": 1.96,
    "points_per_log_lift": 8,
    "max_suggested_change": 8,
}


def _rows() -> list[BacktestRow]:
    """10 HIGH (8 launched), 10 MEDIUM (4), 10 SUPPRESS (1), 3 unknown."""
    rows: list[BacktestRow] = []

    def add(score: int, launched: int, total: int, fired: list[str]) -> None:
        for i in range(total):
            label = LAUNCHED if i < launched else NOT_LAUNCHED
            rows.append(
                BacktestRow(
                    brand_id=len(rows) + 1,
                    brand_uid=f"b_{len(rows)}",
                    journal_number="2025-050",
                    trademark_number=f"UK{len(rows)}",
                    filing_date=date(2025, 9, 15),
                    cutoff=date(2025, 9, 15),
                    pit_score=score,
                    pit_band="HIGH" if score >= 80 else "MEDIUM" if score >= 60 else "SUPPRESS",
                    fired=fired,
                    labels={6: label},
                )
            )

    add(85, 8, 10, ["x_fires_on_high", "web_only"])
    add(65, 4, 10, [])
    add(30, 1, 10, [])
    for i in range(3):
        rows.append(
            BacktestRow(
                brand_id=100 + i,
                brand_uid=f"u{i}",
                journal_number="2025-050",
                trademark_number=f"U{i}",
                filing_date=date(2025, 9, 15),
                cutoff=date(2025, 9, 15),
                pit_score=90,
                pit_band="HIGH",
                fired=["x_fires_on_high"],
                labels={6: UNKNOWN},
            )
        )
    return rows


def test_wilson_interval_known_values():
    assert wilson_interval(0, 0) is None
    lo, hi = wilson_interval(5, 10)  # type: ignore[misc]
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)
    lo, hi = wilson_interval(0, 10)  # type: ignore[misc]
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)


@pytest.fixture
def result() -> dict:
    return compute_report(
        _rows(),
        [6],
        bands=BANDS,
        indicators=INDICATORS,
        not_evaluable={"web_only": "web search (not PIT-safe)"},
        cfg=CFG,
    )["horizons"]["6"]


class TestBands:
    def test_counts_exclude_unknown(self, result):
        assert result["labelled"] == 30
        assert result["launched"] == 13
        assert result["unknown"] == 3
        assert result["base_rate"] == pytest.approx(13 / 30)
        assert result["small_sample"] is False

    def test_precision_and_recall_by_band(self, result):
        by = {b["band"]: b for b in result["bands"]}
        assert by["HIGH"]["n"] == 10
        assert by["HIGH"]["precision"] == pytest.approx(0.8)
        assert by["HIGH"]["recall"] == pytest.approx(8 / 13)
        assert by["MEDIUM"]["precision"] == pytest.approx(0.4)
        assert by["SUPPRESS"]["recall"] == pytest.approx(1 / 13)
        cum = by["MEDIUM"]["at_or_above"]
        assert cum["n"] == 20
        assert cum["precision"] == pytest.approx(12 / 20)
        assert cum["recall"] == pytest.approx(12 / 13)
        assert by["SUPPRESS"]["at_or_above"]["recall"] == pytest.approx(1.0)


class TestIndicators:
    def test_rates_lift_and_suggestion(self, result):
        ind = {i["key"]: i for i in result["indicators"]}["x_fires_on_high"]
        assert ind["support"] == 10
        assert ind["fired_all_scored"] == 13  # unknown rows still count as fired
        assert ind["fired"]["rate"] == pytest.approx(0.8)
        assert ind["not_fired"]["rate"] == pytest.approx(5 / 20)
        assert ind["precision"] == pytest.approx(0.8)
        assert ind["recall"] == pytest.approx(8 / 13)
        assert ind["lift"] == pytest.approx(0.8 / (13 / 30))
        suggestion = ind["suggestion"]
        assert suggestion["action"] == "increase"
        assert suggestion["change"] == round(8 * math.log(0.8 / (13 / 30)))

    def test_insufficient_support_holds(self, result):
        ind = {i["key"]: i for i in result["indicators"]}["never_fires"]
        assert ind["support"] == 0
        assert ind["lift"] is None
        assert ind["suggestion"]["action"] == "hold"
        assert "insufficient support" in ind["suggestion"]["why"]

    def test_not_evaluable_indicator_is_never_assessed(self, result):
        ind = {i["key"]: i for i in result["indicators"]}["web_only"]
        assert ind["evaluable"] is False
        assert ind["suggestion"]["action"] == "cannot assess"

    def test_overlapping_intervals_hold(self):
        rows = _rows()
        # Make the indicator fire on half the HIGH and half the MEDIUM rows: weak signal.
        for r in rows[:30]:
            r.fired = ["x_fires_on_high"] if r.brand_id % 2 else []
        out = compute_report(
            rows, [6], bands=BANDS, indicators=INDICATORS[:1], not_evaluable={}, cfg=CFG
        )
        assert out["horizons"]["6"]["indicators"][0]["suggestion"]["action"] == "hold"

    def test_decrease_when_fired_rows_never_launch(self):
        rows = _rows()
        for r in rows[:30]:
            r.fired = ["x_fires_on_high"] if r.pit_score == 30 else []
            if r.pit_score == 30:
                r.labels = {6: NOT_LAUNCHED}
        out = compute_report(
            rows, [6], bands=BANDS, indicators=INDICATORS[:1], not_evaluable={}, cfg=CFG
        )
        suggestion = out["horizons"]["6"]["indicators"][0]["suggestion"]
        assert suggestion["action"] == "decrease"
        assert suggestion["change"] == -8


def _payload(results: dict) -> dict:
    return {
        "meta": {
            "date": "2026-10-09",
            "generated_at": "now",
            "database": "test",
            "journals": ["2025-050"],
            "pit_window_days": 0,
            "labeller_version": "1",
            "labels_as_of": "2026-10-09",
            "min_labelled_for_conclusions": 30,
            "unknown_reasons": {"6": {"no evidence": 3}},
            "pit_band_counts": {"HIGH": 13, "MEDIUM": 10, "SUPPRESS": 10},
            "caveats": ["a caveat"],
        },
        "dataset": {"brands": 33, "scored": 33, "skipped": {}},
        "results": results,
    }


def test_markdown_warns_prominently_only_for_small_samples():
    big = compute_report(
        _rows(), [6], bands=BANDS, indicators=INDICATORS, not_evaluable={"web_only": "w"}, cfg=CFG
    )
    text = render_markdown(json.loads(json.dumps(_payload(big))))
    assert "SMALL SAMPLE" not in text
    assert "| HIGH | 80–100 | 10 | 8 | 80% (49%–94%)" in text
    assert "`web_only` — w" in text
    assert "advice only, never applied" in text

    small_cfg = dict(CFG, min_labelled_for_conclusions=31)
    small = compute_report(
        _rows(), [6], bands=BANDS, indicators=INDICATORS, not_evaluable={}, cfg=small_cfg
    )
    assert "SMALL SAMPLE" in render_markdown(json.loads(json.dumps(_payload(small))))


def test_generate_report_writes_md_and_json(db_session, settings, tmp_path):
    from src.backtest.pit import PitScorer

    brand = make_brand(db_session)
    make_source_record(db_session, brand)
    matched_company(db_session, brand)
    other = make_brand(db_session, key="tm:abc", first_filing_date=None)
    label_brands(db_session, as_of=date(2026, 10, 9))

    md, js, payload = generate_report(
        db_session,
        out_dir=tmp_path,
        today=date(2026, 10, 9),
        database_label="test-db",
        scorer=PitScorer(settings=settings),
        note="A note for the reader.",
    )
    assert md.name == "2026-10-09.md" and js.name == "2026-10-09.json"
    data = json.loads(js.read_text())
    assert data["meta"]["weights_applied"] is False
    assert data["dataset"] == {"brands": 2, "scored": 1, "skipped": {"no opportunity linked": 1}}
    assert data["meta"]["brands_with_company_match"] == 0
    assert data["meta"]["observations_by_source"]["companies_house"]["pit_safe"] == 1
    assert data["rows"][0]["labels"] == {"3": UNKNOWN, "6": UNKNOWN}
    assert data["rows"][0]["pit_band"] in {"HIGH", "MEDIUM", "SUPPRESS"}
    text = md.read_text()
    assert "SMALL SAMPLE" in text
    assert "No brand has a known outcome yet" in text
    assert "A note for the reader." in text
    assert "Crumbledge Foods" not in text and "Crumbledge Foods" not in js.read_text()
    assert other.id  # the brand without an opportunity was counted, not scored
