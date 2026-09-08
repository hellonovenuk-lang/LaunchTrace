"""IPO Open Data parsing, against the real committed journal weeks."""

from __future__ import annotations

from datetime import date

import pytest

from src.errors import JournalParseError
from src.parse.nice import classes_from_flags, normalise_classes
from src.parse.open_data import parse_open_data_week
from src.settings import DATA_DIR

WEEK = DATA_DIR / "journals" / "opendata_week_2018-01-26.txt.gz"

pytestmark = pytest.mark.skipif(not WEEK.exists(), reason="Open Data week not present")


def test_parses_the_full_week():
    records = list(parse_open_data_week(WEEK, "2018-004"))
    assert len(records) == 1502


def test_fields_are_mapped():
    records = {r.trademark_number: r for r in parse_open_data_week(WEEK, "2018-004")}
    record = records["UK00003267137"]
    assert record.mark_text == "Qima Coffee Yemen Specialty"
    assert record.applicant_name == "Qima Coffee Ltd"
    assert record.publication_date == date(2018, 1, 26)
    assert 30 in record.nice_classes
    assert record.applicant_country == "United Kingdom"


def test_goods_text_is_reported_as_unavailable():
    record = next(iter(parse_open_data_week(WEEK, "2018-004")))
    assert record.goods_text is None
    assert record.goods_text_available is False


def test_source_url_points_at_the_public_record():
    record = next(iter(parse_open_data_week(WEEK, "2018-004")))
    assert record.source_url.startswith("https://www.ipo.gov.uk/tmcase/")


def test_missing_file_raises():
    with pytest.raises(JournalParseError):
        list(parse_open_data_week("/nonexistent/week.txt.gz", "2018-004"))


def test_non_open_data_file_raises(tmp_path):
    path = tmp_path / "wrong.txt"
    path.write_text("not,a,pipe,file\n1,2,3,4\n", encoding="utf-8")
    with pytest.raises(JournalParseError):
        list(parse_open_data_week(path, "2018-004"))


class TestNiceClasses:
    def test_class_flags(self):
        assert classes_from_flags({"Class29": "1", "Class30": "0", "Class5": "1"}) == [5, 29]

    def test_flag_variants(self):
        assert classes_from_flags({"Class1": "Y", "Class2": "", "Class3": "No"}) == [1]

    def test_normalise_from_text(self):
        assert normalise_classes("29, 30; class 43") == [29, 30, 43]

    def test_rejects_out_of_range(self):
        assert normalise_classes(["0", "46", "99", "30"]) == [30]

    def test_handles_none_and_empty(self):
        assert normalise_classes(None) == []
        assert normalise_classes("") == []
