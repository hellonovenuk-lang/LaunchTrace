"""Journal source backed by files an operator downloaded by hand.

Used when ipo.gov.uk cannot be reached from the machine running the pipeline
(for example when the IPO's bot protection challenges the network).  Drop the
journal file into ``JOURNAL_LOCAL_DIR`` named for its journal number or
publication date and the pipeline treats it exactly like a live download.

Accepted filenames::

    2025-052.xml   2025-052.zip   2025-12-26.xml   tmj-2025-052.xml
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from src.errors import JournalRetrievalError
from src.ingest.base import JournalSource
from src.ingest.cache import sha256_file
from src.ingest.discovery import date_for_journal_number, journal_number_for_date
from src.models import JournalArtifact, JournalRef
from src.settings import DATA_DIR, Settings

_NUMBER_RE = re.compile(r"(\d{4})-(\d{2,3})")


class LocalJournalSource(JournalSource):
    name = "ukipo_journal_xml"
    parser = "journal_xml"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.dir = Path(self.settings.journal_local_dir or (DATA_DIR / "journals"))
        self.dir.mkdir(parents=True, exist_ok=True)

    def _files(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for p in sorted(self.dir.iterdir()):
            if p.suffix.lower() not in {".xml", ".zip", ".gz"}:
                continue
            m = _NUMBER_RE.search(p.stem)
            if not m:
                continue
            token = m.group(0)
            try:
                # A full ISO date in the name is a publication date.
                if len(p.stem) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", p.stem):
                    number = journal_number_for_date(date.fromisoformat(p.stem[:10]))
                else:
                    year, ordinal = token.split("-")
                    number = f"{year}-{int(ordinal):03d}"
            except ValueError:
                continue
            found[number] = p
        return found

    def _ref(self, number: str, path: Path) -> JournalRef:
        return JournalRef(
            journal_number=number,
            publication_date=date_for_journal_number(number),
            source_name=self.name,
            source_url=path.as_uri(),
        )

    def latest_ref(self) -> JournalRef:
        files = self._files()
        if not files:
            raise JournalRetrievalError(
                f"No journal files found in {self.dir}. Place the downloaded journal "
                "there named for its journal number, e.g. 2025-052.xml"
            )
        number = sorted(files)[-1]
        return self._ref(number, files[number])

    def ref_for(
        self, journal_number: str | None = None, publication_date: date | None = None
    ) -> JournalRef:
        files = self._files()
        if journal_number is None and publication_date is not None:
            journal_number = journal_number_for_date(publication_date)
        if journal_number is None:
            return self.latest_ref()
        if journal_number not in files:
            raise JournalRetrievalError(
                f"No local journal file for {journal_number} in {self.dir}. "
                f"Available: {sorted(files) or 'none'}"
            )
        return self._ref(journal_number, files[journal_number])

    def available_refs(self, limit: int = 12) -> list[JournalRef]:
        files = self._files()
        return [self._ref(n, files[n]) for n in sorted(files, reverse=True)][:limit]

    def fetch(self, ref: JournalRef) -> JournalArtifact:
        files = self._files()
        path = files.get(ref.journal_number)
        if path is None:
            raise JournalRetrievalError(f"No local journal file for {ref.journal_number}")
        return JournalArtifact(
            ref=ref,
            local_path=str(path),
            content_type="application/zip" if path.suffix == ".zip" else "application/xml",
            byte_size=path.stat().st_size,
            sha256=sha256_file(path),
            from_cache=True,
        )
