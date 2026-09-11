"""Shrinking and vetting a downloaded journal.

The network call is not tested here; what matters is everything that decides
whether a response is believed and how big it is on disk. The failure that
matters is not "the fetch failed" — it is "the fetch returned an error page and
the pipeline scored it as a quiet week".
"""

from __future__ import annotations

import pytest

from src.errors import JournalRetrievalError
from src.ingest.journal_bytes import (
    MIN_PLAUSIBLE_BYTES,
    strip_image_blobs,
    validate_journal_bytes,
)
from src.parse.journal_xml import parse_journal_xml

JOURNAL_XML = b"<?xml version='1.0' encoding='UTF-8'?><JournalTransaction></JournalTransaction>"

# What ipo.gov.uk actually serves for a journal that does not exist: a 401-byte
# XHTML error page, with a 404 on GET but a 200 on HEAD.
IPO_404_PAGE = (
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN">'
    b"<html><body>Page not found</body></html>"
)


def _big(payload: bytes) -> bytes:
    return payload + b"<!--" + b"x" * MIN_PLAUSIBLE_BYTES + b"-->"


class TestResponseValidation:
    def test_real_xml_is_accepted(self):
        validate_journal_bytes(
            "https://example.test/jnl.xml", 200, "application/xml", _big(JOURNAL_XML)
        )

    def test_an_html_error_page_is_refused_even_with_status_200(self):
        """A soft 404 served as 200 must never reach the parser."""
        with pytest.raises(JournalRetrievalError, match="not XML"):
            validate_journal_bytes(
                "https://example.test/jnl.xml", 200, "text/html", _big(IPO_404_PAGE)
            )

    def test_a_404_is_refused(self):
        with pytest.raises(JournalRetrievalError, match="HTTP 404"):
            validate_journal_bytes("https://example.test/jnl.xml", 404, "text/html", IPO_404_PAGE)

    def test_a_403_is_refused(self):
        with pytest.raises(JournalRetrievalError, match="HTTP 403"):
            validate_journal_bytes("https://example.test/jnl.xml", 403, "text/html", b"")

    def test_xml_that_is_too_small_to_be_a_journal_is_refused(self):
        """A truncated download is a failure, not a quiet week."""
        with pytest.raises(JournalRetrievalError, match="too small"):
            validate_journal_bytes(
                "https://example.test/jnl.xml", 200, "application/xml", JOURNAL_XML
            )

    def test_leading_whitespace_does_not_break_detection(self):
        validate_journal_bytes(
            "https://example.test/jnl.xml", 200, "application/xml", _big(b"\n  " + JOURNAL_XML)
        )


class TestImageStripping:
    def test_artwork_is_removed(self):
        raw = b"<TradeMark><MarkImageBinary>" + b"A" * 5000 + b"</MarkImageBinary></TradeMark>"
        assert len(strip_image_blobs(raw)) < 100

    def test_every_blob_is_removed_not_just_the_first(self):
        raw = b"".join(
            b"<TradeMark><MarkImageBinary>data</MarkImageBinary></TradeMark>" for _ in range(5)
        )
        assert b"MarkImageBinary" not in strip_image_blobs(raw)

    def test_a_journal_with_no_artwork_is_untouched(self):
        assert strip_image_blobs(JOURNAL_XML) == JOURNAL_XML

    def test_stripping_does_not_change_a_single_parsed_field(self, tmp_path):
        """The claim that makes stripping safe, tested rather than asserted."""
        from src.settings import FIXTURES_DIR

        source = (FIXTURES_DIR / "journals" / "2025-050.xml").read_bytes()
        with_art = source.replace(
            b"</TradeMark>",
            b"<MarkImageBinary>" + b"Zm9v" * 200 + b"</MarkImageBinary></TradeMark>",
        )
        a = tmp_path / "with_art.xml"
        b = tmp_path / "stripped.xml"
        a.write_bytes(with_art)
        b.write_bytes(strip_image_blobs(with_art))
        assert b.stat().st_size < a.stat().st_size

        left = [r.model_dump() for r in parse_journal_xml(a, "2025-050")]
        right = [r.model_dump() for r in parse_journal_xml(b, "2025-050")]
        assert left == right
        assert left, "the fixture must actually yield records for this to mean anything"


class TestUrlPatterns:
    def test_the_filename_the_ipo_actually_uses_is_tried_first(self):
        """`jnl.xml` was missing, which is why every automated fetch failed."""
        from src.ingest.ukipo_http import XML_FILENAME_PATTERNS

        assert XML_FILENAME_PATTERNS[0] == "jnl.xml"

    def test_the_base_url_points_at_the_live_journal_path(self):
        from src.settings import Settings

        settings = Settings()  # type: ignore[call-arg]
        assert settings.ukipo_journal_base_url.endswith("/t-tmj/tm-journals")
