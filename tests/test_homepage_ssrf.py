"""The homepage fetch never requests a private, loopback or metadata address (D-701).

Every hop -- robots.txt, the page, every redirect target -- is resolved with an
injected resolver and refused when any address is not public. Network-free:
``httpx.MockTransport`` answers, a dict answers DNS.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.enrich.domain.common import domain_config
from src.enrich.domain.homepage import HomepageFetcher, is_blocked_address

PUBLIC = "93.184.215.14"
DNS = {
    "brand.co.uk": [PUBLIC],
    "shop.example.com": [PUBLIC],
    "internal.brand.co.uk": ["10.1.2.3"],
    "169.254.169.254.nip.io": ["169.254.169.254"],
    "v6local.brand.co.uk": ["::1"],
    "mapped.brand.co.uk": ["::ffff:127.0.0.1"],
    "mixed.brand.co.uk": [PUBLIC, "192.168.0.10"],
}


def resolver(host: str) -> list[str]:
    if host not in DNS:
        raise OSError(f"no such host {host}")
    return DNS[host]


class Site:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        value = self.routes.get(url)
        if value is None:
            return httpx.Response(404)
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, text=value, headers={"content-type": "text/html"})


def _redirect(location: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": location})


def _fetcher(site: Site, **overrides: Any) -> HomepageFetcher:
    cfg = json.loads(json.dumps(domain_config()))
    cfg["homepage"].update(overrides)
    return HomepageFetcher(
        "LaunchTrace-test",
        cfg,
        transport=httpx.MockTransport(site),
        sleep=lambda _s: None,
        resolver=resolver,
    )


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "https://internal.brand.co.uk/",
        "http://169.254.169.254.nip.io/latest/meta-data/",
        "http://[::1]/",
        "https://v6local.brand.co.uk/",
        "https://mapped.brand.co.uk/",
        "http://[::ffff:169.254.169.254]/",
        "https://mixed.brand.co.uk/",
        "http://0.0.0.0/",
    ],
)
def test_a_redirect_to_a_private_address_is_never_requested(location: str):
    site = Site({"https://brand.co.uk/": _redirect(location)})
    r = _fetcher(site).fetch("brand.co.uk")
    assert not r.fetched
    assert "blocked_private_address" in r.errors
    assert site.requests == ["https://brand.co.uk/robots.txt", "https://brand.co.uk/"]


def test_a_robots_redirect_to_a_private_address_stops_everything():
    site = Site({"https://brand.co.uk/robots.txt": _redirect("http://169.254.169.254/")})
    r = _fetcher(site).fetch("brand.co.uk")
    assert not r.fetched and r.errors == ["blocked_private_address"]
    assert site.requests == ["https://brand.co.uk/robots.txt"]


def test_the_initial_host_is_checked_too():
    site = Site({})
    r = _fetcher(site).fetch("internal.brand.co.uk")
    assert r.errors == ["blocked_private_address"] and site.requests == []


def test_a_public_redirect_is_followed():
    site = Site(
        {
            "https://brand.co.uk/": _redirect("https://shop.example.com/store"),
            "https://shop.example.com/store": "hello shop",
        }
    )
    r = _fetcher(site).fetch("brand.co.uk")
    assert r.fetched and r.final_url == "https://shop.example.com/store"
    assert r.redirect_target_kind == "other_domain"
    assert "hello shop" in r.html


def test_a_relative_redirect_is_resolved_against_the_current_url():
    site = Site(
        {
            "https://brand.co.uk/": _redirect("/home"),
            "https://brand.co.uk/home": "home page",
        }
    )
    r = _fetcher(site).fetch("brand.co.uk")
    assert r.fetched and r.final_url == "https://brand.co.uk/home"


@pytest.mark.parametrize(
    ("location", "error"),
    [
        ("file:///etc/passwd", "blocked_scheme"),
        ("gopher://brand.co.uk/", "blocked_scheme"),
        ("https://brand.co.uk:8443/", "blocked_port"),
    ],
)
def test_other_schemes_and_ports_are_refused(location: str, error: str):
    site = Site({"https://brand.co.uk/": _redirect(location)})
    r = _fetcher(site).fetch("brand.co.uk")
    assert error in r.errors and len(site.requests) == 2


def test_a_non_default_port_can_be_allowed_by_config():
    site = Site(
        {
            "https://brand.co.uk/": _redirect("https://brand.co.uk:8443/"),
            "https://brand.co.uk:8443/": "ok",
        }
    )
    r = _fetcher(site, allow_non_default_ports=True).fetch("brand.co.uk")
    assert r.fetched


def test_redirects_are_not_followed_by_httpx():
    fetcher = _fetcher(Site({}))
    with fetcher._client() as client:
        assert client.follow_redirects is False


class TestRdapResponseCap:
    """LOW-6: an RDAP body is abandoned past its byte cap, before JSON parsing."""

    def _client(self, tmp_path, handler, **cfg: Any):  # type: ignore[no-untyped-def]
        from src.enrich.domain.rdap import RdapClient

        config = {**domain_config()["rdap"], **cfg}
        return RdapClient(
            "LaunchTrace-test",
            tmp_path,
            config=config,
            transport=httpx.MockTransport(handler),
            sleep=lambda _s: None,
        )

    @staticmethod
    def _handler(domain_body: bytes):  # type: ignore[no-untyped-def]
        def handle(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/rdap/dns.json"):
                return httpx.Response(
                    200, json={"services": [[["uk"], ["https://rdap.nominet.uk/uk/"]]]}
                )
            return httpx.Response(200, content=domain_body)

        return handle

    def test_an_oversized_domain_answer_is_refused(self, tmp_path):
        huge = b'{"events": [], "pad": "' + b"x" * 300_000 + b'"}'
        r = self._client(tmp_path, self._handler(huge)).lookup("brand.co.uk")
        assert not r.fetched and r.error == "rdap_response_too_large"

    def test_a_normal_answer_is_read(self, tmp_path):
        body = json.dumps(
            {"events": [{"eventAction": "registration", "eventDate": "2025-06-02T10:00:00Z"}]}
        ).encode()
        r = self._client(tmp_path, self._handler(body)).lookup("brand.co.uk")
        assert r.fetched and str(r.created) == "2025-06-02"

    def test_the_cap_comes_from_config(self, tmp_path):
        body = json.dumps({"events": [], "pad": "x" * 2000}).encode()
        client = self._client(tmp_path, self._handler(body), max_response_bytes=1000)
        assert client.lookup("brand.co.uk").error == "rdap_response_too_large"

    def test_an_oversized_bootstrap_falls_back(self, tmp_path):
        def handle(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/rdap/dns.json"):
                return httpx.Response(200, content=b"[" + b" " * 2_000_000 + b"]")
            return httpx.Response(404)

        client = self._client(tmp_path, handle)
        assert client.base_for("brand.co.uk") == "https://rdap.nominet.uk/uk/"
        assert not (tmp_path / "rdap" / "dns.json").exists()


@pytest.mark.parametrize(
    ("address", "blocked"),
    [
        ("169.254.169.254", True),
        ("127.0.0.1", True),
        ("10.0.0.1", True),
        ("172.16.5.4", True),
        ("192.168.1.1", True),
        ("100.64.0.1", True),
        ("224.0.0.1", True),
        ("0.0.0.0", True),
        ("240.0.0.1", True),
        ("::1", True),
        ("::", True),
        ("fe80::1", True),
        ("fc00::1", True),
        ("::ffff:10.0.0.1", True),
        ("2002:a9fe:a9fe::1", True),
        ("not-an-ip", True),
        (PUBLIC, False),
        ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", False),
    ],
)
def test_is_blocked_address(address: str, blocked: bool):
    assert is_blocked_address(address) is blocked
