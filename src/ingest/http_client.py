"""Shared HTTP client with retries, backoff and streaming download."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.errors import ProviderError, RateLimitedError
from src.logging_setup import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpClient:
    """Thin wrapper around httpx with sane retry behaviour.

    Streams large downloads straight to disk so a 150 MB journal never has to
    sit in memory.
    """

    def __init__(
        self,
        user_agent: str,
        timeout: int = 60,
        max_retries: int = 4,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {"User-Agent": user_agent, **(headers or {})}

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(self.timeout),
            headers=self._headers,
            follow_redirects=True,
        )

    def get_text(self, url: str) -> str:
        return self._request_with_retry("GET", url).text

    def get_json(self, url: str, **kwargs) -> dict:
        return self._request_with_retry("GET", url, **kwargs).json()

    def head_ok(self, url: str) -> bool:
        try:
            with self._client() as client:
                resp = client.head(url)
                if resp.status_code == 405:  # some servers reject HEAD
                    resp = client.get(url, headers={"Range": "bytes=0-0"})
                return resp.status_code < 400
        except httpx.HTTPError:
            return False

    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        @retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=32),
            retry=retry_if_exception_type((RateLimitedError, httpx.TransportError, ProviderError)),
        )
        def _do() -> httpx.Response:
            with self._client() as client:
                resp = client.request(method, url, **kwargs)
            if resp.status_code == 429:
                raise RateLimitedError(f"429 from {url}")
            if resp.status_code in RETRYABLE_STATUS:
                raise ProviderError(f"{resp.status_code} from {url}")
            resp.raise_for_status()
            return resp

        return _do()

    def download(self, url: str, dest: Path, chunk_size: int = 1024 * 256) -> Path:
        """Stream a URL to ``dest``.  Retries the whole transfer on failure."""

        @retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=32),
            retry=retry_if_exception_type((RateLimitedError, httpx.TransportError, ProviderError)),
        )
        def _do() -> Path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            with self._client() as client, client.stream("GET", url) as resp:
                if resp.status_code == 429:
                    raise RateLimitedError(f"429 from {url}")
                if resp.status_code in RETRYABLE_STATUS:
                    raise ProviderError(f"{resp.status_code} from {url}")
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size):
                        fh.write(chunk)
            tmp.replace(dest)
            return dest

        log.info("http.download.start", url=url, dest=str(dest))
        path = _do()
        log.info("http.download.done", url=url, bytes=path.stat().st_size)
        return path

    def try_urls(self, urls: Iterable[str]) -> str | None:
        """Return the first URL that responds successfully, or None."""
        for url in urls:
            if self.head_ok(url):
                return url
        return None
