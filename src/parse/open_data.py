"""Parser for the IPO Open Data release format.

Pipe-delimited, one row per trade mark, with ``Class1..Class45`` flag columns.
Published under the Open Government Licence v3.0.

The release has no goods and services text, so records produced here are marked
``goods_text_available=False``.  Downstream that is visible in the product
assessment and costs the record score points -- absence is represented, never
invented.
"""

from __future__ import annotations

import gzip
import io
import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from src.errors import JournalParseError
from src.logging_setup import get_logger
from src.models import TrademarkRecord
from src.parse.nice import classes_from_flags

log = get_logger(__name__)

_HYPERLINK_RE = re.compile(r'HYPERLINK\("([^"]+)"', re.IGNORECASE)
IPO_CASE_URL = "https://www.ipo.gov.uk/tmcase/Results/1/{number}"


def _open_text(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")  # type: ignore[return-value]
    # Slices we write are UTF-8; the raw IPO file is UTF-16.
    with path.open("rb") as probe:
        head = probe.read(4)
    encoding = "utf-16" if head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    return path.open("rt", encoding=encoding, errors="replace", newline="")  # type: ignore[return-value]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _source_url(row: dict[str, str]) -> str:
    link = row.get("Hyperlink") or ""
    m = _HYPERLINK_RE.search(link)
    if m:
        return m.group(1).replace("http://", "https://")
    return IPO_CASE_URL.format(number=(row.get("Trade Mark") or "").strip())


def parse_open_data_week(
    path: str | Path,
    journal_number: str,
    source_url: str | None = None,
    source_name: str = "ipo_open_data",
    max_records: int | None = None,
) -> Iterator[TrademarkRecord]:
    path = Path(path)
    if not path.exists():
        raise JournalParseError(f"Open Data slice not found: {path}")

    fh = _open_text(path)
    yielded = 0
    skipped = 0
    try:
        header = fh.readline().rstrip("\r\n")
        if "|" not in header:
            raise JournalParseError(f"{path} does not look like an IPO Open Data file")
        columns = [c.strip() for c in header.split("|")]
        for line in fh:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) < 5:
                skipped += 1
                continue
            row = dict(zip(columns, [p.strip() for p in parts], strict=False))
            number = (row.get("Trade Mark") or "").strip()
            if not number:
                skipped += 1
                continue
            try:
                series_count = int(row.get("No of Marks in Series") or 0)
            except ValueError:
                series_count = 0
            yield TrademarkRecord(
                trademark_number=number,
                mark_text=row.get("Mark Text") or None,
                mark_type=row.get("Mark Type") or None,
                mark_category=row.get("Category of Mark") or None,
                filing_date=_parse_date(row.get("Filed")),
                publication_date=_parse_date(row.get("Published")),
                applicant_name=row.get("Name") or None,
                applicant_country=row.get("Country") or None,
                applicant_region=row.get("Region") or None,
                applicant_postcode_area=(row.get("Postcode") or "").strip() or None,
                nice_classes=classes_from_flags(row),
                goods_text=None,
                goods_text_available=False,
                series_count=series_count,
                status=row.get("Status") or None,
                journal_number=journal_number,
                source_url=_source_url(row),
                source_name=source_name,
            )
            yielded += 1
            if max_records and yielded >= max_records:
                break
    finally:
        fh.close()

    log.info("opendata.parsed", journal=journal_number, records=yielded, skipped=skipped)
    if yielded == 0:
        raise JournalParseError(f"No records parsed from {path}")
