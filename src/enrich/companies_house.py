"""Companies House enrichment.

Two official routes, both first-party, selected by ``COMPANY_REGISTRY_PROVIDER``:

``api``
    The Companies House Public Data API (https://developer.company-information.service.gov.uk).
    Needs a free API key.  Rate-limited to 600 requests per five minutes, so the
    client backs off and caches.

``bulk``
    The free Companies House "Basic Company Data" bulk snapshot
    (https://download.companieshouse.gov.uk/en_output.html).  No key, no rate
    limit, no per-lookup cost -- a monthly download indexed into SQLite.  This is
    the default when no API key is present, which means company verification
    still works on day one.

``fixture``
    Test doubles.

``auto`` (default) picks ``api`` if a key is set, else ``bulk`` if an index
exists, else ``fixture``.

Only corporate information is collected.  No officer records, no director home
addresses, no personal data beyond the applicant name that is already public on
the trade mark register.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import zipfile
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.enrich.matching import CandidateCompany, best_match
from src.errors import ProviderError, RateLimitedError
from src.logging_setup import get_logger
from src.models import CompanyMatch
from src.parse.normalise import company_name_key, normalise_company_name
from src.settings import FIXTURES_DIR, Settings, get_settings

log = get_logger(__name__)

CH_COMPANY_URL = "https://find-and-update.company-information.service.gov.uk/company/{number}"


def _parse_ch_date(value: str | None) -> date | None:
    if not value:
        return None
    v = value.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


class CompanyRegistry(ABC):
    name = "none"

    @abstractmethod
    def find_candidates(self, applicant_name: str) -> list[CandidateCompany]: ...

    def match(self, applicant_name: str | None) -> CompanyMatch:
        if not applicant_name:
            return CompanyMatch(matched=False, match_method="no_applicant_name", provider=self.name)
        try:
            candidates = self.find_candidates(applicant_name)
        except (ProviderError, httpx.HTTPError) as exc:
            log.warning("ch.lookup_failed", applicant=applicant_name[:80], error=str(exc)[:200])
            return CompanyMatch(
                matched=False,
                match_method="provider_error",
                provider=self.name,
                error=str(exc)[:400],
            )
        return best_match(applicant_name, candidates, provider=self.name)


# ---------------------------------------------------------------------------
# API provider
# ---------------------------------------------------------------------------


class CompaniesHouseApiRegistry(CompanyRegistry):
    name = "companies_house_api"

    def __init__(self, settings: Settings, cache_path: Path | None = None) -> None:
        self.settings = settings
        self.base_url = settings.companies_house_base_url.rstrip("/")
        self.cache_path = cache_path or Path(settings.cache_dir) / "companies_house_api.sqlite"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_cache()

    def _init_cache(self) -> None:
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_cache ("
                "  query_key TEXT PRIMARY KEY, payload TEXT NOT NULL, cached_at TEXT NOT NULL)"
            )

    def _cache_get(self, key: str) -> list[dict] | None:
        with sqlite3.connect(self.cache_path) as conn:
            row = conn.execute(
                "SELECT payload FROM search_cache WHERE query_key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _cache_put(self, key: str, payload: list[dict]) -> None:
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache (query_key, payload, cached_at) VALUES (?,?,?)",
                (key, json.dumps(payload), datetime.utcnow().isoformat()),
            )

    def _search(self, query: str) -> list[dict]:
        @retry(
            reraise=True,
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=3, min=3, max=45),
            retry=retry_if_exception_type((RateLimitedError, httpx.TransportError, ProviderError)),
        )
        def _do() -> list[dict]:
            with httpx.Client(timeout=30) as client:
                resp = client.get(
                    f"{self.base_url}/search/companies",
                    params={"q": query, "items_per_page": 20},
                    auth=(self.settings.companies_house_api_key, ""),
                )
            if resp.status_code == 429:
                raise RateLimitedError("Companies House rate limit (600 requests / 5 minutes)")
            if resp.status_code == 401:
                raise ProviderError("Companies House rejected the API key (401)")
            if resp.status_code >= 500:
                raise ProviderError(f"Companies House {resp.status_code}")
            resp.raise_for_status()
            return resp.json().get("items", [])

        return _do()

    def find_candidates(self, applicant_name: str) -> list[CandidateCompany]:
        query = normalise_company_name(applicant_name) or applicant_name
        cached = self._cache_get(query)
        items = cached if cached is not None else self._search(query)
        if cached is None:
            self._cache_put(query, items)
        out: list[CandidateCompany] = []
        for item in items:
            number = item.get("company_number") or ""
            address = item.get("address") or {}
            out.append(
                CandidateCompany(
                    company_name=item.get("title") or "",
                    company_number=number,
                    company_status=item.get("company_status"),
                    company_category=item.get("company_type"),
                    incorporation_date=_parse_ch_date(item.get("date_of_creation")),
                    dissolution_date=_parse_ch_date(item.get("date_of_cessation")),
                    sic_codes=tuple(item.get("sic_codes") or ()),
                    region=address.get("region") or address.get("country"),
                    post_town=address.get("locality"),
                    country=address.get("country"),
                    source_url=CH_COMPANY_URL.format(number=number) if number else None,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Bulk snapshot provider
# ---------------------------------------------------------------------------

BULK_INDEX_URL = "https://download.companieshouse.gov.uk/en_output.html"
BULK_FILE_URL = "https://download.companieshouse.gov.uk/{filename}"


class CompaniesHouseBulkRegistry(CompanyRegistry):
    """Reads the free monthly Companies House snapshot from a local SQLite index."""

    name = "companies_house_bulk"

    def __init__(self, index_path: str | Path) -> None:
        self.index_path = Path(index_path)
        if not self.index_path.exists():
            raise ProviderError(
                f"Companies House bulk index not found at {self.index_path}. "
                "Build it with: python -m src.pipeline build-company-index"
            )
        self.conn = sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def find_candidates(self, applicant_name: str) -> list[CandidateCompany]:
        key = company_name_key(applicant_name)
        if not key:
            return []
        rows = self.conn.execute(
            "SELECT * FROM companies WHERE name_key = ? LIMIT 25", (key,)
        ).fetchall()
        if not rows:
            # Fall back to the leading distinctive word, then filter by similarity.
            tokens = normalise_company_name(applicant_name).split()
            if tokens:
                prefix = tokens[0]
                if len(prefix) >= 4:
                    rows = self.conn.execute(
                        "SELECT * FROM companies WHERE name_key LIKE ? LIMIT 25", (prefix + "%",)
                    ).fetchall()
        return [self._row_to_candidate(r) for r in rows]

    @staticmethod
    def _row_to_candidate(row: sqlite3.Row) -> CandidateCompany:
        return CandidateCompany(
            company_name=row["company_name"],
            company_number=row["company_number"],
            company_status=row["company_status"],
            company_category=row["company_category"],
            incorporation_date=date.fromisoformat(row["incorporation_date"])
            if row["incorporation_date"]
            else None,
            dissolution_date=date.fromisoformat(row["dissolution_date"])
            if row["dissolution_date"]
            else None,
            sic_codes=tuple(json.loads(row["sic_codes"] or "[]")),
            region=row["region"],
            post_town=row["post_town"],
            country=row["country"],
            accounts_category=row["accounts_category"],
            source_url=CH_COMPANY_URL.format(number=row["company_number"]),
        )


def build_bulk_index(
    source: str | Path,
    index_path: str | Path,
    progress_every: int = 500_000,
) -> int:
    """Index a Companies House bulk CSV (or its zip) into SQLite.

    Only the corporate fields LaunchTrace uses are stored: no officers, no
    personal data.
    """
    source = Path(source)
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp)
    conn.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE companies (
            name_key TEXT NOT NULL,
            company_name TEXT NOT NULL,
            company_number TEXT NOT NULL,
            company_status TEXT,
            company_category TEXT,
            incorporation_date TEXT,
            dissolution_date TEXT,
            sic_codes TEXT,
            region TEXT,
            post_town TEXT,
            country TEXT,
            accounts_category TEXT
        );
        """
    )

    def _rows(handle: IO[str]):  # type: ignore[no-untyped-def]
        reader = csv.DictReader(handle)
        reader.fieldnames = [f.strip() for f in (reader.fieldnames or [])]
        for row in reader:
            row = {(k.strip() if k else k): v for k, v in row.items()}
            name = (row.get("CompanyName") or "").strip()
            number = (row.get("CompanyNumber") or "").strip()
            if not name or not number:
                continue
            sic = [
                (row.get(f"SICCode.SicText_{i}") or "").split(" - ")[0].strip() for i in range(1, 5)
            ]
            yield (
                company_name_key(name),
                name,
                number,
                (row.get("CompanyStatus") or "").strip() or None,
                (row.get("CompanyCategory") or "").strip() or None,
                (_parse_ch_date(row.get("IncorporationDate")) or "")
                and _parse_ch_date(row.get("IncorporationDate")).isoformat(),  # type: ignore[union-attr]
                (_parse_ch_date(row.get("DissolutionDate")) or "")
                and _parse_ch_date(row.get("DissolutionDate")).isoformat(),  # type: ignore[union-attr]
                json.dumps([s for s in sic if s]),
                (row.get("RegAddress.County") or "").strip() or None,
                (row.get("RegAddress.PostTown") or "").strip() or None,
                (row.get("RegAddress.Country") or "").strip() or None,
                (row.get("Accounts.AccountCategory") or "").strip() or None,
            )

    count = 0
    batch: list[tuple] = []
    insert = "INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"

    def _consume(handle: IO[str]) -> None:
        nonlocal count, batch
        for row in _rows(handle):
            batch.append(row)
            count += 1
            if len(batch) >= 20_000:
                conn.executemany(insert, batch)
                batch = []
            if progress_every and count % progress_every == 0:
                log.info("ch.index.progress", rows=count)

    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            inner = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
            with zf.open(inner) as raw:
                import io

                _consume(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""))
    else:
        with source.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            _consume(handle)

    if batch:
        conn.executemany(insert, batch)
    conn.commit()
    log.info("ch.index.indexing", rows=count)
    conn.execute("CREATE INDEX ix_companies_name_key ON companies(name_key)")
    conn.execute("CREATE INDEX ix_companies_number ON companies(company_number)")
    conn.commit()
    conn.close()
    tmp.replace(index_path)
    log.info("ch.index.built", rows=count, path=str(index_path))
    return count


def latest_bulk_download_url(http_get_text) -> str | None:  # type: ignore[no-untyped-def]
    """Find the newest one-file snapshot link on the Companies House download page."""
    import re

    html = http_get_text(BULK_INDEX_URL)
    names = re.findall(r'href="(BasicCompanyDataAsOneFile-[\d-]+\.zip)"', html)
    if not names:
        return None
    return BULK_FILE_URL.format(filename=sorted(names)[-1])


# ---------------------------------------------------------------------------
# Fixture provider
# ---------------------------------------------------------------------------


class FixtureCompanyRegistry(CompanyRegistry):
    name = "fixture"

    def __init__(
        self, path: Path | None = None, records: list[dict[str, Any]] | None = None
    ) -> None:
        if records is None:
            path = path or FIXTURES_DIR / "companies_house" / "companies.json"
            records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        self.records = records

    def find_candidates(self, applicant_name: str) -> list[CandidateCompany]:
        key = company_name_key(applicant_name)
        out = []
        for r in self.records:
            if company_name_key(r["company_name"]) == key or key in company_name_key(
                r["company_name"]
            ):
                out.append(
                    CandidateCompany(
                        company_name=r["company_name"],
                        company_number=r["company_number"],
                        company_status=r.get("company_status"),
                        company_category=r.get("company_category"),
                        incorporation_date=_parse_ch_date(r.get("incorporation_date")),
                        dissolution_date=_parse_ch_date(r.get("dissolution_date")),
                        sic_codes=tuple(r.get("sic_codes") or ()),
                        region=r.get("region"),
                        post_town=r.get("post_town"),
                        country=r.get("country"),
                        accounts_category=r.get("accounts_category"),
                        source_url=CH_COMPANY_URL.format(number=r["company_number"]),
                    )
                )
        return out


class NullCompanyRegistry(CompanyRegistry):
    name = "none"

    def find_candidates(self, applicant_name: str) -> list[CandidateCompany]:
        return []

    def match(self, applicant_name: str | None) -> CompanyMatch:
        return CompanyMatch(
            matched=False,
            match_method="registry_unavailable",
            provider="none",
            error="No Companies House provider is configured",
            match_evidence=[
                "Company verification unavailable: set COMPANIES_HOUSE_API_KEY, or build the "
                "free bulk index with 'python -m src.pipeline build-company-index'"
            ],
        )


def get_company_registry(settings: Settings | None = None) -> CompanyRegistry:
    settings = settings or get_settings()
    choice = settings.company_registry_provider
    if choice == "api":
        return CompaniesHouseApiRegistry(settings)
    if choice == "bulk":
        return CompaniesHouseBulkRegistry(settings.companies_house_bulk_index)
    if choice == "fixture":
        return FixtureCompanyRegistry()
    # auto
    if settings.companies_house_api_key:
        return CompaniesHouseApiRegistry(settings)
    if Path(settings.companies_house_bulk_index).exists():
        return CompaniesHouseBulkRegistry(settings.companies_house_bulk_index)
    log.warning("ch.registry.unavailable", reason="no api key and no bulk index")
    return NullCompanyRegistry()
