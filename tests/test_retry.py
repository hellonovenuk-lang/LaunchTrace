"""Retry, backoff and rate-limit handling in the HTTP layer."""

from __future__ import annotations

import httpx
import pytest

from src.errors import ProviderError, RateLimitedError
from src.ingest.http_client import HttpClient


class FlakyTransport(httpx.BaseTransport):
    """Fails a set number of times, then succeeds."""

    def __init__(self, failures: int, status: int = 503, body: bytes = b"ok") -> None:
        self.remaining = failures
        self.status = status
        self.body = body
        self.attempts = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        if self.remaining > 0:
            self.remaining -= 1
            return httpx.Response(self.status, request=request)
        return httpx.Response(200, content=self.body, request=request)


@pytest.fixture
def fast_client(monkeypatch):  # type: ignore[no-untyped-def]
    """A client whose backoff does not actually sleep."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)
    return HttpClient(user_agent="test", timeout=5, max_retries=4)


def _with_transport(client: HttpClient, transport: httpx.BaseTransport) -> None:
    client._client = lambda: httpx.Client(transport=transport, headers={})  # type: ignore[method-assign]


class TestRetry:
    def test_retries_a_server_error_then_succeeds(self, fast_client):
        transport = FlakyTransport(failures=2, status=503)
        _with_transport(fast_client, transport)
        assert fast_client.get_text("https://example.test/x") == "ok"
        assert transport.attempts == 3

    def test_retries_a_rate_limit(self, fast_client):
        transport = FlakyTransport(failures=1, status=429)
        _with_transport(fast_client, transport)
        assert fast_client.get_text("https://example.test/x") == "ok"
        assert transport.attempts == 2

    def test_gives_up_after_the_configured_attempts(self, fast_client):
        transport = FlakyTransport(failures=99, status=503)
        _with_transport(fast_client, transport)
        with pytest.raises((ProviderError, RateLimitedError)):
            fast_client.get_text("https://example.test/x")
        assert transport.attempts == 4

    def test_does_not_retry_a_client_error(self, fast_client):
        transport = FlakyTransport(failures=99, status=404)
        _with_transport(fast_client, transport)
        with pytest.raises(httpx.HTTPStatusError):
            fast_client.get_text("https://example.test/x")
        assert transport.attempts == 1

    def test_transport_errors_are_retried(self, fast_client):
        class Broken(httpx.BaseTransport):
            def __init__(self) -> None:
                self.attempts = 0

            def handle_request(self, request):  # type: ignore[no-untyped-def]
                self.attempts += 1
                raise httpx.ConnectError("refused", request=request)

        transport = Broken()
        _with_transport(fast_client, transport)
        with pytest.raises(httpx.TransportError):
            fast_client.get_text("https://example.test/x")
        assert transport.attempts == 4


class TestDownload:
    def test_streams_to_disk_and_retries(self, fast_client, tmp_path):
        transport = FlakyTransport(failures=1, status=503, body=b"<journal/>")
        _with_transport(fast_client, transport)
        dest = tmp_path / "journal.xml"
        fast_client.download("https://example.test/j.xml", dest)
        assert dest.read_bytes() == b"<journal/>"
        assert transport.attempts == 2

    def test_partial_file_is_not_left_behind_on_failure(self, fast_client, tmp_path):
        transport = FlakyTransport(failures=99, status=503)
        _with_transport(fast_client, transport)
        dest = tmp_path / "journal.xml"
        with pytest.raises((ProviderError, RateLimitedError)):
            fast_client.download("https://example.test/j.xml", dest)
        assert not dest.exists()
