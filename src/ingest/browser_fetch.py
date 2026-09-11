"""Fetching the journal through a real browser engine.

``ipo.gov.uk`` sits behind protection that refuses ordinary HTTP clients. The
block is on the *client*, not on the network or the address: from the same
machine and the same IP, ``httpx`` and ``curl`` get 403 and Chromium gets 200.
Neither a browser-shaped user-agent nor a different path changes that, so this
module does the one thing that works — issues the request from Chromium's own
network stack.

Two consequences worth stating plainly, because both are load-bearing:

* **The response must be validated, not assumed.** The IPO serves a 401-byte
  HTML page with a proper 404 for a journal that does not exist, but a bare
  ``HEAD`` returns 200 for *any* path. So this module always uses ``GET`` and
  checks that what came back is really XML before believing it.
* **This is a dependency on someone else's bot policy.** It works today and is
  the difference between an unattended Friday run and a manual download, but it
  can be tightened at any time. ``JOURNAL_SOURCE=local`` stays supported as the
  fallback, and a proper data feed from the IPO remains the right long-term fix.

Playwright is an optional dependency. If it is not installed, this module
reports that clearly rather than failing at import time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.errors import JournalRetrievalError
from src.logging_setup import get_logger

log = get_logger(__name__)

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# The embedded mark artwork is base64 and is 95% of the download. Nothing in
# the pipeline reads it: stripping it takes journal 2026-036 from 152 MB to
# 7.9 MB and leaves the parsed records identical, field for field.
_IMAGE_BLOB_RE = re.compile(rb"<MarkImageBinary>.*?</MarkImageBinary>", re.S)

# Anything smaller than this is an error page, not a journal.
MIN_PLAUSIBLE_BYTES = 100_000


class BrowserUnavailableError(JournalRetrievalError):
    """Playwright or its browser build is not installed."""


@dataclass
class BrowserFetchResult:
    url: str
    path: Path
    byte_size: int
    stripped_bytes: int
    status: int


def browser_available() -> bool:
    """Whether a browser fetch can be attempted at all."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def strip_image_blobs(raw: bytes) -> bytes:
    """Remove embedded artwork. Lossless for every field the parser reads."""
    return _IMAGE_BLOB_RE.sub(b"", raw)


def _validate(url: str, status: int, content_type: str, body: bytes) -> None:
    """Refuse anything that is not actually a journal.

    A soft failure that returns an HTML error page as if it were data is the
    one outcome worse than a hard failure, because it reaches the scoring
    stages and comes out looking like a quiet week.
    """
    if status != 200:
        raise JournalRetrievalError(f"{url} returned HTTP {status}")
    head = body[:200].lstrip()
    if not head.startswith(b"<?xml"):
        preview = head[:80].decode("utf-8", "replace")
        raise JournalRetrievalError(
            f"{url} returned {content_type or 'unknown content'}, not XML "
            f"(starts {preview!r}). The IPO serves an HTML error page for a "
            "journal that is not published."
        )
    if len(body) < MIN_PLAUSIBLE_BYTES:
        raise JournalRetrievalError(
            f"{url} returned only {len(body):,} bytes, which is too small to be a journal"
        )


def fetch_via_browser(
    url: str,
    dest: Path,
    timeout_seconds: int = 300,
    strip_images: bool = True,
    executable_path: str | None = None,
) -> BrowserFetchResult:
    """Download ``url`` through Chromium and write it to ``dest``.

    ``executable_path`` points at an already-installed Chromium. It is needed
    wherever the browser build on disk does not match the Playwright version --
    a pre-provisioned image, for instance -- and is ignored when empty, which
    is the normal case after ``playwright install chromium``.

    Raises ``BrowserUnavailableError`` if Playwright is missing, and
    ``JournalRetrievalError`` if what came back is not a journal.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise BrowserUnavailableError(
            "Playwright is not installed, so the browser fallback cannot run. "
            "Install it with: pip install playwright && playwright install chromium"
        ) from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("ukipo.browser.fetch_start", url=url)

    launch_kwargs: dict = {"headless": True}
    if executable_path:
        launch_kwargs["executable_path"] = executable_path

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            context = browser.new_context(user_agent=CHROME_USER_AGENT, locale="en-GB")
            response = context.request.get(url, timeout=timeout_seconds * 1000)
            status = response.status
            content_type = response.headers.get("content-type", "")
            body = response.body()
        finally:
            browser.close()

    _validate(url, status, content_type, body)

    raw_size = len(body)
    if strip_images:
        body = strip_image_blobs(body)
    dest.write_bytes(body)

    log.info(
        "ukipo.browser.fetch_ok",
        url=url,
        downloaded=raw_size,
        written=len(body),
        path=str(dest),
    )
    return BrowserFetchResult(
        url=url,
        path=dest,
        byte_size=raw_size,
        stripped_bytes=len(body),
        status=status,
    )


__all__ = [
    "BrowserFetchResult",
    "BrowserUnavailableError",
    "CHROME_USER_AGENT",
    "MIN_PLAUSIBLE_BYTES",
    "browser_available",
    "fetch_via_browser",
    "strip_image_blobs",
]
