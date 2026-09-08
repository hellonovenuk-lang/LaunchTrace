"""Journal source abstraction.

A ``JournalSource`` knows how to (a) say which journals exist and (b) hand back
a local file plus integrity metadata for one of them.  Parsing is a separate
concern -- each source declares which parser its artifacts need.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from src.models import JournalArtifact, JournalRef
from src.settings import Settings, get_settings


class JournalSource(ABC):
    """Retrieves weekly journal artifacts from somewhere."""

    name: str = "base"
    parser: str = "journal_xml"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @abstractmethod
    def latest_ref(self) -> JournalRef:
        """The most recent journal this source can supply."""

    @abstractmethod
    def ref_for(
        self, journal_number: str | None = None, publication_date: date | None = None
    ) -> JournalRef:
        """Resolve an explicit journal identifier or publication date to a ref."""

    @abstractmethod
    def available_refs(self, limit: int = 12) -> list[JournalRef]:
        """Journals this source can supply, newest first."""

    @abstractmethod
    def fetch(self, ref: JournalRef) -> JournalArtifact:
        """Retrieve the journal, returning a local path and integrity metadata."""


def get_source(name: str | None = None, settings: Settings | None = None) -> JournalSource:
    """Factory. Imports lazily so a missing optional dependency cannot break the CLI."""
    settings = settings or get_settings()
    name = name or settings.journal_source
    if name == "ukipo_http":
        from src.ingest.ukipo_http import UkipoJournalHttpSource

        return UkipoJournalHttpSource(settings)
    if name == "open_data":
        from src.ingest.open_data import IpoOpenDataSource

        return IpoOpenDataSource(settings)
    if name == "local":
        from src.ingest.local_file import LocalJournalSource

        return LocalJournalSource(settings)
    if name == "fixture":
        from src.ingest.fixture import FixtureJournalSource

        return FixtureJournalSource(settings)
    raise ValueError(f"Unknown journal source: {name!r}")
