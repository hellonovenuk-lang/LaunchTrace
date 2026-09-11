"""Making a downloaded journal small, and checking it is really a journal.

Two things the weekly fetch needs that are easy to get wrong.

**Size.** The journal carries every mark's artwork inline as base64. It is
around 95% of the download — journal 2026-037 is 225 MB, of which 11 MB is the
data — and nothing in this pipeline reads a single image. Stripping it on
arrival is what keeps a year of journals in tens of megabytes instead of
gigabytes. ``tests/test_journal_bytes.py`` proves the parsed records are
identical either way, so this is lossless rather than merely cheap.

**Trust.** ``ipo.gov.uk`` answers **403, not 404**, for a file that does not
exist, and serves a small HTML error page for some paths that do. A fetch that
believes whatever it receives will hand the parser an error page, and a week
with no data scores exactly like a quiet week. So a response is checked before
it is believed.
"""

from __future__ import annotations

import re

from src.errors import JournalRetrievalError
from src.logging_setup import get_logger

log = get_logger(__name__)

_IMAGE_BLOB_RE = re.compile(rb"<MarkImageBinary>.*?</MarkImageBinary>", re.S)

# Anything smaller than this is an error page, not a journal.
MIN_PLAUSIBLE_BYTES = 100_000


def strip_image_blobs(raw: bytes) -> bytes:
    """Remove embedded artwork. Lossless for every field the parser reads."""
    return _IMAGE_BLOB_RE.sub(b"", raw)


def validate_journal_bytes(url: str, status: int, content_type: str, body: bytes) -> None:
    """Refuse anything that is not actually a journal.

    Raises ``JournalRetrievalError`` rather than returning a flag, because the
    one outcome worse than a failed fetch is a fetch that quietly succeeds with
    the wrong thing.
    """
    if status != 200:
        raise JournalRetrievalError(
            f"{url} returned HTTP {status}. Note that ipo.gov.uk answers 403, "
            "not 404, for a file that does not exist."
        )
    head = body[:200].lstrip()
    if not head.startswith(b"<?xml"):
        preview = head[:80].decode("utf-8", "replace")
        raise JournalRetrievalError(
            f"{url} returned {content_type or 'unknown content'}, not XML (starts {preview!r})."
        )
    if len(body) < MIN_PLAUSIBLE_BYTES:
        raise JournalRetrievalError(
            f"{url} returned only {len(body):,} bytes, which is too small to be a journal"
        )


__all__ = [
    "MIN_PLAUSIBLE_BYTES",
    "strip_image_blobs",
    "validate_journal_bytes",
]
