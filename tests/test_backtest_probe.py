"""Historical availability probe: binary search, stop rules, politeness. No network."""

from __future__ import annotations

import json
import math

import httpx
import pytest

from src.backtest import probe as probe_mod
from src.backtest.probe import (
    AVAILABLE,
    BLOCKED,
    MISSING,
    JournalAvailabilityProber,
    format_probe,
    journal_numbers_between,
    run_probe,
)

BASE = "https://ipo.test/t-tmj/tm-journals"


class FakeClock:
    """A clock that only moves when the prober sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _journal(request: httpx.Request) -> str:
    return request.url.path.rstrip("/").split("/")[-2]


def make_handler(available: set[str], *, blocked: bool = False, head_status: int = 403):
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        number = _journal(request)
        calls.append((request.method, number))
        if blocked:
            body = b"<html><body>Please complete the CAPTCHA to continue</body></html>"
            return httpx.Response(403, content=body, headers={"content-type": "text/html"})
        if number in available:
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-type": "application/xml"})
            return httpx.Response(
                206,
                content=b'<?xml version="1.0"?><journal>',
                headers={"content-type": "application/xml", "content-range": "bytes 0-1023/999"},
            )
        if request.method == "HEAD":
            return httpx.Response(head_status)
        return httpx.Response(
            404, content=b"<html>Not found</html>", headers={"content-type": "text/html"}
        )

    return handler, calls


def make_prober(handler, clock: FakeClock, **kwargs) -> JournalAvailabilityProber:  # type: ignore[no-untyped-def]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    defaults = {"min_delay_seconds": 2.5, "max_requests": 40}
    defaults.update(kwargs)
    return JournalAvailabilityProber(
        client,
        BASE,
        sleep=clock.sleep,
        clock=clock.time,
        **defaults,  # type: ignore[arg-type]
    )


def test_journal_numbers_between_is_chronological_and_inclusive():
    numbers = journal_numbers_between("2025-051", "2026-002")
    assert numbers == ["2025-051", "2025-052", "2026-001", "2026-002"]
    assert journal_numbers_between("2026-002", "2025-051") == numbers


class TestFindEarliest:
    def test_finds_the_earliest_served_journal_by_binary_search(self):
        numbers = journal_numbers_between("2020-001", "2026-040")
        available = set(numbers[numbers.index("2025-040") :])
        handler, calls = make_handler(available)
        clock = FakeClock()
        prober = make_prober(handler, clock)

        summary = prober.find_earliest(numbers)

        assert summary.outcome == "found"
        assert summary.earliest_available == "2025-040"
        assert summary.earliest_exact is True
        assert summary.latest_available == "2026-040"
        probed_journals = {n for _, n in calls}
        # A binary search, not a walk: about log2(N) journals, not hundreds.
        assert len(probed_journals) <= math.ceil(math.log2(len(numbers))) + 2

    def test_politeness_delay_between_every_request(self):
        numbers = journal_numbers_between("2024-001", "2026-040")
        available = set(numbers[-30:])
        handler, calls = make_handler(available)
        clock = FakeClock()
        prober = make_prober(handler, clock, min_delay_seconds=3.0)

        prober.find_earliest(numbers)

        assert prober.requests_made == len(calls)
        # Every request after the first waited out the minimum delay.
        assert len(clock.sleeps) == len(calls) - 1
        assert all(s == pytest.approx(3.0) for s in clock.sleeps)

    def test_a_403_is_only_trusted_as_missing_after_a_journal_was_served(self):
        numbers = journal_numbers_between("2026-030", "2026-040")
        handler, calls = make_handler(set(numbers[5:]))
        prober = make_prober(handler, FakeClock())

        summary = prober.find_earliest(numbers)

        assert summary.earliest_available == numbers[5]
        # The host served the newest journal first, so no 403 needed a ranged GET.
        assert [m for m, _ in calls].count("GET") == 0

    def test_ambiguous_head_falls_back_to_a_small_ranged_get(self):
        handler, calls = make_handler({"2026-040"}, head_status=405)
        prober = make_prober(handler, FakeClock())

        result = prober.probe("2026-039")
        assert result.outcome == MISSING
        assert result.head_status == 405
        assert result.method == "HEAD+GET-range"
        assert calls == [("HEAD", "2026-039"), ("GET", "2026-039")]

        def head_refused(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(405)
            assert request.headers["range"] == "bytes=0-1023"
            return httpx.Response(
                206,
                content=b'<?xml version="1.0"?>',
                headers={"content-type": "application/xml", "content-range": "bytes 0-1023/5000"},
            )

        ok = make_prober(head_refused, FakeClock()).probe("2026-040")
        assert ok.outcome == AVAILABLE
        assert ok.content_length == 5000

    def test_stops_after_consecutive_misses_when_nothing_is_served(self):
        numbers = journal_numbers_between("2020-001", "2026-040")
        handler, calls = make_handler(set())
        prober = make_prober(handler, FakeClock(), max_consecutive_misses=4)

        summary = prober.find_earliest(numbers)

        assert summary.outcome == "none_available"
        assert summary.earliest_available is None
        assert len({n for _, n in calls}) == 4
        assert any("4 consecutive misses" in n for n in summary.notes)

    def test_a_captcha_wall_stops_everything_and_is_reported_as_blocked(self):
        numbers = journal_numbers_between("2020-001", "2026-040")
        handler, calls = make_handler(set(numbers), blocked=True)
        prober = make_prober(handler, FakeClock(), max_consecutive_blocked=3)

        summary = prober.find_earliest(numbers)

        assert summary.outcome == BLOCKED
        assert summary.earliest_available is None
        assert all(r.outcome == BLOCKED for r in summary.results)
        assert len(summary.results) == 3

    def test_request_budget_is_a_hard_cap_and_gives_an_upper_bound(self):
        numbers = journal_numbers_between("2020-001", "2026-040")
        handler, calls = make_handler(set(numbers[100:]))
        prober = make_prober(handler, FakeClock(), max_requests=4)

        summary = prober.find_earliest(numbers)

        assert prober.requests_made <= 4
        assert len(calls) <= 4
        assert summary.earliest_exact is False
        assert summary.outcome == "budget_exhausted"
        assert summary.earliest_available is not None
        assert numbers.index(summary.earliest_available) >= 100

    def test_an_error_mid_search_stops_with_an_upper_bound(self):
        numbers = journal_numbers_between("2026-001", "2026-040")

        def handler(request: httpx.Request) -> httpx.Response:
            number = _journal(request)
            if number == numbers[-1]:
                return httpx.Response(200, headers={"content-type": "application/xml"})
            return httpx.Response(503)

        summary = make_prober(handler, FakeClock()).find_earliest(numbers)
        assert summary.latest_available == numbers[-1]
        assert summary.earliest_exact is False
        assert any("search stopped" in n for n in summary.notes)

    def test_transport_errors_are_recorded_not_raised(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        result = make_prober(handler, FakeClock()).probe("2026-040")
        assert result.outcome == "error"
        assert "ConnectTimeout" in (result.error or "")

    def test_oldest_in_range_served_notes_the_archive_may_go_further(self):
        numbers = journal_numbers_between("2026-030", "2026-040")
        handler, _ = make_handler(set(numbers))
        summary = make_prober(handler, FakeClock()).find_earliest(numbers)
        assert summary.earliest_available == "2026-030"
        assert any("may go back further" in n for n in summary.notes)


def test_run_probe_writes_availability_json_including_open_data(tmp_path, settings):
    numbers = journal_numbers_between("2026-020", "2026-040")
    available = set(numbers[8:])
    journal_handler, _ = make_handler(available)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "assets.publishing.service.gov.uk":
            return httpx.Response(
                200,
                headers={"content-length": "63238846", "last-modified": "Tue, 13 Feb 2018"},
            )
        if request.url.host == "www.gov.uk":
            return httpx.Response(
                200,
                text='<a href="https://x.test/opendatadomestic.zip">data</a> 13 February 2018',
            )
        return journal_handler(request)

    settings = settings.model_copy(update={"ukipo_journal_base_url": BASE})
    out = tmp_path / "availability.json"
    clock = FakeClock()
    payload = run_probe(
        first="2026-020",
        last="2026-040",
        out_path=out,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        settings=settings,
        sleep=clock.sleep,
    )
    written = json.loads(out.read_text())
    assert written["earliest_available"] == numbers[8]
    assert written["outcome"] == "found"
    assert written["open_data"]["snapshot_content_length"] == "63238846"
    assert written["open_data"]["landing_data_links"] == ["https://x.test/opendatadomestic.zip"]
    assert written["total_requests"] == written["journal_requests"] + 2
    assert payload["results"][0]["journal_number"] == "2026-040"
    text = format_probe(payload)
    assert "Earliest served: " + numbers[8] in text
    assert "Open Data snapshot: status 200" in text


def test_default_range_covers_the_configured_lookback():
    from datetime import date

    first, last = probe_mod.default_range({"default_lookback_years": 2}, date(2026, 10, 9))
    assert last == "2026-041"
    assert first.startswith("2024-")


def test_cli_probe_uses_the_runner(monkeypatch, capsys):
    from src.pipeline import main

    seen: dict = {}

    def fake_run_probe(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return {
            "range": {"from": "2026-001", "to": "2026-002", "weeks": 2},
            "journal_requests": 2,
            "outcome": "found",
            "latest_available": "2026-002",
            "earliest_available": "2026-001",
            "earliest_exact": True,
            "results": [],
            "notes": [],
            "open_data": None,
        }

    monkeypatch.setattr(probe_mod, "run_probe", fake_run_probe)
    assert main(["backtest", "probe", "--from", "2026-001", "--to", "2026-002"]) == 0
    assert seen["first"] == "2026-001" and seen["last"] == "2026-002"
    assert "Earliest served: 2026-001" in capsys.readouterr().out


def _fixed(head: httpx.Response, get: httpx.Response | None = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return head
        assert get is not None, "no ranged GET expected"
        return get

    return handler


HTML = {"content-type": "text/html"}


@pytest.mark.parametrize(
    ("head", "get", "outcome"),
    [
        (httpx.Response(404), None, MISSING),
        (httpx.Response(410), None, MISSING),
        (httpx.Response(429), None, BLOCKED),
        (httpx.Response(503), None, "error"),
        # 200 with an HTML page: look inside.
        (
            httpx.Response(200, headers=HTML),
            httpx.Response(
                200, content=b"<html>Request unsuccessful. Incapsula</html>", headers=HTML
            ),
            BLOCKED,
        ),
        (
            httpx.Response(200, headers=HTML),
            httpx.Response(200, content=b"<html><p>Journal list</p></html>", headers=HTML),
            MISSING,
        ),
        (
            httpx.Response(403),
            httpx.Response(206, content=b"PK\x03\x04zipdata", headers={"content-type": "x"}),
            AVAILABLE,
        ),
        (httpx.Response(403), httpx.Response(429), BLOCKED),
        (httpx.Response(405), httpx.Response(500, content=b"oops"), "error"),
    ],
)
def test_classification(head, get, outcome):  # type: ignore[no-untyped-def]
    result = make_prober(_fixed(head, get), FakeClock()).probe("2026-040")
    assert result.outcome == outcome


def test_no_budget_for_the_ranged_get_is_an_error_not_a_guess():
    prober = make_prober(_fixed(httpx.Response(403)), FakeClock(), max_requests=1)
    result = prober.probe("2026-040")
    assert result.outcome == "error"
    assert "no request budget" in (result.error or "")
    assert prober.probe("2026-039").outcome == "skipped"


def test_ranged_get_transport_error_is_recorded():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405)
        raise httpx.ReadTimeout("slow", request=request)

    result = make_prober(handler, FakeClock()).probe("2026-040")
    assert result.outcome == "error" and "ReadTimeout" in (result.error or "")


def test_budget_exhausted_while_walking_back():
    numbers = journal_numbers_between("2026-030", "2026-040")
    handler, _ = make_handler(set(), head_status=404)
    summary = make_prober(handler, FakeClock(), max_requests=2).find_earliest(numbers)
    assert summary.outcome == "budget_exhausted"
    assert summary.earliest_available is None


def test_find_earliest_needs_numbers():
    with pytest.raises(ValueError):
        make_prober(_fixed(httpx.Response(404)), FakeClock()).find_earliest([])


def test_open_data_check_failures_are_recorded():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    out = probe_mod.check_open_data(httpx.Client(transport=httpx.MockTransport(handler)))
    assert "ConnectError" in out["snapshot_error"]
    assert "ConnectError" in out["landing_error"]
    assert "2018-01-05" in out["local_weeks"]
