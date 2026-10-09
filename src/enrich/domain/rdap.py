"""Domain registration dates from public RDAP.

RDAP is the structured successor to WHOIS: free, unauthenticated JSON. The
IANA bootstrap file (https://data.iana.org/rdap/dns.json) says which server
answers for each TLD; it is cached on disk and refreshed every few days, and
known servers in ``config/domain_layer.json`` stand in when it cannot be had.

Every lookup fails soft: one retry at most, a short timeout, a minimum interval
per server, and a 429 makes us stop asking that server for a while. The result
always comes back as an ``RdapResult``; a failure is an ``error`` on it.

A registration date is the one domain fact that is point-in-time safe, with one
caveat: a domain that lapsed and was registered again shows the *latest*
registration, so it can only make a domain look younger, never older.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from src.enrich.domain.common import HostPacer, domain_config
from src.logging_setup import get_logger

log = get_logger(__name__)

RDAP_ACCEPT = "application/rdap+json, application/json;q=0.9"


@dataclass
class RdapResult:
    domain: str
    fetched: bool = False
    created: date | None = None
    expires: date | None = None
    last_changed: date | None = None
    registrar: str | None = None
    status_code: int | None = None
    server: str | None = None
    error: str | None = None
    events: dict[str, str] = field(default_factory=dict)


def parse_event_date(value: Any) -> date | None:
    """The calendar date of an RDAP ``eventDate`` (RFC 3339), or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _vcard_fn(entity: dict[str, Any]) -> str | None:
    vcard = entity.get("vcardArray")
    if isinstance(vcard, list) and len(vcard) >= 2 and isinstance(vcard[1], list):
        for item in vcard[1]:
            if isinstance(item, list) and len(item) >= 4 and item[0] == "fn":
                text = str(item[3]).strip()
                if text:
                    return text
    return None


def _registrar(payload: dict[str, Any]) -> str | None:
    for entity in payload.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        roles = [str(r).lower() for r in entity.get("roles") or []]
        if "registrar" not in roles:
            continue
        name = _vcard_fn(entity)
        if name:
            return name[:200]
        for public_id in entity.get("publicIds") or []:
            if isinstance(public_id, dict) and public_id.get("identifier"):
                return f"IANA {public_id['identifier']}"
        if entity.get("handle"):
            return str(entity["handle"])[:200]
    return None


def parse_rdap_domain(domain: str, payload: dict[str, Any]) -> RdapResult:
    """Read the dates and registrar from an RDAP domain response."""
    result = RdapResult(domain=domain, fetched=True)
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        action = str(event.get("eventAction") or "").strip().lower()
        raw = event.get("eventDate")
        when = parse_event_date(raw)
        if when is None:
            continue
        result.events[action] = str(raw)
        if action == "registration":
            result.created = when
        elif action == "expiration":
            result.expires = when
        elif action == "last changed":
            result.last_changed = when
    result.registrar = _registrar(payload)
    return result


def parse_bootstrap(payload: dict[str, Any]) -> dict[str, str]:
    """TLD -> RDAP base URL (https preferred) from the IANA bootstrap file."""
    out: dict[str, str] = {}
    for service in payload.get("services") or []:
        if not (isinstance(service, list) and len(service) >= 2):
            continue
        tlds, urls = service[0], service[1]
        if not urls:
            continue
        base = next((u for u in urls if str(u).startswith("https://")), urls[0])
        base = str(base) if str(base).endswith("/") else f"{base}/"
        for tld in tlds:
            out[str(tld).lower()] = base
    return out


class RdapClient:
    """Looks up one domain at a time against the right RDAP server."""

    def __init__(
        self,
        user_agent: str,
        cache_dir: str | Path,
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = config or domain_config()["rdap"]
        self.user_agent = user_agent
        self.cache_path = Path(cache_dir) / "rdap" / "dns.json"
        self.transport = transport
        self.wall_clock = wall_clock
        self.pacer = HostPacer(
            float(self.cfg.get("min_interval_seconds_per_host", 1.0)), clock=clock, sleep=sleep
        )
        self._bases: dict[str, str] | None = None

    # -- bootstrap ----------------------------------------------------------
    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(float(self.cfg.get("timeout_seconds", 8))),
            headers={"User-Agent": self.user_agent, "Accept": RDAP_ACCEPT},
            follow_redirects=True,
            max_redirects=3,
            transport=self.transport,
        )

    def _read_cached_bootstrap(self, allow_stale: bool) -> dict[str, str] | None:
        try:
            if not self.cache_path.exists():
                return None
            age_days = (self.wall_clock() - self.cache_path.stat().st_mtime) / 86400
            if not allow_stale and age_days > float(self.cfg.get("bootstrap_refresh_days", 7)):
                return None
            bases = parse_bootstrap(json.loads(self.cache_path.read_text(encoding="utf-8")))
            return bases or None
        except (OSError, ValueError) as exc:
            log.warning("rdap.bootstrap_cache_unreadable", error=str(exc)[:200])
            return None

    def _fetch_bootstrap(self) -> dict[str, str] | None:
        url = str(self.cfg.get("bootstrap_url", "https://data.iana.org/rdap/dns.json"))
        try:
            with self._client() as client:
                resp = client.get(url)
            if resp.status_code != 200:
                log.warning("rdap.bootstrap_http", status=resp.status_code)
                return None
            payload = resp.json()
            bases = parse_bootstrap(payload)
            if not bases:
                return None
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return bases
        except (httpx.HTTPError, ValueError, OSError) as exc:
            log.warning("rdap.bootstrap_failed", error=str(exc)[:200])
            return None

    def bases(self) -> dict[str, str]:
        """TLD -> base URL: fresh cache, else download, else stale cache; plus fallbacks."""
        if self._bases is None:
            bases = (
                self._read_cached_bootstrap(allow_stale=False)
                or self._fetch_bootstrap()
                or self._read_cached_bootstrap(allow_stale=True)
                or {}
            )
            merged = {
                str(k).lower(): (str(v) if str(v).endswith("/") else f"{v}/")
                for k, v in (self.cfg.get("fallback_bases") or {}).items()
                if not str(k).startswith("_")
            }
            merged.update(bases)
            self._bases = merged
        return self._bases

    def base_for(self, domain: str) -> str | None:
        tld = domain.rsplit(".", 1)[-1].lower()
        return self.bases().get(tld)

    # -- lookup -------------------------------------------------------------
    def lookup(self, domain: str) -> RdapResult:
        domain = domain.lower().strip(".")
        base = self.base_for(domain)
        if not base:
            return RdapResult(domain=domain, error="rdap_no_server_for_tld")
        server = urlparse(base).hostname or base
        url = f"{base}domain/{domain}"
        attempts = 1 + max(int(self.cfg.get("max_retries", 1)), 0)
        last_error = "rdap_error"
        for _ in range(attempts):
            if self.pacer.is_blocked(server):
                return RdapResult(domain=domain, server=server, error="rdap_rate_limited")
            self.pacer.wait(server)
            try:
                with self._client() as client:
                    resp = client.get(url)
            except httpx.TimeoutException:
                last_error = "rdap_timeout"
                continue
            except httpx.HTTPError as exc:
                last_error = f"rdap_transport_error: {type(exc).__name__}"
                continue
            if resp.status_code == 429:
                self.pacer.block(server, float(self.cfg.get("backoff_on_429_seconds", 30)))
                log.warning("rdap.rate_limited", server=server)
                return RdapResult(
                    domain=domain, server=server, status_code=429, error="rdap_rate_limited"
                )
            if resp.status_code == 404:
                # The registry has no such domain: it is not registered.
                return RdapResult(
                    domain=domain,
                    server=server,
                    fetched=True,
                    status_code=404,
                    error="rdap_not_found",
                )
            if resp.status_code >= 500:
                last_error = f"rdap_http_{resp.status_code}"
                continue
            if resp.status_code != 200:
                return RdapResult(
                    domain=domain,
                    server=server,
                    status_code=resp.status_code,
                    error=f"rdap_http_{resp.status_code}",
                )
            try:
                payload = resp.json()
            except ValueError:
                return RdapResult(
                    domain=domain, server=server, status_code=200, error="rdap_bad_json"
                )
            if not isinstance(payload, dict):
                return RdapResult(
                    domain=domain, server=server, status_code=200, error="rdap_bad_json"
                )
            result = parse_rdap_domain(domain, payload)
            result.server = server
            result.status_code = 200
            return result
        return RdapResult(domain=domain, server=server, error=last_error)
