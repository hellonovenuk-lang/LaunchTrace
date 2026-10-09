"""Which past Trade Marks Journals can still be downloaded from ipo.gov.uk?

A backtest needs past journals. This probe answers "how far back can we go?"
without downloading anything: one ``HEAD`` per candidate journal URL (a 1 KB
ranged ``GET`` when HEAD is refused or the answer is ambiguous), and a binary
search for the earliest journal that is still served, instead of walking
hundreds of weeks.

Rules (config/backtest.json -> ``probe``):

* at least ``min_delay_seconds`` between any two requests, a per-request
  timeout and the honest ``UKIPO_USER_AGENT``;
* a hard cap of ``max_requests`` per invocation;
* the walk back from the newest journal to the first one that is served stops
  after ``max_consecutive_misses``;
* ``max_consecutive_blocked`` captcha / rate-limit answers stop everything.

ipo.gov.uk answers **403, not 404**, for a file that does not exist, and also
answers 403 with a captcha page when it is blocking a network. A 403 is
therefore only read as "missing" once this session has seen the host serve a
journal; before that, a small ranged GET looks at the body to tell the two
apart.

The binary search assumes the archive is contiguous (every week from the
earliest served journal to today is served). The result records that
assumption and every request made, so a human can check it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.ingest.discovery import (
    date_for_journal_number,
    journal_number_for_date,
    latest_expected_journal,
)
from src.logging_setup import get_logger
from src.settings import REPORTS_DIR, Settings, get_settings, load_config

log = get_logger(__name__)

AVAILABLE = "available"
MISSING = "missing"
BLOCKED = "blocked"
ERROR = "error"
SKIPPED = "skipped"

_BLOCK_MARKERS = (
    b"captcha",
    b"are you a robot",
    b"access denied",
    b"request unsuccessful",
    b"incapsula",
    b"cf-chl",
    b"challenge-platform",
)


def journal_numbers_between(first: str, last: str) -> list[str]:
    """Every journal number from ``first`` to ``last`` inclusive, oldest first."""
    start, end = date_for_journal_number(first), date_for_journal_number(last)
    if start > end:
        start, end = end, start
    out: list[str] = []
    day = start
    while day <= end:
        out.append(journal_number_for_date(day))
        day += timedelta(weeks=1)
    return out


@dataclass
class ProbeResult:
    journal_number: str
    url: str
    outcome: str
    status: int | None = None
    head_status: int | None = None
    method: str = "HEAD"
    content_type: str | None = None
    content_length: int | None = None
    last_modified: str | None = None
    error: str | None = None
    probed_at: str = ""


@dataclass
class ProbeSummary:
    outcome: str  # found | none_available | blocked | budget_exhausted
    earliest_available: str | None
    earliest_exact: bool
    latest_available: str | None
    range_from: str
    range_to: str
    requests_made: int
    results: list[ProbeResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class JournalAvailabilityProber:
    """Polite availability checks for journal URLs, through an injectable client."""

    def __init__(
        self,
        client: httpx.Client,
        base_url: str,
        *,
        filename: str = "jnl.xml",
        min_delay_seconds: float = 2.5,
        max_requests: int = 24,
        max_consecutive_misses: int = 6,
        max_consecutive_blocked: int = 3,
        range_bytes: int = 1023,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.filename = filename
        self.min_delay = max(float(min_delay_seconds), 0.0)
        self.max_requests = int(max_requests)
        self.max_misses = int(max_consecutive_misses)
        self.max_blocked = int(max_consecutive_blocked)
        self.range_bytes = int(range_bytes)
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.requests_made = 0
        self.host_serves_journals = False
        self._consecutive_blocked = 0

    # -- requests -----------------------------------------------------------
    def url_for(self, journal_number: str) -> str:
        return f"{self.base_url}/{journal_number}/{self.filename}"

    @property
    def budget_left(self) -> int:
        return self.max_requests - self.requests_made

    @property
    def blocked(self) -> bool:
        return self._consecutive_blocked >= self.max_blocked

    def _wait(self) -> None:
        if self._last_request is not None:
            elapsed = self._clock() - self._last_request
            if elapsed < self.min_delay:
                self._sleep(self.min_delay - elapsed)
        self._last_request = self._clock()

    def _request(
        self, method: str, url: str, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        self._wait()
        self.requests_made += 1
        return self.client.request(method, url, headers=headers)

    # -- classification -------------------------------------------------------
    @staticmethod
    def _looks_blocked(body: bytes) -> bool:
        lowered = body[:4096].lower()
        return any(marker in lowered for marker in _BLOCK_MARKERS)

    @staticmethod
    def _looks_like_journal(body: bytes, content_type: str) -> bool:
        head = body[:512].lstrip()
        if head.startswith(b"PK"):
            return True  # a zip
        if head.startswith(b"<?xml") or head.startswith(b"\xef\xbb\xbf<?xml"):
            return True
        return "xml" in content_type and b"<html" not in head.lower()

    def probe(self, journal_number: str) -> ProbeResult:
        """One journal: HEAD, then a ranged GET if HEAD cannot answer on its own."""
        url = self.url_for(journal_number)
        result = ProbeResult(
            journal_number=journal_number,
            url=url,
            outcome=ERROR,
            probed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        if self.budget_left <= 0:
            result.outcome = SKIPPED
            result.error = "request budget exhausted"
            return result
        try:
            resp = self._request("HEAD", url)
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {str(exc)[:160]}"
            return self._record(result)
        result.status = result.head_status = resp.status_code
        result.content_type = resp.headers.get("content-type")
        result.last_modified = resp.headers.get("last-modified")
        length = resp.headers.get("content-length")
        result.content_length = int(length) if length and length.isdigit() else None
        content_type = (result.content_type or "").lower()

        if resp.status_code == 200 and "html" not in content_type:
            result.outcome = AVAILABLE
            return self._record(result)
        if resp.status_code in (404, 410):
            result.outcome = MISSING
            return self._record(result)
        if resp.status_code == 429:
            result.outcome = BLOCKED
            result.error = "rate limited (429)"
            return self._record(result)
        if resp.status_code == 403 and self.host_serves_journals:
            # The host has served us a journal this session, so it is not
            # blocking this network: its 403 means "no such file".
            result.outcome = MISSING
            return self._record(result)
        if resp.status_code >= 500:
            result.error = f"server error {resp.status_code}"
            return self._record(result)

        # 200 with HTML, 403 before we know what 403 means here, 405/501 (no
        # HEAD), or anything else: look at the first kilobyte.
        if self.budget_left <= 0:
            result.error = "ambiguous HEAD and no request budget left for a ranged GET"
            return self._record(result)
        try:
            got = self._request("GET", url, headers={"Range": f"bytes=0-{self.range_bytes}"})
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {str(exc)[:160]}"
            return self._record(result)
        result.method = "HEAD+GET-range"
        result.status = got.status_code
        body = got.content[: self.range_bytes + 1]
        content_type = (got.headers.get("content-type") or "").lower()
        result.content_type = got.headers.get("content-type") or result.content_type
        if got.status_code == 429 or self._looks_blocked(body):
            result.outcome = BLOCKED
            result.error = "bot protection / captcha page" if got.status_code != 429 else "429"
        elif got.status_code in (200, 206) and self._looks_like_journal(body, content_type):
            result.outcome = AVAILABLE
            total = got.headers.get("content-range", "").rpartition("/")[2]
            if total.isdigit():
                result.content_length = int(total)
        elif got.status_code in (403, 404, 410) or got.status_code in (200, 206):
            result.outcome = MISSING
        else:
            result.error = f"unexpected status {got.status_code}"
        return self._record(result)

    def _record(self, result: ProbeResult) -> ProbeResult:
        if result.outcome == AVAILABLE:
            self.host_serves_journals = True
        self._consecutive_blocked = (
            self._consecutive_blocked + 1 if result.outcome == BLOCKED else 0
        )
        log.info(
            "backtest.probe",
            journal=result.journal_number,
            outcome=result.outcome,
            status=result.status,
            requests=self.requests_made,
        )
        return result

    # -- search -----------------------------------------------------------------
    def find_earliest(self, numbers: list[str]) -> ProbeSummary:
        """Earliest served journal among ``numbers`` (oldest first)."""
        if not numbers:
            raise ValueError("no journal numbers to probe")
        summary = ProbeSummary(
            outcome="none_available",
            earliest_available=None,
            earliest_exact=False,
            latest_available=None,
            range_from=numbers[0],
            range_to=numbers[-1],
            requests_made=0,
        )
        probed: dict[int, ProbeResult] = {}

        def run(index: int) -> ProbeResult:
            res = self.probe(numbers[index])
            probed[index] = res
            summary.results.append(res)
            return res

        # 1. Walk back from the newest journal to the first one that is served.
        anchor: int | None = None
        for index in range(len(numbers) - 1, -1, -1):
            if self.budget_left <= 0:
                summary.outcome = "budget_exhausted"
                break
            res = run(index)
            if res.outcome == AVAILABLE:
                anchor = index
                break
            if self.blocked:
                summary.outcome = BLOCKED
                summary.notes.append(
                    f"{self.max_blocked} consecutive blocked answers (captcha or 429): stopped."
                )
                break
            misses = len(numbers) - index  # every journal walked so far was a miss
            if misses >= self.max_misses:
                summary.notes.append(
                    f"{misses} consecutive misses walking back from {numbers[-1]}: stopped."
                )
                break

        if anchor is None:
            summary.requests_made = self.requests_made
            if summary.outcome == "none_available" and any(
                r.outcome == BLOCKED for r in summary.results
            ):
                summary.outcome = BLOCKED
            return summary

        summary.latest_available = numbers[anchor]

        # 2. Binary search for the earliest served journal in [0, anchor].
        lo, hi = 0, anchor
        while lo < hi:
            if self.budget_left <= 0 or self.blocked:
                break
            mid = (lo + hi) // 2
            res = run(mid)
            if res.outcome == AVAILABLE:
                hi = mid
            elif res.outcome == MISSING:
                lo = mid + 1
            else:
                # Blocked or an error: we cannot tell, so we cannot narrow the
                # range honestly. Stop with the best upper bound we have.
                summary.notes.append(
                    f"{numbers[mid]} answered '{res.outcome}' ({res.error or res.status}); "
                    "search stopped there."
                )
                break
        summary.earliest_available = numbers[hi]
        summary.earliest_exact = lo == hi
        summary.outcome = "found" if summary.earliest_exact else "budget_exhausted"
        if summary.earliest_exact and hi == 0:
            summary.notes.append(
                f"The oldest journal in the probed range ({numbers[0]}) is served: the archive "
                "may go back further than this range."
            )
        if not summary.earliest_exact:
            summary.notes.append(
                f"Not narrowed to a single week: the earliest served journal is between "
                f"{numbers[lo]} and {numbers[hi]} (upper bound reported)."
            )
        summary.notes.append(
            "Binary search assumes every week from the earliest served journal to the "
            "latest is served (a contiguous archive)."
        )
        summary.requests_made = self.requests_made
        return summary


# ---------------------------------------------------------------------------
# IPO Open Data route
# ---------------------------------------------------------------------------


def check_open_data(client: httpx.Client, wait: Callable[[], None] | None = None) -> dict[str, Any]:
    """HEAD the Open Data snapshot (size and date) and read its landing page's links.

    Downloads nothing large: one HEAD, and one GET of an HTML landing page.
    """
    import re

    from src.ingest.open_data import JOURNAL_DIR, OPEN_DATA_LANDING, OPEN_DATA_ZIP

    out: dict[str, Any] = {
        "snapshot_url": OPEN_DATA_ZIP,
        "landing_page": OPEN_DATA_LANDING,
        "local_weeks": sorted(
            p.name.replace("opendata_week_", "").split(".")[0]
            for p in JOURNAL_DIR.glob("opendata_week_*.txt*")
        ),
    }
    try:
        if wait:
            wait()
        resp = client.head(OPEN_DATA_ZIP)
        out["snapshot_status"] = resp.status_code
        out["snapshot_content_length"] = resp.headers.get("content-length")
        out["snapshot_last_modified"] = resp.headers.get("last-modified")
    except httpx.HTTPError as exc:
        out["snapshot_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    try:
        if wait:
            wait()
        page = client.get(OPEN_DATA_LANDING)
        out["landing_status"] = page.status_code
        if page.status_code == 200:
            links = re.findall(r'href="([^"]+\.(?:zip|csv|txt))"', page.text, re.IGNORECASE)
            out["landing_data_links"] = sorted(set(links))[:20]
            updated = re.findall(r"(\d{1,2} \w+ \d{4})", page.text)
            out["landing_dates_mentioned"] = sorted(set(updated))[:20]
    except httpx.HTTPError as exc:
        out["landing_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return out


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def default_range(cfg: dict[str, Any], today: date | None = None) -> tuple[str, str]:
    latest = latest_expected_journal(today)
    years = int(cfg.get("default_lookback_years", 6))
    first = latest - timedelta(weeks=52 * years)
    return journal_number_for_date(first), journal_number_for_date(latest)


def run_probe(
    *,
    first: str | None = None,
    last: str | None = None,
    max_requests: int | None = None,
    out_path: Path | None = None,
    client: httpx.Client | None = None,
    settings: Settings | None = None,
    sleep: Callable[[float], None] = time.sleep,
    check_open_data_route: bool | None = None,
) -> dict[str, Any]:
    """Probe, write ``availability.json``, and return what was written."""
    settings = settings or get_settings()
    cfg = load_config("backtest.json")["probe"]
    default_first, default_last = default_range(cfg)
    first = first or default_first
    last = last or default_last
    numbers = journal_numbers_between(first, last)
    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(float(cfg.get("timeout_seconds", 20))),
        headers={"User-Agent": settings.ukipo_user_agent},
        follow_redirects=True,
    )
    try:
        prober = JournalAvailabilityProber(
            client,
            settings.ukipo_journal_base_url,
            filename=str(cfg.get("filename", "jnl.xml")),
            min_delay_seconds=float(cfg.get("min_delay_seconds", 2.5)),
            max_requests=int(max_requests or cfg.get("max_requests", 24)),
            max_consecutive_misses=int(cfg.get("max_consecutive_misses", 6)),
            max_consecutive_blocked=int(cfg.get("max_consecutive_blocked", 3)),
            range_bytes=int(cfg.get("range_get_bytes", 1023)),
            sleep=sleep,
        )
        summary = prober.find_earliest(numbers)
        open_data: dict[str, Any] | None = None
        do_open_data = (
            bool(cfg.get("open_data_check", True))
            if check_open_data_route is None
            else check_open_data_route
        )
        if do_open_data:
            open_data = check_open_data(client, wait=prober._wait)
        total_requests = prober.requests_made + (2 if open_data is not None else 0)
    finally:
        if owns_client:
            client.close()

    payload: dict[str, Any] = {
        "probed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": settings.ukipo_journal_base_url,
        "url_pattern": f"{settings.ukipo_journal_base_url.rstrip('/')}/<YYYY-NNN>/"
        f"{cfg.get('filename', 'jnl.xml')}",
        "user_agent": settings.ukipo_user_agent,
        "min_delay_seconds": float(cfg.get("min_delay_seconds", 2.5)),
        "range": {"from": summary.range_from, "to": summary.range_to, "weeks": len(numbers)},
        "outcome": summary.outcome,
        "earliest_available": summary.earliest_available,
        "earliest_available_publication_date": (
            date_for_journal_number(summary.earliest_available).isoformat()
            if summary.earliest_available
            else None
        ),
        "earliest_exact": summary.earliest_exact,
        "latest_available": summary.latest_available,
        "journal_requests": summary.requests_made,
        "total_requests": total_requests,
        "notes": summary.notes,
        "results": [asdict(r) for r in summary.results],
        "open_data": open_data,
    }
    out_path = out_path or (REPORTS_DIR / "backtest" / "availability.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def format_probe(payload: dict[str, Any]) -> str:
    lines = [
        f"Probed {payload['range']['from']} .. {payload['range']['to']} "
        f"({payload['range']['weeks']} weeks) with {payload['journal_requests']} journal requests.",
        f"Outcome: {payload['outcome']}",
        f"Latest served:   {payload['latest_available'] or '-'}",
        f"Earliest served: {payload['earliest_available'] or '-'}"
        + ("" if payload["earliest_exact"] else " (upper bound)"),
    ]
    for r in payload["results"]:
        lines.append(f"  {r['journal_number']}  {r['outcome']:<10} {r['status']}  {r['method']}")
    for note in payload["notes"]:
        lines.append(f"Note: {note}")
    od = payload.get("open_data")
    if od:
        lines.append(
            f"Open Data snapshot: status {od.get('snapshot_status')}, "
            f"{od.get('snapshot_content_length')} bytes, last modified "
            f"{od.get('snapshot_last_modified')}"
        )
    return "\n".join(lines)
