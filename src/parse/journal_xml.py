"""Streaming parser for the UKIPO Trade Marks Journal XML.

The journal is a large XML document (well over 100 MB uncompressed), so it is
parsed with ``iterparse`` and each trade mark element is cleared as soon as it
has been converted.  Memory stays flat regardless of journal size.

The IPO's XML follows the WIPO ST.66 family of trade mark schemas, but the
namespace URI and some element spellings have varied over the years.  Rather
than binding to one namespace, this parser matches on *local* element names and
accepts a set of known aliases for each field.  Unknown or malformed records are
skipped and counted rather than aborting the run.
"""

from __future__ import annotations

import gzip
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import IO, Any

from lxml import etree

from src.errors import JournalParseError
from src.logging_setup import get_logger
from src.models import TrademarkRecord
from src.parse.nice import normalise_classes
from src.parse.normalise import summarise

log = get_logger(__name__)

# Elements that delimit one trade mark record.
RECORD_TAGS = {"TradeMark", "TradeMarkRecord", "Trademark", "MarkRecord"}

# local-name aliases for each field we care about, in priority order.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "trademark_number": (
        "ApplicationNumber",
        "RegistrationNumber",
        "MarkNumber",
        "TradeMarkNumber",
        "ApplicationNumberText",
    ),
    "filing_date": ("ApplicationDate", "FilingDate", "ApplicationFilingDate"),
    "publication_date": ("PublicationDate", "MarkEventDate", "JournalDate"),
    "mark_text": (
        "MarkVerbalElementText",
        "MarkSignificantVerbalElementText",
        "WordMarkSpecification",
        "MarkVerbalElement",
        "MarkText",
    ),
    "mark_type": ("MarkFeature", "MarkKind", "MarkType"),
    "mark_category": ("MarkCategory", "MarkCurrentStatusCode", "KindMark"),
    "status": ("MarkCurrentStatusCode", "MarkCurrentStatus", "Status"),
    "series_count": ("SeriesQuantity", "NumberOfMarksInSeries", "SeriesCount"),
}

APPLICANT_TAGS = ("ApplicantDetails", "Applicant", "ApplicantAddressBook")
APPLICANT_NAME_TAGS = (
    "FreeFormatNameDetails",
    "FreeFormatNameLine",
    "FreeFormatName",
    "OrganizationName",
    "EntityName",
    "Name",
    "FirstName",
    "LastName",
    "ApplicantName",
)
COUNTRY_TAGS = ("CountryCode", "Country", "ApplicantCountryCode")
POSTCODE_TAGS = ("PostcodeText", "PostalCode", "Postcode")
CLASS_NUMBER_TAGS = ("ClassNumber", "NiceClassNumber", "ClassDescriptionNumber")
GOODS_TEXT_TAGS = (
    "GoodsServicesDescription",
    "GoodsServicesDescriptionText",
    "ClassificationTermText",
    "GoodsServicesText",
)


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _iter_texts(element: etree._Element, names: tuple[str, ...]) -> Iterator[str]:
    wanted = set(names)
    for node in element.iter():
        if local_name(node.tag) in wanted:
            text = (node.text or "").strip()
            if text:
                yield text


def _first_text(element: etree._Element, names: tuple[str, ...]) -> str | None:
    for name in names:
        for value in _iter_texts(element, (name,)):
            return value
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime

            return datetime.strptime(v[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _applicant_details(element: etree._Element) -> dict[str, str | None]:
    """Pull the applicant's name / country / postcode from whichever shape is used."""
    blocks = [n for n in element.iter() if local_name(n.tag) in APPLICANT_TAGS]
    scope = blocks[0] if blocks else element
    name = _first_text(scope, APPLICANT_NAME_TAGS)
    if name is None and blocks:
        # Some shapes split names across First/Last name elements.
        parts = list(_iter_texts(scope, ("FirstName", "LastName")))
        name = " ".join(parts) if parts else None
    return {
        "applicant_name": name,
        "applicant_country": _first_text(scope, COUNTRY_TAGS),
        "applicant_postcode_area": (_first_text(scope, POSTCODE_TAGS) or "").split(" ")[0] or None,
    }


def _goods(element: etree._Element) -> tuple[list[int], str | None]:
    classes = normalise_classes(list(_iter_texts(element, CLASS_NUMBER_TAGS)))
    texts = list(_iter_texts(element, GOODS_TEXT_TAGS))
    goods_text = " ".join(texts).strip() or None
    return classes, goods_text


def element_to_record(
    element: etree._Element, journal_number: str, source_url: str | None, source_name: str
) -> TrademarkRecord | None:
    number = _first_text(element, FIELD_ALIASES["trademark_number"])
    if not number:
        return None
    classes, goods_text = _goods(element)
    applicant = _applicant_details(element)
    series_raw = _first_text(element, FIELD_ALIASES["series_count"])
    try:
        series_count = int(series_raw) if series_raw else 0
    except ValueError:
        series_count = 0
    return TrademarkRecord(
        trademark_number=number.strip(),
        mark_text=_first_text(element, FIELD_ALIASES["mark_text"]),
        mark_type=_first_text(element, FIELD_ALIASES["mark_type"]),
        mark_category=_first_text(element, FIELD_ALIASES["mark_category"]),
        filing_date=_parse_date(_first_text(element, FIELD_ALIASES["filing_date"])),
        publication_date=_parse_date(_first_text(element, FIELD_ALIASES["publication_date"])),
        nice_classes=classes,
        goods_text=summarise(goods_text, 2000),
        goods_text_available=bool(goods_text),
        series_count=series_count,
        status=_first_text(element, FIELD_ALIASES["status"]),
        journal_number=journal_number,
        source_url=source_url,
        source_name=source_name,
        **applicant,  # type: ignore[arg-type]
    )


def _open_stream(path: Path) -> IO[bytes]:
    """Open XML directly, or the first XML member of a zip, or a gzip file."""
    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise JournalParseError(f"No XML file inside {path}")
        return zf.open(names[0])  # type: ignore[return-value]
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rb")  # type: ignore[return-value]
    return path.open("rb")


def parse_journal_xml(
    path: str | Path,
    journal_number: str,
    source_url: str | None = None,
    source_name: str = "ukipo_journal_xml",
    max_records: int | None = None,
) -> Iterator[TrademarkRecord]:
    """Yield ``TrademarkRecord``s from a UKIPO journal XML file.

    Malformed individual records are skipped and logged; a document that cannot
    be parsed at all raises ``JournalParseError`` so the run fails closed.
    """
    path = Path(path)
    if not path.exists():
        raise JournalParseError(f"Journal file not found: {path}")

    skipped = 0
    yielded = 0
    stream = _open_stream(path)
    try:
        context = etree.iterparse(stream, events=("end",), recover=True, huge_tree=False)
        for _event, element in context:
            if local_name(element.tag) not in RECORD_TAGS:
                continue
            try:
                record = element_to_record(element, journal_number, source_url, source_name)
            except Exception as exc:  # one bad record must not kill the journal
                skipped += 1
                log.warning("journal.record.skipped", error=str(exc)[:200])
                record = None
            if record is not None:
                yielded += 1
                yield record
                if max_records and yielded >= max_records:
                    break
            else:
                skipped += 1
            # Free the element and its now-unneeded previous siblings.
            element.clear()
            parent = element.getparent()
            if parent is not None:
                while element.getprevious() is not None:
                    del parent[0]
    except etree.XMLSyntaxError as exc:
        raise JournalParseError(f"Could not parse {path}: {exc}") from exc
    finally:
        stream.close()

    log.info("journal.parsed", journal=journal_number, records=yielded, skipped=skipped)
    if yielded == 0:
        raise JournalParseError(
            f"No trade mark records found in {path}. The journal format may have changed; "
            f"expected an element whose local name is one of {sorted(RECORD_TAGS)}."
        )


def probe_structure(path: str | Path, limit: int = 40) -> dict[str, Any]:
    """Diagnostic: report the element names present, to adapt to a format change."""
    from collections import Counter

    counts: Counter[str] = Counter()
    stream = _open_stream(Path(path))
    try:
        context = etree.iterparse(stream, events=("start",), recover=True)
        for i, (_e, el) in enumerate(context):
            counts[local_name(el.tag)] += 1
            if i > 200_000:
                break
    finally:
        stream.close()
    return {"top_elements": counts.most_common(limit), "distinct": len(counts)}
