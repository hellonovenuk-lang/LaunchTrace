"""Enrichment layer 3: domain evidence for a brand's own website.

``DomainProber.probe(domain)`` returns ``DomainSignals`` for one domain:

* ``LiveDomainProber`` -- public RDAP, DNS and one homepage fetch;
* ``NullDomainProber`` -- the layer is off; nothing is probed;
* ``FixtureDomainProber`` -- canned signals from a dict or JSON file (tests,
  the smoke test and the stability harness).

``DomainLayer`` decides *which* domain to probe for a lead, and enforces the
per-run cap. It only ever probes a domain we have reason to believe is the
brand's: the entity-verified website (and, only if config says so, the
unverified candidate). It never guesses a domain from a brand name, and never
probes a marketplace or social host. Whatever goes wrong, the layer returns
signals with an error; it never raises into the pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from src.enrich.domain.common import (
    domain_config,
    host_matches,
    host_of,
    is_public_hostname,
    registrable_domain,
)
from src.enrich.domain.dns_check import DnsChecker
from src.enrich.domain.homepage import HomepageFetcher
from src.enrich.domain.platforms import assess_page, resolve_stage
from src.enrich.domain.rdap import RdapClient
from src.logging_setup import get_logger
from src.models import DomainSignals, WebEnrichment
from src.settings import Settings

__all__ = [
    "DomainLayer",
    "DomainProber",
    "DomainSignals",
    "FixtureDomainProber",
    "LiveDomainProber",
    "NullDomainProber",
    "get_domain_prober",
    "select_domain",
]

log = get_logger(__name__)


@runtime_checkable
class DomainProber(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    def probe(self, domain: str) -> DomainSignals: ...


class NullDomainProber:
    """The domain layer is off: no lead gets domain signals."""

    name = "none"

    @property
    def enabled(self) -> bool:
        return False

    def probe(self, domain: str) -> DomainSignals:
        return DomainSignals(domain=domain, checked=False, prober=self.name)


class FixtureDomainProber:
    """Canned signals keyed by domain; ``default`` (if given) answers any other domain."""

    name = "fixture"

    def __init__(
        self,
        fixtures: dict[str, dict[str, Any] | DomainSignals] | None = None,
        default: dict[str, Any] | DomainSignals | None = None,
    ) -> None:
        self.fixtures = {k.lower(): v for k, v in (fixtures or {}).items()}
        self.default = default
        self.calls: list[str] = []

    @classmethod
    def from_json(cls, path: str | Path) -> FixtureDomainProber:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(fixtures=raw.get("domains", {}), default=raw.get("default"))

    @property
    def enabled(self) -> bool:
        return True

    def probe(self, domain: str) -> DomainSignals:
        self.calls.append(domain)
        spec = self.fixtures.get(domain.lower(), self.default)
        if spec is None:
            return DomainSignals(
                domain=domain,
                checked=True,
                prober=self.name,
                errors=["fixture_missing"],
                checked_at=datetime.now(UTC),
            )
        data = spec.model_dump() if isinstance(spec, DomainSignals) else dict(spec)
        data.update(domain=domain, checked=True, prober=self.name)
        data.setdefault("checked_at", datetime.now(UTC))
        return DomainSignals.model_validate(data)


class LiveDomainProber:
    """RDAP + DNS + homepage for one domain; each check can be switched off in config."""

    name = "live"

    def __init__(
        self,
        settings: Settings,
        config: dict[str, Any] | None = None,
        rdap: RdapClient | None = None,
        dns_checker: DnsChecker | None = None,
        homepage: HomepageFetcher | None = None,
    ) -> None:
        self.cfg = config or domain_config()
        ua = settings.resolved_domain_user_agent
        checks = self.cfg.get("enabled_checks", {})
        self.rdap = (
            rdap or RdapClient(ua, settings.cache_dir, self.cfg["rdap"])
            if checks.get("rdap", True)
            else None
        )
        self.dns = (
            dns_checker or DnsChecker(config=self.cfg["dns"]) if checks.get("dns", True) else None
        )
        self.homepage = (
            homepage or HomepageFetcher(ua, self.cfg) if checks.get("homepage", True) else None
        )

    @property
    def enabled(self) -> bool:
        return True

    def probe(self, domain: str) -> DomainSignals:
        signals = DomainSignals(
            domain=domain, checked=True, prober=self.name, checked_at=datetime.now(UTC)
        )
        if self.rdap is not None:
            try:
                r = self.rdap.lookup(domain)
                signals.rdap_fetched = r.fetched and r.error is None
                signals.rdap_created = r.created
                signals.rdap_expires = r.expires
                signals.rdap_last_changed = r.last_changed
                signals.rdap_registrar = r.registrar
                if r.error:
                    signals.errors.append(r.error)
            except Exception as exc:
                signals.errors.append(f"rdap_failed: {type(exc).__name__}")
        if self.dns is not None:
            try:
                d = self.dns.check(domain)
                signals.has_dns, signals.has_a = d.has_dns, d.has_a
                signals.has_mx, signals.has_ns = d.has_mx, d.has_ns
                signals.errors.extend(d.errors)
            except Exception as exc:
                signals.errors.append(f"dns_failed: {type(exc).__name__}")
        # No point asking a web server that DNS says does not exist.
        if self.homepage is not None and signals.has_dns is not False:
            try:
                h = self.homepage.fetch(domain)
                signals.homepage_fetched = h.fetched
                signals.homepage_status = h.status
                signals.final_url = h.final_url
                signals.redirected_off_domain = h.redirected_off_domain
                signals.redirect_target_kind = h.redirect_target_kind
                signals.robots_disallowed = h.robots_disallowed
                signals.errors.extend(h.errors)
                if h.fetched:
                    page = assess_page(
                        h.html, h.headers, h.final_url, h.redirect_target_kind, self.cfg
                    )
                    if h.redirect_target_kind != "marketplace":
                        signals.platform = page.platform.key if page.platform else None
                        signals.shop_platform = (
                            page.platform.is_shop_platform if page.platform else False
                        )
                        signals.store_detected = page.store_detected
                    signals.is_parked = page.is_parked
                    signals.is_holding_page = page.is_holding_page
            except Exception as exc:
                signals.errors.append(f"homepage_failed: {type(exc).__name__}")
        signals.web_presence_stage = stage_for(signals, self.cfg)
        return signals


def stage_for(signals: DomainSignals, config: dict[str, Any] | None = None) -> str:
    facts = {
        "has_domain": bool(signals.domain),
        "has_dns": signals.has_dns,
        "homepage_fetched": signals.homepage_fetched if signals.has_dns is not False else None,
        "redirected_to_marketplace": signals.redirect_target_kind == "marketplace",
        "is_parked": signals.is_parked,
        "is_holding_page": signals.is_holding_page,
        "store_detected": signals.store_detected,
        "shop_platform": signals.shop_platform,
    }
    return resolve_stage(facts, config)


def select_domain(
    web: WebEnrichment, config: dict[str, Any] | None = None
) -> tuple[str | None, str | None, str | None]:
    """(registrable domain, which field it came from, why nothing was chosen).

    Only the entity-verified website (and, if enabled, the unverified
    candidate) is used. Never a guess.
    """
    cfg = (config or domain_config())["domain_selection"]
    sources: list[tuple[str, str | None]] = []
    if cfg.get("use_verified_website", True):
        sources.append(("verified_website", web.website))
    if cfg.get("use_candidate_website", False):
        sources.append(("candidate_website", web.candidate_website))
    reason = "no_verified_website"
    for label, url in sources:
        host = host_of(url)
        if not host:
            continue
        if host_matches(host, cfg.get("skip_hosts", [])):
            reason = f"skipped_host:{host}"
            continue
        if not is_public_hostname(host):
            reason = f"not_a_public_hostname:{host}"
            continue
        domain = registrable_domain(host, cfg.get("multi_label_suffixes", []))
        if domain:
            return domain, label, None
    return None, None, reason


class DomainLayer:
    """Runs a prober over a run's leads: selection, per-run cap, de-duplication."""

    def __init__(self, prober: DomainProber, config: dict[str, Any] | None = None) -> None:
        self.prober = prober
        self.cfg = config or domain_config()
        self.max_domains = int(self.cfg["domain_selection"].get("max_domains_per_run", 60))
        self._cache: dict[str, DomainSignals] = {}
        self.probed = 0
        self.cap_skipped = 0

    @property
    def enabled(self) -> bool:
        try:
            return bool(self.prober.enabled)
        except Exception:
            return False

    def signals_for(self, web: WebEnrichment) -> DomainSignals | None:
        """Signals for one lead, or None when the layer has nothing to say.

        Never raises: a failing prober produces signals carrying the error.
        """
        if not self.enabled or not web.attempted:
            # Without a search we do not know the brand's website, so "no
            # domain" would be a claim we have not checked.
            return None
        domain, source, reason = select_domain(web, self.cfg)
        if domain is None:
            return DomainSignals(
                domain=None,
                checked=False,
                prober=self.prober.name,
                web_presence_stage="no_domain",
                errors=[reason] if reason and reason != "no_verified_website" else [],
            )
        if domain in self._cache:
            return self._cache[domain].model_copy(update={"domain_source": source}, deep=True)
        if self.probed >= self.max_domains:
            self.cap_skipped += 1
            return DomainSignals(
                domain=domain,
                domain_source=source,
                checked=False,
                prober=self.prober.name,
                errors=["domain_cap_reached"],
            )
        self.probed += 1
        try:
            signals = self.prober.probe(domain)
        except Exception as exc:
            log.warning("domain.probe_failed", domain=domain, error=str(exc)[:200])
            signals = DomainSignals(
                domain=domain,
                checked=True,
                prober=self.prober.name,
                errors=[f"probe_failed: {type(exc).__name__}: {str(exc)[:200]}"],
                checked_at=datetime.now(UTC),
            )
        signals = signals.model_copy(update={"domain_source": source})
        self._cache[domain] = signals
        return signals.model_copy(deep=True)


def days_registered_before(signals: DomainSignals | None, filing: date | None) -> int | None:
    """Days from domain registration to the trade mark filing (negative: after filing)."""
    if signals is None or signals.rdap_created is None or filing is None:
        return None
    return (filing - signals.rdap_created).days


def get_domain_prober(settings: Settings) -> DomainProber:
    if not settings.domain_layer_enabled:
        return NullDomainProber()
    return LiveDomainProber(settings)
