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

Server-side request forgery guard (D-701): redirects are followed by hand, and
*every* hop -- robots.txt, the page, each redirect target -- is checked before
it is requested: http(s) only, the default port only (unless
``allow_non_default_ports``), and the host must resolve only to public
addresses (no private, loopback, link-local, multicast, reserved, unspecified
or shared addresses, IPv4-mapped forms included). A refused hop stops the fetch
with the error ``blocked_private_address`` (or ``blocked_scheme`` /
``blocked_port``). Residual risk: httpx resolves the host again when it
connects, so a DNS server that answers differently within milliseconds (DNS
rebinding) is not fully excluded.
"""

from __future__ import annotations

import ipaddress
import socket
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


class _Blocked(Exception):
    """A hop that must not be requested; ``args[0]`` is the error code."""


class _TooManyRedirects(Exception):
    pass


class _NextScheme(Exception):
    """This scheme could not be reached; try the next one."""


Resolver = Callable[[str], list[str]]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def system_resolver(host: str) -> list[str]:
    """Every address ``host`` resolves to (A and AAAA), as strings."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})


def is_blocked_address(address: str) -> bool:
    """True for any address a public web server cannot have.

    Private, loopback, link-local (169.254.0.0/16, the cloud metadata range),
    multicast, reserved, unspecified and carrier-grade shared space, and the
    same in IPv4-mapped / 6to4 / Teredo IPv6 form. Unparseable means blocked.
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        for embedded in (ip.ipv4_mapped, ip.sixtofour, ip.teredo and ip.teredo[1]):
            if embedded:
                candidates.append(embedded)
    for c in candidates:
        if (
            c.is_private
            or c.is_loopback
            or c.is_link_local
            or c.is_multicast
            or c.is_reserved
            or c.is_unspecified
            or not c.is_global
        ):
            return True
    return False


class HomepageFetcher:
    def __init__(
        self,
        user_agent: str,
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        resolver: Resolver | None = None,
    ) -> None:
        full = config or domain_config()
        self.cfg = full["homepage"]
        self.selection = full.get("domain_selection", {})
        self.holding = full.get("holding_page", {})
        self.user_agent = user_agent
        self.transport = transport
        self.resolver: Resolver = resolver or system_resolver
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
            # Redirects are followed by hand so every hop is checked (D-701).
            follow_redirects=False,
            transport=self.transport,
        )

    def _check_hop(self, url: httpx.URL) -> None:
        """Raise ``_Blocked`` unless ``url`` is a public http(s) address."""
        scheme = url.scheme.lower()
        if scheme not in _DEFAULT_PORTS:
            raise _Blocked("blocked_scheme")
        if (
            url.port is not None
            and url.port != _DEFAULT_PORTS[scheme]
            and not self.cfg.get("allow_non_default_ports", False)
        ):
            raise _Blocked("blocked_port")
        host = (url.host or "").strip("[]").rstrip(".")
        if not host:
            raise _Blocked("blocked_private_address")
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
            addresses = [host]
        except ValueError:
            try:
                addresses = list(self.resolver(host))
            except (OSError, UnicodeError, ValueError) as exc:
                raise httpx.ConnectError(f"dns: {type(exc).__name__}") from exc
        if not addresses or any(is_blocked_address(a) for a in addresses):
            raise _Blocked("blocked_private_address")

    def _read_capped(
        self, client: httpx.Client, url: str, max_bytes: int
    ) -> tuple[Any, bytes, bool]:
        """Stream ``url``; stop at ``max_bytes`` or when the timeout budget is spent.

        Redirects are followed here, at most ``max_redirects`` of them, and each
        target is checked with ``_check_hop`` before it is requested.
        """
        deadline = self.clock() + float(self.cfg.get("timeout_seconds", 8)) * 2
        max_redirects = int(self.cfg.get("max_redirects", 5))
        target = httpx.URL(url)
        for _hop in range(max_redirects + 1):
            self._check_hop(target)
            with client.stream("GET", target) as resp:
                location = resp.headers.get("location")
                if resp.status_code in _REDIRECT_STATUSES and location:
                    target = resp.url.join(location)
                    continue
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
        raise _TooManyRedirects(url)

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
        except _TooManyRedirects:
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
                try:
                    return self._fetch_scheme(client, scheme, base, max_bytes, result)
                except _Blocked as exc:
                    result.errors.append(str(exc.args[0]))
                    return result
                except _NextScheme:
                    continue
        return result

    def _fetch_scheme(
        self,
        client: httpx.Client,
        scheme: str,
        base: str,
        max_bytes: int,
        result: HomepageResult,
    ) -> HomepageResult:
        """robots.txt then the page over one scheme; ``_NextScheme`` to fall back."""
        domain = result.domain
        if self.cfg.get("check_robots_txt", True):
            allowed = self._robots_allows(client, base, result)
            if allowed is None:
                result.errors.append(f"unreachable_{scheme}")
                raise _NextScheme
            if not allowed:
                result.robots_disallowed = True
                return result
        try:
            self.pacer.wait(domain)
            resp, body, truncated = self._read_capped(client, f"{base}/", max_bytes)
        except (httpx.TimeoutException, _BodyTooSlow):
            result.errors.append(f"homepage_timeout_{scheme}")
            raise _NextScheme from None
        except _TooManyRedirects:
            result.errors.append("homepage_too_many_redirects")
            return result
        except httpx.HTTPError as exc:
            result.errors.append(f"homepage_error_{scheme}: {type(exc).__name__}")
            raise _NextScheme from None
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
