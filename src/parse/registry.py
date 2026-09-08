"""Maps a journal artifact to the parser its source declared."""

from __future__ import annotations

from collections.abc import Iterator

from src.errors import JournalParseError
from src.models import JournalArtifact, TrademarkRecord
from src.parse.journal_xml import parse_journal_xml
from src.parse.open_data import parse_open_data_week

PARSERS = {
    "journal_xml": parse_journal_xml,
    "open_data": parse_open_data_week,
}


def parse_artifact(
    artifact: JournalArtifact, parser: str, max_records: int | None = None
) -> Iterator[TrademarkRecord]:
    fn = PARSERS.get(parser)
    if fn is None:
        raise JournalParseError(f"Unknown parser: {parser!r}")
    return fn(
        artifact.local_path,
        journal_number=artifact.ref.journal_number,
        source_url=artifact.ref.source_url,
        source_name=artifact.ref.source_name,
        max_records=max_records,
    )
