"""Live retrieval of the UKIPO Trade Marks Journal.

The IPO publishes the Trade Marks Journal every Friday.  Each journal has its
own directory on ipo.gov.uk, addressed by ``YYYY-NNN`` (year plus the ordinal
Friday of that year), for example::

    https://www.ipo.gov.uk/types/tm/t-os/t-tmj/tm-journals/2025-052/

The XML data file inside that directory has been published under several
filenames over the years, so rather than hard-coding one obsolete pattern this
source:

1. tries to read the journal's index page and take the XML/ZIP link from it;
2. falls back to a list of known filename patterns;
3. reports precisely what it tried when nothing works.

``UKIPO_JOURNAL_BASE_URL`` overrides the base if the IPO reorganises, and
``JOURNAL_SOURCE=local`` lets an operator feed in a manually downloaded file
without any code change.

Note on access: ipo.gov.uk sits behind bot protection that challenges some
network ranges with a captcha.  When that happens this source raises
``JournalRetrievalError`` with the HTTP status, and the run fails closed rather
than delivering a partial report.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import httpx

from src.errors import JournalNotYetPublishedError, JournalRetrievalError
from src.ingest.base import JournalSource
from src.ingest.cache import FileCache, sha256_file
from src.ingest.discovery import (
    date_for_journal_number,
    journal_number_for_date,
    latest_expected_journal,
    previous_journal_dates,
)
from src.ingest.http_client import HttpClient
from src.logging_setup import get_logger
from src.models import JournalArtifact, JournalRef
from src.settings import Settings

log = get_logger(__name__)

# Filename patterns the IPO has used for the machine-readable journal.
# ``{n}`` = journal number (2025-052), ``{c}`` = compact form (2025052).
XML_FILENAME_PATTERNS: tuple[str, ...] = (
    "{n}.xml",
    "{c}.xml",
    "journal.xml",
    "tmj{c}.xml",
    "xml/{n}.xml",
    "xml/{c}.xml",
    "{n}.zip",
    "{c}.zip",
    "tmj{c}.zip",
    "xml/{n}.zip",
)

_LINK_RE = re.compile(r'href="([^"]+\.(?:xml|zip))"', re.IGNORECASE)


class UkipoJournalHttpSource(JournalSource):
    name = "ukipo_journal_xml"
    parser = "journal_xml"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.base_url = self.settings.ukipo_journal_base_url.rstrip("/")
        self.client = HttpClient(
            user_agent=self.settings.ukipo_user_agent,
            timeout=self.settings.ukipo_request_timeout_seconds,
            max_retries=self.settings.ukipo_max_retries,
        )
        self.cache = FileCache(Path(self.settings.cache_dir) / "journals")

    # -- refs -------------------------------------------------------------
    def _ref(self, publication_date: date) -> JournalRef:
        number = journal_number_for_date(publication_date)
        return JournalRef(
            journal_number=number,
            publication_date=publication_date,
            source_name=self.name,
            source_url=f"{self.base_url}/{number}/",
        )

    def latest_ref(self) -> JournalRef:
        return self._ref(latest_expected_journal())

    def ref_for(
        self, journal_number: str | None = None, publication_date: date | None = None
    ) -> JournalRef:
        if journal_number:
            return self._ref(date_for_journal_number(journal_number))
        if publication_date:
            return self._ref(publication_date)
        return self.latest_ref()

    def available_refs(self, limit: int = 12) -> list[JournalRef]:
        return [self._ref(d) for d in reversed(previous_journal_dates(limit))]

    # -- retrieval --------------------------------------------------------
    def candidate_urls(self, ref: JournalRef) -> list[str]:
        n = ref.journal_number
        c = n.replace("-", "")
        base = f"{self.base_url}/{n}"
        return [f"{base}/{p.format(n=n, c=c)}" for p in XML_FILENAME_PATTERNS]

    def _discover_from_index(self, ref: JournalRef) -> list[str]:
        index_url = f"{self.base_url}/{ref.journal_number}/index.html"
        try:
            html = self.client.get_text(index_url)
        except httpx.HTTPError as exc:
            log.info("ukipo.index.unavailable", url=index_url, error=str(exc)[:200])
            return []
        found: list[str] = []
        for href in _LINK_RE.findall(html):
            if href.startswith("http"):
                found.append(href)
            else:
                found.append(f"{self.base_url}/{ref.journal_number}/{href.lstrip('./')}")
        if found:
            log.info("ukipo.index.links", url=index_url, count=len(found))
        return found

    def fetch(self, ref: JournalRef) -> JournalArtifact:
        cache_key = f"{ref.source_name}-{ref.journal_number}"
        cached = self.cache.get(cache_key, ".xml") or self.cache.get(cache_key, ".zip")
        if cached:
            log.info("ukipo.fetch.cache_hit", journal=ref.journal_number, path=str(cached))
            return JournalArtifact(
                ref=ref,
                local_path=str(cached),
                byte_size=cached.stat().st_size,
                sha256=sha256_file(cached),
                content_type="application/zip" if cached.suffix == ".zip" else "application/xml",
                from_cache=True,
            )

        attempted: list[str] = []
        urls = self._discover_from_index(ref) + self.candidate_urls(ref)
        last_status: int | None = None
        for url in urls:
            attempted.append(url)
            suffix = ".zip" if url.lower().endswith(".zip") else ".xml"
            dest = self.cache.path_for(cache_key, suffix)
            try:
                self.client.download(url, dest)
            except httpx.HTTPStatusError as exc:
                last_status = exc.response.status_code
                continue
            except httpx.HTTPError:
                continue
            if dest.exists() and dest.stat().st_size > 1024:
                return JournalArtifact(
                    ref=ref.model_copy(update={"source_url": url}),
                    local_path=str(dest),
                    byte_size=dest.stat().st_size,
                    sha256=sha256_file(dest),
                    content_type="application/zip" if suffix == ".zip" else "application/xml",
                )
            dest.unlink(missing_ok=True)

        detail = (
            f"journal={ref.journal_number} publication_date={ref.publication_date} "
            f"attempted={len(attempted)} urls last_status={last_status}"
        )
        if ref.publication_date >= latest_expected_journal():
            raise JournalNotYetPublishedError(
                f"UKIPO journal not retrievable yet ({detail}). "
                "If this is the current week, retry after the Friday publication window."
            )
        raise JournalRetrievalError(
            f"Could not retrieve the UKIPO journal ({detail}). "
            f"First URL tried: {attempted[0] if attempted else 'none'}. "
            "If ipo.gov.uk is returning a captcha/403 for this network, download the "
            "journal manually and re-run with JOURNAL_SOURCE=local."
        )
