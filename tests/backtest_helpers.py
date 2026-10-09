"""Builders shared by the backtest tests: brands, source records, opportunities, observations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from src.brands import brand_uid_for, record_observation
from src.db.tables import Brand, OpportunityRow, TrademarkRecordRow


def make_brand(session: Session, key: str = "ch:14000001", **overrides: Any) -> Brand:
    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "brand_key": key,
        "brand_uid": brand_uid_for(key),
        "brand_name": "CRUMBLEDGE",
        "applicant_type": "corporate",
        "first_seen_journal": "2025-050",
        "first_seen_at": now,
        "first_filing_date": date(2025, 9, 15),
        "last_seen_journal": "2025-050",
        "current_stage": "pre_launch",
        "current_score": 70,
        "current_band": "MEDIUM",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    brand = Brand(**fields)
    session.add(brand)
    session.flush()
    return brand


def make_source_record(
    session: Session, brand: Brand | None = None, **overrides: Any
) -> TrademarkRecordRow:
    fields: dict[str, Any] = {
        "journal_number": "2025-050",
        "dedupe_key": "k-" + str(overrides.get("trademark_number", "UK00003900001")),
        "trademark_number": "UK00003900001",
        "mark_text": "CRUMBLEDGE",
        "mark_type": "Word",
        "filing_date": date(2025, 9, 15),
        "publication_date": date(2025, 12, 12),
        "applicant_name": "Crumbledge Foods Ltd",
        "applicant_country": "GB",
        "nice_classes": [30],
        "goods_text": "Snack bars; cereal bars; oat bars; biscuits.",
        "goods_text_available": True,
        "source_name": "fixture_journal_xml",
    }
    fields.update(overrides)
    row = TrademarkRecordRow(**fields)
    session.add(row)
    if brand is not None:
        session.add(
            OpportunityRow(
                dedupe_key=row.dedupe_key,
                run_id="run_test",
                journal_number=row.journal_number,
                trademark_number=row.trademark_number,
                brand_name=row.mark_text,
                filing_date=row.filing_date,
                launchtrace_score=72,
                score_band="MEDIUM",
                brand_id=brand.id,
            )
        )
    session.flush()
    return row


def observe(
    session: Session,
    brand: Brand,
    signal: str,
    value: Any,
    *,
    observed_at: datetime | None = None,
    source_date: date | None = None,
    pit: bool | None = None,
) -> None:
    from src.brands import signal_registry

    record_observation(
        session,
        brand.id,
        str(signal_registry()[signal]["source"]),
        signal,
        value,
        observed_at=observed_at or datetime(2025, 12, 12, 9, 0, tzinfo=UTC),
        source_date=source_date,
        point_in_time_safe=pit,
        run_id="run_test",
        journal_number="2025-050",
    )


def matched_company(
    session: Session,
    brand: Brand,
    incorporated: date = date(2025, 3, 1),
    confidence: int = 98,
    number: str = "14000001",
) -> None:
    """What sync_brands records for a confident Companies House match."""
    observe(
        session,
        brand,
        "incorporation_date",
        {"company_number": number, "incorporation_date": incorporated.isoformat()},
        source_date=incorporated,
    )
    observe(
        session,
        brand,
        "company_match",
        {"company_number": number, "match_confidence": confidence, "match_method": "exact"},
    )
