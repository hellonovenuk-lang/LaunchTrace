"""One polite fetch of a brand's homepage.

Order of play for a domain:

1. ``robots.txt`` at https (http if https cannot be reached at all), with the
   same size and time limits as the page. If it disallows ``/`` for our user
   agent, stop: nothing is fetched and ``robots_disallowed`` is recorded.
2. ``GET /`` with an honest user agent, a short timeout and a bounded number of
   redirects. The body is streamed and reading stops at ``max_bytes``.
3. Record the final URL and status, and where a redirect led: the same
   domain, another domain, a parking service or a marketplace.

Never more than one page per domain; no crawling.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.robotparser import RobotFileParser

import httpx

from src.enrich.domain.common import HostPacer, domain_config, host_matches, host_of
from src.enrich.domain.common import registrable_domain as _registrable

ROBOTS_AGENT_TOKEN = "LaunchTrace"


@dataclass
class HomepageResult:
    domain: str
    fetched: bool = False
    status: int | None = None
    final_url: str | None = None
    html: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    robots_disallowed: bool = False
    redirected_off_domain: bool | None = None
    redirect_target_kind: str | None = None
    errors: list[str] = field(default_factory=list)


class _BodyTooSlow(Exception):
    pass


class HomepageFetcher:
    def __init__(
        self,
        user_agent: str,
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        full = config or domain_config()
        self.cfg = full["homepage"]
        self.selection = full.get("domain_selection", {})
        self.holding = full.get("holding_page", {})
        self.user_agent = user_agent
        self.transport = transport
        self.clock = clock
        self.pacer = HostPacer(
            float(self.cfg.get("min_interval_seconds_per_host", 1.0)), clock=clock, sleep=sleep
        )

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(float(self.cfg.get("timeout_seconds", 8))),
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            },
            follow_redirects=True,
            max_redirects=int(self.cfg.get("max_redirects", 5)),
            transport=self.transport,
        )

    def _read_capped(
        self, client: httpx.Client, url: str, max_bytes: int
    ) -> tuple[Any, bytes, bool]:
        """Stream ``url``; stop at ``max_bytes`` or when the timeout budget is spent."""
        deadline = self.clock() + float(self.cfg.get("timeout_seconds", 8)) * 2
        with client.stream("GET", url) as resp:
            body = bytearray()
            truncated = False
            for chunk in resp.iter_bytes():
                body.extend(chunk)
                if len(body) >= max_bytes:
                    del body[max_bytes:]
                    truncated = True
                    break
                if self.clock() > deadline:
                    raise _BodyTooSlow(url)
            return resp, bytes(body), truncated

    @staticmethod
    def _decode(resp: Any, body: bytes) -> str:
        encoding = getattr(resp, "charset_encoding", None) or "utf-8"
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    def _robots_allows(
        self, client: httpx.Client, base: str, result: HomepageResult
    ) -> bool | None:
        """True/False from robots.txt; None when the host could not be reached at all."""
        url = f"{base}/robots.txt"
        try:
            self.pacer.wait(result.domain)
            resp, body, _ = self._read_capped(
                client, url, int(self.cfg.get("robots_max_bytes", 65536))
            )
        except (httpx.TimeoutException, _BodyTooSlow):
            return None
        except httpx.TooManyRedirects:
            result.errors.append("robots_too_many_redirects")
            return False
        except httpx.HTTPError:
            return None
        status = resp.status_code
        if status in (401, 403):
            # robotparser convention: an access-controlled robots.txt means keep out.
            return False
        if status >= 500:
            result.errors.append(f"robots_http_{status}")
            return False
        if status >= 400:
            return True
        parser = RobotFileParser()
        parser.parse(self._decode(resp, body).splitlines())
        return parser.can_fetch(ROBOTS_AGENT_TOKEN, f"{base}/") and parser.can_fetch(
            self.user_agent, f"{base}/"
        )

    def _classify_redirect(self, domain: str, final_url: str | None) -> tuple[bool | None, str]:
        final_host = host_of(final_url)
        if not final_host:
            return None, "unknown"
        suffixes = self.selection.get("multi_label_suffixes", [])
        if _registrable(final_host, suffixes) == _registrable(domain, suffixes):
            return False, "same_domain"
        if host_matches(final_host, self.holding.get("parking_hosts", [])):
            return True, "parking"
        if host_matches(final_host, self.selection.get("skip_hosts", [])):
            return True, "marketplace"
        return True, "other_domain"

    def fetch(self, domain: str) -> HomepageResult:
        domain = domain.lower().strip(".")
        result = HomepageResult(domain=domain)
        schemes = ["https", "http"] if self.cfg.get("try_http_fallback", True) else ["https"]
        max_bytes = int(self.cfg.get("max_bytes", 524288))
        try:
            client = self._client()
        except Exception as exc:  # pragma: no cover - client construction is local
            result.errors.append(f"homepage_client_error: {type(exc).__name__}")
            return result
        with client:
            for scheme in schemes:
                base = f"{scheme}://{domain}"
                if self.cfg.get("check_robots_txt", True):
                    allowed = self._robots_allows(client, base, result)
                    if allowed is None:
                        result.errors.append(f"unreachable_{scheme}")
                        continue
                    if not allowed:
                        result.robots_disallowed = True
                        return result
                try:
                    self.pacer.wait(domain)
                    resp, body, truncated = self._read_capped(client, f"{base}/", max_bytes)
                except (httpx.TimeoutException, _BodyTooSlow):
                    result.errors.append(f"homepage_timeout_{scheme}")
                    continue
                except httpx.TooManyRedirects:
                    result.errors.append("homepage_too_many_redirects")
                    return result
                except httpx.HTTPError as exc:
                    result.errors.append(f"homepage_error_{scheme}: {type(exc).__name__}")
                    continue
                result.status = resp.status_code
                result.final_url = str(resp.url)
                result.headers = {k.lower(): v for k, v in resp.headers.items()}
                result.html = self._decode(resp, body)
                result.truncated = truncated
                result.fetched = 200 <= resp.status_code < 300
                if not result.fetched:
                    result.errors.append(f"homepage_http_{resp.status_code}")
                off, kind = self._classify_redirect(domain, result.final_url)
                result.redirected_off_domain = off
                result.redirect_target_kind = kind
                return result
        return result
