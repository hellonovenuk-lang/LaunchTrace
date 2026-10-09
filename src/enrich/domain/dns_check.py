"""A, MX and NS lookups for a domain, via dnspython.

What it answers: does the domain resolve at all (NXDOMAIN -> no), does it point
at a web server (A), can it receive email (MX), is it delegated (NS). Short
lifetime, no retries beyond the resolver's own, fail soft: a timeout leaves the
answer ``None`` (unknown), never ``False``.

The resolver is injectable; tests pass a fake and never send a packet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import dns.exception
import dns.resolver

from src.enrich.domain.common import domain_config


class Resolver(Protocol):
    def resolve(self, qname: str, rdtype: str, *, lifetime: float | None = ...) -> Any: ...


@dataclass
class DnsResult:
    domain: str
    has_dns: bool | None = None
    records: dict[str, bool | None] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def has_a(self) -> bool | None:
        return self.records.get("A")

    @property
    def has_mx(self) -> bool | None:
        return self.records.get("MX")

    @property
    def has_ns(self) -> bool | None:
        return self.records.get("NS")


class _DnspythonResolver:
    """Adapter so the default resolver matches the injectable interface."""

    def __init__(self) -> None:
        self._resolver = dns.resolver.Resolver()

    def resolve(self, qname: str, rdtype: str, *, lifetime: float | None = None) -> Any:
        return self._resolver.resolve(qname, rdtype, lifetime=lifetime, search=False)


class DnsChecker:
    def __init__(self, resolver: Resolver | None = None, config: dict[str, Any] | None = None):
        self.cfg = config or domain_config()["dns"]
        self._resolver = resolver

    @property
    def resolver(self) -> Resolver:
        if self._resolver is None:
            self._resolver = _DnspythonResolver()
        return self._resolver

    def check(self, domain: str) -> DnsResult:
        domain = domain.lower().strip(".")
        result = DnsResult(domain=domain)
        lifetime = float(self.cfg.get("lifetime_seconds", 3.0))
        qname = f"{domain}."
        for rdtype in self.cfg.get("record_types", ["A", "MX", "NS"]):
            try:
                answer = self.resolver.resolve(qname, rdtype, lifetime=lifetime)
                result.records[rdtype] = bool(answer) and len(answer) > 0
            except dns.resolver.NXDOMAIN:
                # The name does not exist: nothing else can exist under it.
                for t in self.cfg.get("record_types", ["A", "MX", "NS"]):
                    result.records[t] = False
                result.has_dns = False
                result.errors.append("dns_nxdomain")
                return result
            except dns.resolver.NoAnswer:
                result.records[rdtype] = False
            except dns.exception.Timeout:
                result.records[rdtype] = None
                result.errors.append(f"dns_timeout:{rdtype}")
            except dns.resolver.NoNameservers:
                result.records[rdtype] = None
                result.errors.append(f"dns_no_nameservers:{rdtype}")
            except Exception as exc:  # any resolver failure is "unknown", never a crash
                result.records[rdtype] = None
                result.errors.append(f"dns_error:{rdtype}:{type(exc).__name__}")
        values = list(result.records.values())
        if any(v is True for v in values):
            result.has_dns = True
        elif values and all(v is False for v in values):
            result.has_dns = False
        else:
            result.has_dns = None
        return result
