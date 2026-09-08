"""Journal discovery, retrieval, caching and integrity metadata."""

from __future__ import annotations

from datetime import date

import pytest

from src.errors import JournalRetrievalError
from src.ingest.base import get_source
from src.ingest.cache import FileCache, sha256_file
from src.ingest.discovery import (
    date_for_journal_number,
    friday_ordinal,
    is_publication_day,
    journal_number_for_date,
    latest_expected_journal,
    most_recent_friday,
    previous_journal_dates,
)
from src.ingest.fixture import FixtureJournalSource
from src.ingest.local_file import LocalJournalSource
from src.ingest.ukipo_http import UkipoJournalHttpSource


class TestJournalNumbering:
    @pytest.mark.parametrize(
        "day,expected",
        [
            (date(2018, 1, 5), "2018-001"),
            (date(2018, 1, 26), "2018-004"),
            (date(2025, 12, 26), "2025-052"),
        ],
    )
    def test_number_from_publication_date(self, day, expected):
        assert journal_number_for_date(day) == expected

    @pytest.mark.parametrize("day", [date(2018, 1, 5), date(2018, 1, 26), date(2025, 12, 26)])
    def test_round_trips(self, day):
        assert date_for_journal_number(journal_number_for_date(day)) == day

    def test_a_midweek_date_maps_to_its_journal_week(self):
        assert journal_number_for_date(date(2018, 1, 24)) == "2018-003"

    def test_rejects_a_nonsense_number(self):
        with pytest.raises(ValueError):
            date_for_journal_number("not-a-journal")

    def test_friday_ordinal_rejects_a_non_friday(self):
        with pytest.raises(ValueError):
            friday_ordinal(date(2025, 12, 25))

    def test_publication_day_is_friday(self):
        assert is_publication_day(date(2025, 12, 26)) is True
        assert is_publication_day(date(2025, 12, 25)) is False


class TestSchedule:
    def test_most_recent_friday(self):
        assert most_recent_friday(date(2025, 12, 30)) == date(2025, 12, 26)
        assert most_recent_friday(date(2025, 12, 26)) == date(2025, 12, 26)

    def test_backfill_dates_are_oldest_first(self):
        dates = previous_journal_dates(4, date(2018, 1, 29))
        assert dates == [date(2018, 1, 5), date(2018, 1, 12), date(2018, 1, 19), date(2018, 1, 26)]

    def test_same_day_publication_falls_back_a_week(self):
        friday = date(2025, 12, 26)
        assert latest_expected_journal(friday, min_age_hours=12) == date(2025, 12, 19)


class TestSourceFactory:
    @pytest.mark.parametrize("name", ["ukipo_http", "open_data", "local", "fixture"])
    def test_every_configured_source_can_be_built(self, name, settings):
        assert get_source(name, settings) is not None

    def test_unknown_source_is_rejected(self, settings):
        with pytest.raises(ValueError):
            get_source("nonexistent", settings)


class TestUkipoHttpSource:
    def test_candidate_urls_target_the_journal_directory(self, settings):
        source = UkipoJournalHttpSource(settings)
        ref = source.ref_for(journal_number="2025-052")
        urls = source.candidate_urls(ref)
        assert all("/2025-052/" in u for u in urls)
        assert any(u.endswith(".xml") for u in urls)
        assert any(u.endswith(".zip") for u in urls)

    def test_base_url_is_configurable(self, settings):
        custom = settings.model_copy(
            update={"ukipo_journal_base_url": "https://mirror.test/journals"}
        )
        source = UkipoJournalHttpSource(custom)
        assert source.candidate_urls(source.ref_for("2025-052"))[0].startswith(
            "https://mirror.test/"
        )

    def test_ref_carries_the_public_source_url(self, settings):
        ref = UkipoJournalHttpSource(settings).ref_for(journal_number="2025-052")
        assert ref.source_url and ref.source_url.endswith("/2025-052/")


class TestFixtureSource:
    def test_lists_available_journals(self, settings):
        assert FixtureJournalSource(settings).available_refs()

    def test_fetch_returns_integrity_metadata(self, settings):
        source = FixtureJournalSource(settings)
        artifact = source.fetch(source.latest_ref())
        assert artifact.sha256 and len(artifact.sha256) == 64
        assert artifact.byte_size > 0

    def test_unknown_journal_is_an_error(self, settings):
        with pytest.raises(JournalRetrievalError):
            FixtureJournalSource(settings).ref_for(journal_number="1999-001")


class TestLocalSource:
    def test_reads_a_file_named_by_journal_number(self, settings, tmp_path):
        (tmp_path / "2025-052.xml").write_text("<x/>", encoding="utf-8")
        source = LocalJournalSource(
            settings.model_copy(update={"journal_local_dir": str(tmp_path)})
        )
        ref = source.ref_for(journal_number="2025-052")
        assert ref.publication_date == date(2025, 12, 26)
        assert source.fetch(ref).local_path.endswith("2025-052.xml")

    def test_reads_a_file_named_by_publication_date(self, settings, tmp_path):
        (tmp_path / "2025-12-26.xml").write_text("<x/>", encoding="utf-8")
        source = LocalJournalSource(
            settings.model_copy(update={"journal_local_dir": str(tmp_path)})
        )
        assert source.latest_ref().journal_number == "2025-052"

    def test_empty_directory_explains_what_to_do(self, settings, tmp_path):
        source = LocalJournalSource(
            settings.model_copy(update={"journal_local_dir": str(tmp_path)})
        )
        with pytest.raises(JournalRetrievalError) as exc:
            source.latest_ref()
        assert "2025-052.xml" in str(exc.value)


class TestCache:
    def test_round_trips_a_file(self, tmp_path):
        cache = FileCache(tmp_path / "cache")
        source = tmp_path / "source.xml"
        source.write_text("<x/>", encoding="utf-8")
        stored = cache.put_file("key", source, ".xml")
        assert cache.has("key", ".xml")
        assert stored.read_text(encoding="utf-8") == "<x/>"

    def test_sanitises_the_key_for_the_filesystem(self, tmp_path):
        cache = FileCache(tmp_path / "cache")
        assert "/" not in cache.path_for("a/b:c", ".xml").name

    def test_checksum_is_stable_and_changes_with_content(self, tmp_path):
        path = tmp_path / "f.xml"
        path.write_text("a", encoding="utf-8")
        first = sha256_file(path)
        assert first == sha256_file(path)
        path.write_text("b", encoding="utf-8")
        assert sha256_file(path) != first

    def test_prune_removes_old_files(self, tmp_path):
        import os
        import time

        cache = FileCache(tmp_path / "cache")
        old = cache.path_for("old", ".xml")
        old.write_text("x", encoding="utf-8")
        os.utime(old, (time.time() - 60 * 86400, time.time() - 60 * 86400))
        assert cache.prune(max_age_days=30) == 1
        assert not old.exists()
