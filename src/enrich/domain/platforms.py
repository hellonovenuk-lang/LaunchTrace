"""What a fetched homepage says: platform, holding page, live store, and stage.

Pure functions over HTML text and response headers, driven by the rules in
``config/domain_layer.json``. No network here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.enrich.domain.common import domain_config, host_matches, host_of

WEB_PRESENCE_STAGES = (
    "no_domain",
    "no_dns",
    "parked",
    "holding_page",
    "site_no_store",
    "live_store",
    "unknown",
)

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class PlatformMatch:
    key: str
    is_shop_platform: bool
    evidence: str


@dataclass
class PageAssessment:
    platform: PlatformMatch | None = None
    store_detected: bool = False
    is_parked: bool = False
    is_holding_page: bool = False
    reasons: list[str] = field(default_factory=list)


def visible_text(html: str) -> str:
    """Rough visible text of a page: scripts, styles and tags removed."""
    text = _SCRIPT_STYLE.sub(" ", html or "")
    text = _TAG.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _header_hit(marker: str, headers: dict[str, str]) -> bool:
    name, sep, needle = marker.partition(":")
    name = name.strip().lower()
    if name not in headers:
        return False
    return not sep or needle.strip().lower() in str(headers[name]).lower()


def detect_platform(
    html: str, headers: dict[str, str] | None, config: dict[str, Any] | None = None
) -> PlatformMatch | None:
    """First platform whose fingerprint matches, in config order."""
    cfg = config or domain_config()
    lowered = (html or "").lower()
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    for rule in cfg["platforms"]["rules"]:
        for marker in rule.get("headers", []):
            if _header_hit(marker, hdrs):
                return PlatformMatch(rule["key"], bool(rule.get("is_shop_platform")), marker)
        for marker in rule.get("html", []):
            if marker.lower() in lowered:
                return PlatformMatch(rule["key"], bool(rule.get("is_shop_platform")), marker)
    return None


def detect_store(html: str, config: dict[str, Any] | None = None) -> bool:
    cfg = config or domain_config()
    lowered = (html or "").lower()
    return any(m.lower() in lowered for m in cfg["store_markers"]["html"])


def assess_page(
    html: str,
    headers: dict[str, str] | None,
    final_url: str | None,
    redirect_target_kind: str | None = None,
    config: dict[str, Any] | None = None,
) -> PageAssessment:
    cfg = config or domain_config()
    holding = cfg["holding_page"]
    out = PageAssessment()
    lowered = (html or "").lower()

    out.platform = detect_platform(html, headers, cfg)
    out.store_detected = detect_store(html, cfg)

    final_host = host_of(final_url)
    if redirect_target_kind == "parking" or host_matches(
        final_host, holding.get("parking_hosts", [])
    ):
        out.is_parked = True
        out.reasons.append(f"redirected to parking host {final_host}")
    for marker in holding.get("parking_html_markers", []):
        if marker.lower() in lowered:
            out.is_parked = True
            out.reasons.append(f"parking marker '{marker}'")
            break

    text = visible_text(html)
    if not out.store_detected:
        lowered_text = text.lower()
        for phrase in holding.get("phrases", []):
            if phrase.lower() in lowered_text or phrase.lower() in lowered:
                out.is_holding_page = True
                out.reasons.append(f"holding phrase '{phrase}'")
                break
        if len(text) < int(holding.get("min_visible_text_chars", 200)):
            out.is_holding_page = True
            out.reasons.append(f"only {len(text)} characters of visible text")
    if out.is_parked:
        out.is_holding_page = True
    return out


def resolve_stage(facts: dict[str, Any], config: dict[str, Any] | None = None) -> str:
    """First ordered rule whose conditions all equal the facts; ``unknown`` otherwise.

    A fact that is ``None`` (not established) matches no condition, so missing
    evidence never satisfies a rule by accident.
    """
    cfg = config or domain_config()
    for rule in cfg["web_presence_stage"]["rules"]:
        when = rule.get("when") or {}
        if all(facts.get(k) is not None and facts.get(k) == v for k, v in when.items()):
            stage = str(rule["stage"])
            return stage if stage in WEB_PRESENCE_STAGES else "unknown"
    return "unknown"
