"""Journal XML parsing, including malformed input."""

from __future__ import annotations

from datetime import date

import pytest

from src.errors import JournalParseError
from src.parse.journal_xml import parse_journal_xml, probe_structure
from tests.conftest import FIXTURE_JOURNAL, MALFORMED_JOURNAL


def test_parses_every_record():
    records = list(parse_journal_xml(FIXTURE_JOURNAL, "2025-050"))
    assert len(records) == 8
    assert {r.trademark_number for r in records} == {f"UK0000390000{i}" for i in range(1, 9)}


def test_extracts_all_expected_fields():
    records = {r.trademark_number: r for r in parse_journal_xml(FIXTURE_JOURNAL, "2025-050")}
    record = records["UK00003900001"]
    assert record.mark_text == "CRUMBLEDGE"
    assert record.mark_type == "Word"
    assert record.filing_date == date(2025, 9, 15)
    assert record.publication_date == date(2025, 12, 12)
    assert record.applicant_name == "Crumbledge Foods Ltd"
    assert record.applicant_country == "GB"
    assert record.applicant_postcode_area == "BS1"
    assert record.nice_classes == [30]
    assert record.goods_text_available is True
    assert "cereal bars" in record.goods_text.lower()
    assert record.journal_number == "2025-050"


def test_multiple_nice_classes_are_collected():
    records = {r.trademark_number: r for r in parse_journal_xml(FIXTURE_JOURNAL, "2025-050")}
    assert records["UK00003900007"].nice_classes == [29, 30]


def test_malformed_records_are_skipped_not_fatal():
    records = list(parse_journal_xml(MALFORMED_JOURNAL, "2025-099"))
    numbers = {r.trademark_number for r in records}
    # The record with no application number is dropped; the valid one survives.
    assert "UK00003999003" in numbers
    assert len(records) == 2


def test_unparseable_dates_become_none_rather_than_crashing():
    records = {r.trademark_number: r for r in parse_journal_xml(MALFORMED_JOURNAL, "2025-099")}
    assert records["UK00003999001"].filing_date is None


def test_invalid_nice_class_is_discarded():
    records = {r.trademark_number: r for r in parse_journal_xml(MALFORMED_JOURNAL, "2025-099")}
    assert records["UK00003999001"].nice_classes == []  # class 99 does not exist


def test_missing_file_raises_parse_error(tmp_path):
    with pytest.raises(JournalParseError):
        list(parse_journal_xml(tmp_path / "nope.xml", "2025-050"))


def test_empty_document_raises_parse_error(tmp_path):
    path = tmp_path / "empty.xml"
    path.write_text("<?xml version='1.0'?><Nothing/>", encoding="utf-8")
    with pytest.raises(JournalParseError):
        list(parse_journal_xml(path, "2025-050"))


def test_probe_reports_structure():
    info = probe_structure(FIXTURE_JOURNAL)
    names = dict(info["top_elements"])
    assert names["TradeMark"] == 8
