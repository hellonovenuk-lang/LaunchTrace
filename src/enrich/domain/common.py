"""Small helpers shared by the domain-layer checks: config, host names, pacing."""

from __future__ import annotations

import ipaddress
import re
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse

from src.settings import load_config

_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$"
)


def domain_config() -> dict[str, Any]:
    return load_config("domain_layer.json")


def host_of(url_or_host: str | None) -> str | None:
    """Lower-case host name of a URL or bare host, without ``www.`` and port."""
    if not url_or_host:
        return None
    text = url_or_host.strip()
    if "://" not in text:
        text = "http://" + text
    host = (urlparse(text).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.") or None


def is_public_hostname(host: str | None) -> bool:
    """A syntactically valid public DNS name: no IP literals, no single labels."""
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if host in {"localhost"} or host.endswith((".local", ".localhost", ".internal", ".test")):
        return False
    return bool(_HOST_RE.match(host))


def registrable_domain(host: str | None, multi_label_suffixes: Iterable[str]) -> str | None:
    """Reduce a host to the domain someone registered (``shop.brand.co.uk`` -> ``brand.co.uk``)."""
    if not host:
        return None
    labels = host.lower().strip(".").split(".")
    if len(labels) < 2:
        return None
    suffixes = {s.lower() for s in multi_label_suffixes}
    if len(labels) >= 3 and ".".join(labels[-2:]) in suffixes:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_matches(host: str | None, patterns: Iterable[str]) -> bool:
    """True when ``host`` is one of ``patterns`` or a subdomain of one."""
    if not host:
        return False
    host = host.lower()
    for pattern in patterns:
        p = pattern.lower().strip(".")
        if host == p or host.endswith("." + p):
            return True
    return False


class HostPacer:
    """Keeps a minimum interval between two requests to the same host.

    ``clock`` and ``sleep`` are injectable so tests neither wait nor depend on
    wall time.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = max(float(min_interval_seconds), 0.0)
        self.clock = clock
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._blocked_until: dict[str, float] = {}

    def wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = self.min_interval - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last[host] = self.clock()

    def block(self, host: str, seconds: float) -> None:
        """Stop asking ``host`` for ``seconds`` (after a 429)."""
        self._blocked_until[host] = self.clock() + max(float(seconds), 0.0)

    def is_blocked(self, host: str) -> bool:
        until = self._blocked_until.get(host)
        return until is not None and self.clock() < until
