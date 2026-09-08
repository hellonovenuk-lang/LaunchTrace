"""Fixture journal source used by the smoke test and the test suite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.errors import JournalRetrievalError
from src.ingest.base import JournalSource
from src.ingest.cache import sha256_file
from src.ingest.discovery import date_for_journal_number, journal_number_for_date
from src.models import JournalArtifact, JournalRef
from src.settings import FIXTURES_DIR, Settings

FIXTURE_JOURNAL_DIR = FIXTURES_DIR / "journals"


class FixtureJournalSource(JournalSource):
    name = "fixture_journal_xml"
    parser = "journal_xml"

    def __init__(self, settings: Settings | None = None, directory: Path | None = None) -> None:
        super().__init__(settings)
        self.dir = directory or FIXTURE_JOURNAL_DIR

    def _files(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for p in sorted(self.dir.glob("*.xml")):
            out[p.stem] = p
        return out

    def _ref(self, number: str) -> JournalRef:
        return JournalRef(
            journal_number=number,
            publication_date=date_for_journal_number(number),
            source_name=self.name,
            source_url=f"fixture://{number}",
        )

    def latest_ref(self) -> JournalRef:
        files = self._files()
        if not files:
            raise JournalRetrievalError(f"No fixture journals in {self.dir}")
        return self._ref(sorted(files)[-1])

    def ref_for(
        self, journal_number: str | None = None, publication_date: date | None = None
    ) -> JournalRef:
        if journal_number is None and publication_date is not None:
            journal_number = journal_number_for_date(publication_date)
        if journal_number is None:
            return self.latest_ref()
        if journal_number not in self._files():
            raise JournalRetrievalError(f"No fixture journal {journal_number} in {self.dir}")
        return self._ref(journal_number)

    def available_refs(self, limit: int = 12) -> list[JournalRef]:
        return [self._ref(n) for n in sorted(self._files(), reverse=True)][:limit]

    def fetch(self, ref: JournalRef) -> JournalArtifact:
        path = self._files().get(ref.journal_number)
        if path is None:
            raise JournalRetrievalError(f"No fixture journal {ref.journal_number}")
        return JournalArtifact(
            ref=ref,
            local_path=str(path),
            byte_size=path.stat().st_size,
            sha256=sha256_file(path),
            from_cache=True,
        )

    @staticmethod
    def unused(_: date) -> None:  # pragma: no cover - keeps date import meaningful
        return None
