"""Enrichment layer 3: RDAP, DNS, homepage, platforms, and the layer itself.

Everything here is network-free: RDAP and homepage requests go through
``httpx.MockTransport``, DNS through a fake resolver.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import dns.exception
import dns.resolver
import httpx
import pytest

from src.enrich.domain import (
    DomainLayer,
    DomainSignals,
    FixtureDomainProber,
    LiveDomainProber,
    NullDomainProber,
    get_domain_prober,
    select_domain,
)
from src.enrich.domain.common import (
    HostPacer,
    domain_config,
    host_of,
    is_public_hostname,
    registrable_domain,
)
from src.enrich.domain.dns_check import DnsChecker
from src.enrich.domain.homepage import HomepageFetcher
from src.enrich.domain.platforms import (
    WEB_PRESENCE_STAGES,
    assess_page,
    detect_platform,
    resolve_stage,
    visible_text,
)
from src.enrich.domain.rdap import (
    RdapClient,
    parse_bootstrap,
    parse_event_date,
    parse_rdap_domain,
)
from src.models import WebEnrichment
from src.settings import FIXTURES_DIR, Settings

DOMAIN_FIXTURES = FIXTURES_DIR / "domain"
HTML = DOMAIN_FIXTURES / "html"
UA = "LaunchTrace/0.1 (+https://launchtrace.test; domain check)"


def _json(name: str) -> dict[str, Any]:
    return json.loads((DOMAIN_FIXTURES / name).read_text(encoding="utf-8"))


def _html(name: str) -> str:
    return (HTML / name).read_text(encoding="utf-8")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_stage_rules_use_only_known_stages(self):
        cfg = domain_config()
        assert set(cfg["web_presence_stage"]["values"]) == set(WEB_PRESENCE_STAGES)
        for rule in cfg["web_presence_stage"]["rules"]:
            assert rule["stage"] in WEB_PRESENCE_STAGES

    def test_candidate_websites_are_not_probed_by_default(self):
        assert domain_config()["domain_selection"]["use_candidate_website"] is False

    def test_limits_are_bounded(self):
        cfg = domain_config()
        assert cfg["rdap"]["max_retries"] <= 1
        assert cfg["rdap"]["timeout_seconds"] <= 15
        assert cfg["homepage"]["max_bytes"] <= 1024 * 1024
        assert cfg["homepage"]["max_redirects"] <= 10
        assert 0 < cfg["domain_selection"]["max_domains_per_run"] <= 500

    def test_tests_run_with_the_layer_off(self):
        assert os.environ.get("DOMAIN_LAYER_ENABLED") == "false"
        assert isinstance(get_domain_prober(Settings()), NullDomainProber)  # type: ignore[call-arg]

    def test_production_default_is_live(self, settings: Settings):
        live = settings.model_copy(update={"domain_layer_enabled": True})
        assert isinstance(get_domain_prober(live), LiveDomainProber)

    def test_user_agent_defaults_to_site_url(self, settings: Settings):
        assert settings.resolved_domain_user_agent == UA
        custom = settings.model_copy(update={"domain_user_agent": "Custom/1.0"})
        assert custom.resolved_domain_user_agent == "Custom/1.0"


# ---------------------------------------------------------------------------
# common helpers
# ---------------------------------------------------------------------------


class TestCommon:
    SUFFIXES = domain_config()["domain_selection"]["multi_label_suffixes"]

    @pytest.mark.parametrize(
        "host,expected",
        [
            ("shop.brand.co.uk", "brand.co.uk"),
            ("brand.co.uk", "brand.co.uk"),
            ("www.brand.com", "brand.com"),
            ("a.b.brand.com", "brand.com"),
            ("brand.shop", "brand.shop"),
        ],
    )
    def test_registrable_domain(self, host, expected):
        assert registrable_domain(host, self.SUFFIXES) == expected

    def test_host_of(self):
        assert host_of("https://WWW.Brand.co.uk:443/shop?x=1") == "brand.co.uk"
        assert host_of("brand.com") == "brand.com"
        assert host_of(None) is None

    @pytest.mark.parametrize(
        "host,ok",
        [
            ("brand.co.uk", True),
            ("127.0.0.1", False),
            ("localhost", False),
            ("printer.local", False),
            ("nodot", False),
            ("bad_host.com", False),
        ],
    )
    def test_public_hostname(self, host, ok):
        assert is_public_hostname(host) is ok

    def test_pacer_spaces_requests_per_host(self):
        clock = FakeClock()
        pacer = HostPacer(1.0, clock=clock, sleep=clock.sleep)
        pacer.wait("a")
        pacer.wait("a")
        pacer.wait("b")
        assert clock.slept == [1.0]
        pacer.block("a", 30)
        assert pacer.is_blocked("a") and not pacer.is_blocked("b")
        clock.now += 31
        assert not pacer.is_blocked("a")


# ---------------------------------------------------------------------------
# RDAP
# ---------------------------------------------------------------------------


class TestRdapParsing:
    def test_nominet_response(self):
        r = parse_rdap_domain("examplebrand.co.uk", _json("rdap_nominet.json"))
        assert r.fetched
        assert r.created == date(2025, 6, 2)
        assert r.expires == date(2027, 6, 2)
        assert r.last_changed == date(2026, 1, 15)
        assert r.registrar == "Example Registrar Ltd t/a Example Hosting"

    def test_verisign_response(self):
        r = parse_rdap_domain("examplebrand.com", _json("rdap_verisign.json"))
        assert r.created == date(2024, 11, 20)
        assert r.expires == date(2026, 11, 20)
        assert r.last_changed == date(2025, 10, 22)
        assert r.registrar == "NameCheap, Inc."

    def test_missing_events_leave_dates_empty(self):
        r = parse_rdap_domain("x.com", {"objectClassName": "domain", "events": [{"x": 1}]})
        assert r.created is None and r.expires is None and r.registrar is None

    def test_registrar_from_public_id_when_no_vcard(self):
        payload = {"entities": [{"roles": ["registrar"], "publicIds": [{"identifier": "9"}]}]}
        assert parse_rdap_domain("x.com", payload).registrar == "IANA 9"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2025-06-02T10:14:22Z", date(2025, 6, 2)),
            ("2025-06-02T10:14:22.123+01:00", date(2025, 6, 2)),
            ("2025-06-02", date(2025, 6, 2)),
            ("not a date", None),
            (None, None),
        ],
    )
    def test_event_dates(self, raw, expected):
        assert parse_event_date(raw) == expected

    def test_bootstrap_prefers_https(self):
        bases = parse_bootstrap(_json("iana_dns_bootstrap.json"))
        assert bases["uk"] == "https://rdap.nominet.uk/uk/"
        assert bases["net"] == "https://rdap.verisign.com/com/v1/"
        assert bases["shop"] == "https://rdap.example-shop.invalid/rdap/"


class RdapServer:
    """A MockTransport handler serving the bootstrap and domain lookups."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.bootstrap: Any = _json("iana_dns_bootstrap.json")
        self.domain_responses: dict[str, Any] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url.endswith("/rdap/dns.json"):
            if isinstance(self.bootstrap, Exception):
                raise self.bootstrap
            return httpx.Response(200, json=self.bootstrap)
        name = url.rsplit("/", 1)[-1]
        resp = self.domain_responses.get(name)
        if resp is None:
            return httpx.Response(404, json={"errorCode": 404})
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, list):
            item = resp.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return resp


def _rdap(tmp_path: Path, server: RdapServer, clock: FakeClock | None = None) -> RdapClient:
    clock = clock or FakeClock()
    return RdapClient(
        UA,
        tmp_path,
        transport=httpx.MockTransport(server),
        clock=clock,
        sleep=clock.sleep,
        wall_clock=lambda: 2_000_000_000.0,
    )


class TestRdapClient:
    def test_lookup_through_bootstrap_and_cache(self, tmp_path: Path):
        server = RdapServer()
        server.domain_responses["examplebrand.co.uk"] = httpx.Response(
            200, json=_json("rdap_nominet.json")
        )
        result = _rdap(tmp_path, server).lookup("examplebrand.co.uk")
        assert result.created == date(2025, 6, 2)
        assert result.error is None and result.server == "rdap.nominet.uk"
        assert server.requests[0] == "https://data.iana.org/rdap/dns.json"
        assert server.requests[1] == "https://rdap.nominet.uk/uk/domain/examplebrand.co.uk"
        cached = tmp_path / "rdap" / "dns.json"
        assert cached.exists()

        # A fresh cache is used without asking IANA again.
        os.utime(cached, (2_000_000_000.0, 2_000_000_000.0))
        server2 = RdapServer()
        server2.bootstrap = AssertionError("bootstrap must come from the cache")
        server2.domain_responses["examplebrand.com"] = httpx.Response(
            200, json=_json("rdap_verisign.json")
        )
        result = _rdap(tmp_path, server2).lookup("examplebrand.com")
        assert result.created == date(2024, 11, 20)
        assert all("iana" not in u for u in server2.requests)

    def test_stale_cache_is_refreshed(self, tmp_path: Path):
        cached = tmp_path / "rdap" / "dns.json"
        cached.parent.mkdir(parents=True)
        cached.write_text(json.dumps({"services": [[["uk"], ["https://old.invalid/"]]]}))
        old = 2_000_000_000.0 - 30 * 86400
        os.utime(cached, (old, old))
        server = RdapServer()
        client = _rdap(tmp_path, server)
        assert client.base_for("brand.co.uk") == "https://rdap.nominet.uk/uk/"
        assert server.requests == ["https://data.iana.org/rdap/dns.json"]

    def test_unreachable_bootstrap_falls_back_to_known_servers(self, tmp_path: Path):
        server = RdapServer()
        server.bootstrap = httpx.ConnectError("no route")
        client = _rdap(tmp_path, server)
        assert client.base_for("brand.co.uk") == "https://rdap.nominet.uk/uk/"
        assert client.base_for("brand.com") == "https://rdap.verisign.com/com/v1/"
        result = client.lookup("brand.zz")
        assert result.error == "rdap_no_server_for_tld" and not result.fetched

    def test_429_backs_off_and_marks_not_fetched(self, tmp_path: Path):
        server = RdapServer()
        server.domain_responses["one.co.uk"] = httpx.Response(429)
        server.domain_responses["two.co.uk"] = httpx.Response(200, json=_json("rdap_nominet.json"))
        client = _rdap(tmp_path, server)
        first = client.lookup("one.co.uk")
        assert first.error == "rdap_rate_limited" and not first.fetched
        second = client.lookup("two.co.uk")
        assert second.error == "rdap_rate_limited"
        assert not any(u.endswith("two.co.uk") for u in server.requests)

    def test_timeout_retries_once_then_fails_soft(self, tmp_path: Path):
        server = RdapServer()
        server.domain_responses["slow.co.uk"] = httpx.ReadTimeout("slow")
        result = _rdap(tmp_path, server).lookup("slow.co.uk")
        assert result.error == "rdap_timeout" and not result.fetched
        assert sum(u.endswith("slow.co.uk") for u in server.requests) == 2

    def test_retry_recovers_from_a_server_error(self, tmp_path: Path):
        server = RdapServer()
        server.domain_responses["flaky.co.uk"] = [
            httpx.Response(503),
            httpx.Response(200, json=_json("rdap_nominet.json")),
        ]
        clock = FakeClock()
        result = _rdap(tmp_path, server, clock).lookup("flaky.co.uk")
        assert result.created == date(2025, 6, 2)
        assert clock.slept, "the second request to the same server waited"

    def test_unregistered_domain(self, tmp_path: Path):
        result = _rdap(tmp_path, RdapServer()).lookup("nobody.co.uk")
        assert result.error == "rdap_not_found" and result.created is None

    def test_bad_json_fails_soft(self, tmp_path: Path):
        server = RdapServer()
        server.domain_responses["junk.co.uk"] = httpx.Response(200, content=b"<html>")
        assert _rdap(tmp_path, server).lookup("junk.co.uk").error == "rdap_bad_json"


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


class FakeResolver:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.queries: list[tuple[str, str, float | None]] = []

    def resolve(self, qname: str, rdtype: str, *, lifetime: float | None = None) -> Any:
        self.queries.append((qname, rdtype, lifetime))
        value = self.answers.get(rdtype, ["x"])
        if isinstance(value, Exception):
            raise value
        if isinstance(value, type) and issubclass(value, Exception):
            raise value()
        return value


class TestDns:
    def test_all_records_present(self):
        resolver = FakeResolver({"A": ["1.2.3.4"], "MX": ["mx"], "NS": ["ns1"]})
        r = DnsChecker(resolver=resolver).check("brand.co.uk")
        assert (r.has_dns, r.has_a, r.has_mx, r.has_ns) == (True, True, True, True)
        assert resolver.queries[0] == ("brand.co.uk.", "A", 3.0)

    def test_nxdomain(self):
        resolver = FakeResolver({"A": dns.resolver.NXDOMAIN})
        r = DnsChecker(resolver=resolver).check("gone.co.uk")
        assert r.has_dns is False and r.has_mx is False and r.has_ns is False
        assert len(resolver.queries) == 1

    def test_no_mx(self):
        r = DnsChecker(resolver=FakeResolver({"MX": dns.resolver.NoAnswer})).check("b.co.uk")
        assert r.has_dns is True and r.has_mx is False and r.has_a is True

    def test_timeout_is_unknown_not_false(self):
        resolver = FakeResolver(
            {
                "A": dns.exception.Timeout,
                "MX": dns.exception.Timeout,
                "NS": dns.exception.Timeout,
            }
        )
        r = DnsChecker(resolver=resolver).check("slow.co.uk")
        assert r.has_dns is None and r.has_a is None
        assert "dns_timeout:A" in r.errors

    def test_unexpected_resolver_error_fails_soft(self):
        r = DnsChecker(resolver=FakeResolver({"NS": RuntimeError("boom")})).check("b.co.uk")
        assert r.has_ns is None and r.has_dns is True
        assert any(e.startswith("dns_error:NS") for e in r.errors)


# ---------------------------------------------------------------------------
# homepage
# ---------------------------------------------------------------------------


class Site:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[str] = []
        self.user_agents: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        self.user_agents.append(request.headers.get("user-agent", ""))
        value = self.routes.get(url)
        if value is None:
            return httpx.Response(404)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, text=value, headers={"content-type": "text/html"})


def _fetcher(site: Site, **overrides: Any) -> HomepageFetcher:
    cfg = json.loads(json.dumps(domain_config()))
    cfg["homepage"].update(overrides)
    clock = FakeClock()
    return HomepageFetcher(
        UA,
        cfg,
        transport=httpx.MockTransport(site),
        clock=clock,
        sleep=clock.sleep,
        resolver=public_resolver,
    )


def public_resolver(host: str) -> list[str]:
    """Every test host resolves to a public documentation-free address."""
    return ["93.184.215.14"]


class TestHomepage:
    def test_fetches_page_with_honest_user_agent(self):
        site = Site({"https://brand.co.uk/": _html("shopify.html")})
        r = _fetcher(site).fetch("brand.co.uk")
        assert r.fetched and r.status == 200 and "cdn.shopify.com" in r.html
        assert r.final_url == "https://brand.co.uk/"
        assert r.redirected_off_domain is False and r.redirect_target_kind == "same_domain"
        assert site.requests == ["https://brand.co.uk/robots.txt", "https://brand.co.uk/"]
        assert all(ua == UA for ua in site.user_agents)

    def test_robots_disallow_stops_before_the_page(self):
        site = Site(
            {
                "https://brand.co.uk/robots.txt": "User-agent: *\nDisallow: /\n",
                "https://brand.co.uk/": _html("shopify.html"),
            }
        )
        r = _fetcher(site).fetch("brand.co.uk")
        assert r.robots_disallowed and not r.fetched
        assert "https://brand.co.uk/" not in site.requests

    def test_robots_disallow_for_our_agent_only(self):
        robots = "User-agent: LaunchTrace\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
        site = Site({"https://brand.co.uk/robots.txt": robots, "https://brand.co.uk/": "x"})
        assert _fetcher(site).fetch("brand.co.uk").robots_disallowed

    def test_robots_allowing_other_paths_lets_us_in(self):
        robots = "User-agent: *\nDisallow: /admin\n"
        site = Site({"https://brand.co.uk/robots.txt": robots, "https://brand.co.uk/": "hello"})
        assert _fetcher(site).fetch("brand.co.uk").fetched

    def test_body_is_capped(self):
        big = "<html>" + ("a" * 50_000) + "</html>"
        site = Site({"https://brand.co.uk/": big})
        r = _fetcher(site, max_bytes=1000).fetch("brand.co.uk")
        assert r.fetched and r.truncated and len(r.html) == 1000

    def test_timeout_falls_back_to_http(self):
        site = Site(
            {
                "https://brand.co.uk/robots.txt": httpx.ConnectTimeout("tls"),
                "http://brand.co.uk/": _html("plain_site.html"),
            }
        )
        r = _fetcher(site).fetch("brand.co.uk")
        assert r.fetched and r.final_url == "http://brand.co.uk/"
        assert "unreachable_https" in r.errors

    def test_timeout_everywhere_fails_soft(self):
        site = Site(
            {
                "https://brand.co.uk/robots.txt": httpx.ReadTimeout("x"),
                "http://brand.co.uk/robots.txt": httpx.ReadTimeout("x"),
            }
        )
        r = _fetcher(site).fetch("brand.co.uk")
        assert not r.fetched and r.status is None
        assert r.errors == ["unreachable_https", "unreachable_http"]

    def test_page_timeout_fails_soft(self):
        site = Site(
            {
                "https://brand.co.uk/": httpx.ReadTimeout("x"),
                "http://brand.co.uk/": httpx.ReadTimeout("x"),
            }
        )
        r = _fetcher(site).fetch("brand.co.uk")
        assert not r.fetched
        assert "homepage_timeout_https" in r.errors

    def test_redirect_to_parking_host(self):
        site = Site(
            {
                "https://brand.co.uk/": httpx.Response(
                    302, headers={"location": "https://www.sedo.com/search/details/?d=brand"}
                ),
                "https://www.sedo.com/search/details/?d=brand": "Buy this domain",
            }
        )
        r = _fetcher(site).fetch("brand.co.uk")
        assert r.fetched and r.redirected_off_domain is True
        assert r.redirect_target_kind == "parking"
        assert r.final_url == "https://www.sedo.com/search/details/?d=brand"

    def test_redirect_to_marketplace(self):
        site = Site(
            {
                "https://brand.co.uk/": httpx.Response(
                    301, headers={"location": "https://www.etsy.com/shop/Brand"}
                ),
                "https://www.etsy.com/shop/Brand": "Add to basket",
            }
        )
        r = _fetcher(site).fetch("brand.co.uk")
        assert r.redirect_target_kind == "marketplace"

    def test_redirect_within_domain_is_not_off_domain(self):
        site = Site(
            {
                "https://brand.co.uk/": httpx.Response(
                    301, headers={"location": "https://shop.brand.co.uk/"}
                ),
                "https://shop.brand.co.uk/": "hello",
            }
        )
        r = _fetcher(site).fetch("brand.co.uk")
        assert r.redirected_off_domain is False

    def test_redirect_loop_is_bounded(self):
        site = Site(
            {
                "https://brand.co.uk/": httpx.Response(
                    302, headers={"location": "https://brand.co.uk/"}
                ),
            }
        )
        r = _fetcher(site, max_redirects=3, check_robots_txt=False).fetch("brand.co.uk")
        assert not r.fetched and "homepage_too_many_redirects" in r.errors
        assert len(site.requests) <= 4

    def test_error_status_is_recorded_not_fetched(self):
        site = Site({"https://brand.co.uk/": httpx.Response(503, text="down")})
        r = _fetcher(site).fetch("brand.co.uk")
        assert not r.fetched and r.status == 503 and "homepage_http_503" in r.errors


# ---------------------------------------------------------------------------
# platforms and stage
# ---------------------------------------------------------------------------


class TestPlatforms:
    @pytest.mark.parametrize(
        "fixture,platform,shop",
        [
            ("shopify.html", "shopify", True),
            ("woocommerce.html", "woocommerce", True),
            ("squarespace.html", "squarespace", False),
            ("wix.html", "wix", False),
            ("bigcartel.html", "bigcartel", True),
            ("wordpress.html", "wordpress", False),
        ],
    )
    def test_fingerprints(self, fixture, platform, shop):
        match = detect_platform(_html(fixture), {})
        assert match is not None
        assert (match.key, match.is_shop_platform) == (platform, shop)

    def test_header_fingerprint(self):
        match = detect_platform("<html></html>", {"X-ShopId": "123"})
        assert match is not None and match.key == "shopify"

    def test_no_platform(self):
        assert detect_platform(_html("plain_site.html"), {}) is None

    def test_live_store(self):
        page = assess_page(_html("shopify.html"), {}, "https://brand.co.uk/")
        assert page.store_detected and not page.is_holding_page and not page.is_parked

    def test_coming_soon(self):
        page = assess_page(_html("coming_soon.html"), {}, "https://brand.co.uk/")
        assert page.is_holding_page and not page.is_parked and not page.store_detected

    def test_locked_shopify_store_is_a_holding_page(self):
        page = assess_page(_html("shopify_locked.html"), {}, "https://brand.co.uk/")
        assert page.platform is not None and page.platform.key == "shopify"
        assert page.is_holding_page

    def test_parked(self):
        page = assess_page(_html("parked.html"), {}, "https://brand.co.uk/")
        assert page.is_parked and page.is_holding_page

    def test_parking_redirect(self):
        page = assess_page("Buy this domain", {}, "https://dan.com/buy/brand", "parking")
        assert page.is_parked

    def test_plain_site_is_neither(self):
        page = assess_page(_html("plain_site.html"), {}, "https://brand.co.uk/")
        assert not page.is_holding_page and not page.store_detected

    def test_visible_text_strips_scripts(self):
        assert visible_text("<script>var a='coming soon'</script><p>Hi</p>") == "Hi"

    @pytest.mark.parametrize(
        "facts,stage",
        [
            ({"has_domain": False}, "no_domain"),
            ({"has_domain": True, "has_dns": False}, "no_dns"),
            ({"has_domain": True, "has_dns": True, "homepage_fetched": False}, "unknown"),
            ({"has_domain": True, "has_dns": None, "homepage_fetched": True, "is_parked": True}, "parked"),
            (
                {"has_domain": True, "has_dns": True, "homepage_fetched": True,
                 "redirected_to_marketplace": True, "store_detected": True},
                "unknown",
            ),
            (
                {"has_domain": True, "has_dns": True, "homepage_fetched": True,
                 "is_parked": False, "is_holding_page": True},
                "holding_page",
            ),
            (
                {"has_domain": True, "has_dns": True, "homepage_fetched": True,
                 "is_parked": False, "is_holding_page": False, "store_detected": True},
                "live_store",
            ),
            (
                {"has_domain": True, "has_dns": True, "homepage_fetched": True,
                 "is_parked": False, "is_holding_page": False, "store_detected": False,
                 "shop_platform": True},
                "live_store",
            ),
            (
                {"has_domain": True, "has_dns": True, "homepage_fetched": True,
                 "is_parked": False, "is_holding_page": False, "store_detected": False,
                 "shop_platform": False},
                "site_no_store",
            ),
        ],
    )  # fmt: skip
    def test_stage_rules(self, facts, stage):
        assert resolve_stage(facts) == stage


# ---------------------------------------------------------------------------
# the layer
# ---------------------------------------------------------------------------


def _web(**kw: Any) -> WebEnrichment:
    base: dict[str, Any] = {"attempted": True, "provider": "fixture"}
    base.update(kw)
    return WebEnrichment(**base)


class ExplodingProber:
    name = "exploding"
    enabled = True

    def probe(self, domain: str) -> DomainSignals:
        raise RuntimeError("kaboom")


class TestDomainSelection:
    def test_verified_website(self):
        assert select_domain(_web(website="https://shop.brand.co.uk/about")) == (
            "brand.co.uk",
            "verified_website",
            None,
        )

    def test_candidate_is_ignored_by_default(self):
        domain, _, reason = select_domain(_web(candidate_website="https://maybe.co.uk"))
        assert domain is None and reason == "no_verified_website"

    def test_candidate_when_enabled(self):
        cfg = json.loads(json.dumps(domain_config()))
        cfg["domain_selection"]["use_candidate_website"] = True
        domain, source, _ = select_domain(_web(candidate_website="https://maybe.co.uk"), cfg)
        assert (domain, source) == ("maybe.co.uk", "candidate_website")

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.etsy.com/shop/Brand",
            "https://www.instagram.com/brand",
            "https://brand.myshopify.com/",
            "https://www.tripadvisor.co.uk/Restaurant",
        ],
    )
    def test_marketplace_and_social_hosts_are_skipped(self, url):
        domain, _, reason = select_domain(_web(website=url))
        assert domain is None and reason is not None and reason.startswith("skipped_host:")

    def test_ip_literal_is_not_probed(self):
        domain, _, reason = select_domain(_web(website="http://10.0.0.1/"))
        assert domain is None and reason is not None and "not_a_public_hostname" in reason


class TestDomainLayer:
    def test_null_prober_gives_nothing(self):
        layer = DomainLayer(NullDomainProber())
        assert layer.signals_for(_web(website="https://brand.co.uk")) is None

    def test_no_search_no_claim(self):
        layer = DomainLayer(FixtureDomainProber(default={}))
        assert layer.signals_for(WebEnrichment(attempted=False)) is None

    def test_no_verified_website_is_no_domain_without_a_probe(self):
        prober = FixtureDomainProber(default={})
        signals = DomainLayer(prober).signals_for(_web())
        assert signals is not None
        assert signals.web_presence_stage == "no_domain" and not signals.checked
        assert prober.calls == []

    def test_cap_and_deduplication(self):
        cfg = json.loads(json.dumps(domain_config()))
        cfg["domain_selection"]["max_domains_per_run"] = 2
        prober = FixtureDomainProber(default={"web_presence_stage": "site_no_store"})
        layer = DomainLayer(prober, cfg)
        for name in ("a.co.uk", "a.co.uk", "b.co.uk", "c.co.uk"):
            layer.signals_for(_web(website=f"https://{name}"))
        assert prober.calls == ["a.co.uk", "b.co.uk"]
        capped = layer.signals_for(_web(website="https://d.co.uk"))
        assert capped is not None and not capped.checked
        assert capped.errors == ["domain_cap_reached"]
        assert layer.cap_skipped == 2

    def test_prober_exception_becomes_an_error(self):
        signals = DomainLayer(ExplodingProber()).signals_for(_web(website="https://b.co.uk"))
        assert signals is not None and signals.checked
        assert signals.errors[0].startswith("probe_failed: RuntimeError")
        assert signals.web_presence_stage == "unknown"

    def test_fixture_from_json(self):
        prober = FixtureDomainProber.from_json(DOMAIN_FIXTURES / "probes.json")
        s = prober.probe("crumbledge.co.uk")
        assert s.rdap_created == date(2025, 6, 2) and s.platform == "shopify"
        assert prober.probe("other.co.uk").web_presence_stage == "site_no_store"


class TestLiveProber:
    def _prober(self, settings: Settings, tmp_path: Path, routes: dict[str, Any], dns_answers):
        server = RdapServer()
        server.domain_responses["brand.co.uk"] = httpx.Response(
            200, json=_json("rdap_nominet.json")
        )
        clock = FakeClock()
        site = Site(routes)
        return (
            LiveDomainProber(
                settings,
                rdap=_rdap(tmp_path, server),
                dns_checker=DnsChecker(resolver=FakeResolver(dns_answers)),
                homepage=HomepageFetcher(
                    UA,
                    domain_config(),
                    transport=httpx.MockTransport(site),
                    clock=clock,
                    sleep=clock.sleep,
                    resolver=public_resolver,
                ),
            ),
            site,
        )

    def test_composes_every_check(self, settings: Settings, tmp_path: Path):
        prober, _ = self._prober(
            settings, tmp_path, {"https://brand.co.uk/": _html("shopify.html")}, {}
        )
        s = prober.probe("brand.co.uk")
        assert s.checked and s.rdap_fetched and s.rdap_created == date(2025, 6, 2)
        assert s.has_dns and s.has_mx
        assert s.platform == "shopify" and s.shop_platform and s.store_detected
        assert s.web_presence_stage == "live_store"
        assert s.errors == []

    def test_holding_page(self, settings: Settings, tmp_path: Path):
        prober, _ = self._prober(
            settings, tmp_path, {"https://brand.co.uk/": _html("coming_soon.html")}, {}
        )
        s = prober.probe("brand.co.uk")
        assert s.is_holding_page and s.web_presence_stage == "holding_page"

    def test_nxdomain_skips_the_homepage(self, settings: Settings, tmp_path: Path):
        prober, site = self._prober(settings, tmp_path, {}, {"A": dns.resolver.NXDOMAIN})
        s = prober.probe("brand.co.uk")
        assert s.web_presence_stage == "no_dns"
        assert site.requests == []

    def test_robots_disallowed(self, settings: Settings, tmp_path: Path):
        prober, _ = self._prober(
            settings,
            tmp_path,
            {"https://brand.co.uk/robots.txt": "User-agent: *\nDisallow: /\n"},
            {},
        )
        s = prober.probe("brand.co.uk")
        assert s.robots_disallowed and s.web_presence_stage == "unknown"

    def test_checks_can_be_switched_off(self, settings: Settings):
        cfg = json.loads(json.dumps(domain_config()))
        cfg["enabled_checks"] = {"rdap": False, "dns": True, "homepage": False}
        prober = LiveDomainProber(settings, cfg, dns_checker=DnsChecker(resolver=FakeResolver({})))
        s = prober.probe("brand.co.uk")
        assert not s.rdap_fetched and s.has_dns and not s.homepage_fetched
