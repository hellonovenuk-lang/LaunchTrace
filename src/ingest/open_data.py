"""IPO Open Data release as a journal source.

The IPO publishes a full snapshot of UK domestic trade mark applications as a
pipe-delimited data file under the Open Government Licence:

    https://www.gov.uk/government/publications/ipo-trade-mark-data-release

Every record carries a ``Published`` date, and the IPO publishes on Fridays, so
the snapshot can be sliced back into the exact weekly journals it came from.
That makes it a genuine second retrieval route for the same official records --
useful for historical backfill and validation, and as a fallback when the
weekly journal endpoint is unavailable.

One real limitation, recorded honestly rather than papered over: the Open Data
release does not include goods and services text.  Records sourced this way are
flagged ``goods_text_available=False``, which the classifier and the score both
take into account.
"""

from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

from src.errors import JournalRetrievalError
from src.ingest.base import JournalSource
from src.ingest.cache import FileCache, sha256_file
from src.ingest.discovery import date_for_journal_number, journal_number_for_date
from src.ingest.http_client import HttpClient
from src.logging_setup import get_logger
from src.models import JournalArtifact, JournalRef
from src.settings import DATA_DIR, Settings

log = get_logger(__name__)

OPEN_DATA_LANDING = "https://www.gov.uk/government/publications/ipo-trade-mark-data-release"
OPEN_DATA_ZIP = (
    "https://assets.publishing.service.gov.uk/media/5a82e5f4e5274a2e87dc387d/opendatadomestic.zip"
)
JOURNAL_DIR = DATA_DIR / "journals"


class IpoOpenDataSource(JournalSource):
    """Serves per-week slices of the official IPO Open Data release.

    Weeks that have been extracted into ``data/journals/`` are served straight
    from disk.  Anything else is cut from the full snapshot, which is
    downloaded on demand (see ``scripts/extract_open_data_weeks.py``).
    """

    name = "ipo_open_data"
    parser = "open_data"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.journal_dir = JOURNAL_DIR
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.cache = FileCache(Path(self.settings.cache_dir) / "opendata")
        self.client = HttpClient(
            user_agent=self.settings.ukipo_user_agent,
            timeout=self.settings.ukipo_request_timeout_seconds,
            max_retries=self.settings.ukipo_max_retries,
        )

    # -- local slices ------------------------------------------------------
    def _slice_path(self, publication_date: date) -> Path | None:
        for suffix in (".txt.gz", ".txt"):
            p = self.journal_dir / f"opendata_week_{publication_date.isoformat()}{suffix}"
            if p.exists():
                return p
        return None

    def local_publication_dates(self) -> list[date]:
        dates: set[date] = set()
        for p in self.journal_dir.glob("opendata_week_*.txt*"):
            stem = p.name.replace("opendata_week_", "").split(".")[0]
            try:
                dates.add(date.fromisoformat(stem))
            except ValueError:
                continue
        return sorted(dates)

    # -- refs --------------------------------------------------------------
    def _ref(self, publication_date: date) -> JournalRef:
        return JournalRef(
            journal_number=journal_number_for_date(publication_date),
            publication_date=publication_date,
            source_name=self.name,
            source_url=OPEN_DATA_LANDING,
        )

    def latest_ref(self) -> JournalRef:
        dates = self.local_publication_dates()
        if not dates:
            raise JournalRetrievalError(
                "No IPO Open Data journal weeks are available locally. "
                "Run: python -m src.pipeline fetch-open-data --weeks 4"
            )
        return self._ref(dates[-1])

    def ref_for(
        self, journal_number: str | None = None, publication_date: date | None = None
    ) -> JournalRef:
        if journal_number:
            return self._ref(date_for_journal_number(journal_number))
        if publication_date:
            return self._ref(publication_date)
        return self.latest_ref()

    def available_refs(self, limit: int = 12) -> list[JournalRef]:
        return [self._ref(d) for d in reversed(self.local_publication_dates())][:limit]

    # -- retrieval ---------------------------------------------------------
    def fetch(self, ref: JournalRef) -> JournalArtifact:
        path = self._slice_path(ref.publication_date)
        if path is None:
            raise JournalRetrievalError(
                f"No Open Data slice for {ref.publication_date.isoformat()} "
                f"({ref.journal_number}). Available weeks: "
                f"{[d.isoformat() for d in self.local_publication_dates()] or 'none'}. "
                "Run: python -m src.pipeline fetch-open-data --weeks 4"
            )
        return JournalArtifact(
            ref=ref,
            local_path=str(path),
            content_type="text/tab-separated-values",
            byte_size=path.stat().st_size,
            sha256=sha256_file(path),
            from_cache=True,
        )

    # -- snapshot management -----------------------------------------------
    def download_snapshot(self) -> Path:
        """Download the full official Open Data release (~63 MB compressed)."""
        dest = self.cache.path_for("opendatadomestic", ".zip")
        if dest.exists() and dest.stat().st_size > 1_000_000:
            log.info("opendata.snapshot.cached", path=str(dest))
            return dest
        return self.client.download(OPEN_DATA_ZIP, dest)

    def extract_weeks(
        self, publication_dates: list[date], snapshot: Path | None = None
    ) -> list[Path]:
        """Cut the requested publication weeks out of the snapshot."""
        import io
        import zipfile
        from contextlib import ExitStack

        snapshot = snapshot or self.download_snapshot()
        wanted = {d.isoformat() for d in publication_dates}
        kept = 0

        with ExitStack() as stack:
            zf = stack.enter_context(zipfile.ZipFile(snapshot))
            raw = stack.enter_context(zf.open(zf.namelist()[0]))
            text = io.TextIOWrapper(raw, encoding="utf-16", errors="replace", newline="")
            header = text.readline().rstrip("\r\n")
            pub_idx = header.split("|").index("Published")

            writers = {}
            for iso in sorted(wanted):
                path = self.journal_dir / f"opendata_week_{iso}.txt.gz"
                handle = stack.enter_context(gzip.open(path, "wt", encoding="utf-8", newline="\n"))
                handle.write(header + "\n")
                writers[iso] = handle

            for line in text:
                parts = line.split("|")
                if len(parts) <= pub_idx:
                    continue
                pub = parts[pub_idx].strip()
                if pub in wanted:
                    writers[pub].write(line.rstrip("\r\n") + "\n")
                    kept += 1

        log.info("opendata.weeks.extracted", weeks=len(wanted), records=kept)
        return [self.journal_dir / f"opendata_week_{iso}.txt.gz" for iso in sorted(wanted)]

    def discover_recent_publication_dates(
        self, weeks: int, snapshot: Path | None = None
    ) -> list[date]:
        """The most recent complete publication weeks present in the snapshot."""
        import io
        import zipfile
        from collections import Counter

        snapshot = snapshot or self.download_snapshot()
        counts: Counter[str] = Counter()
        with zipfile.ZipFile(snapshot) as zf:
            inner = zf.namelist()[0]
            with zf.open(inner) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-16", errors="replace", newline="")
                columns = text.readline().rstrip("\r\n").split("|")
                pub_idx = columns.index("Published")
                for line in text:
                    parts = line.split("|")
                    if len(parts) <= pub_idx:
                        continue
                    pub = parts[pub_idx].strip()
                    if len(pub) == 10:
                        counts[pub] += 1
        # A complete journal week has a meaningful record count; ignore stragglers.
        candidates = sorted(d for d, n in counts.items() if n >= 100)
        return [date.fromisoformat(d) for d in candidates[-weeks:]]
