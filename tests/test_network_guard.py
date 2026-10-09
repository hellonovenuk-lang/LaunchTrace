"""The autouse network guard in conftest.py: tests cannot reach the internet."""

from __future__ import annotations

import socket
import threading

import httpx
import pytest

from tests.conftest import NetworkBlockedError


def test_create_connection_to_the_internet_is_blocked():
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("example.com", 443), timeout=1)


def test_raw_socket_connect_is_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.connect(("93.184.216.34", 80))
        with pytest.raises(NetworkBlockedError):
            sock.connect_ex(("93.184.216.34", 80))
    finally:
        sock.close()


def test_an_http_client_cannot_get_out():
    with pytest.raises((NetworkBlockedError, httpx.HTTPError)) as info:
        httpx.get("https://www.ipo.gov.uk/", timeout=1)
    # httpx may wrap it, but the cause is always the guard.
    chain = [info.value, info.value.__cause__, info.value.__context__]
    assert any(isinstance(e, NetworkBlockedError) for e in chain)


def test_loopback_is_allowed():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted: list[socket.socket] = []
    thread = threading.Thread(target=lambda: accepted.append(server.accept()[0]))
    thread.start()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.close()
        thread.join(timeout=2)
        assert accepted
    finally:
        for s in accepted:
            s.close()
        server.close()


def test_unix_sockets_are_allowed(tmp_path):
    if not hasattr(socket, "AF_UNIX"):  # pragma: no cover - Windows
        pytest.skip("no AF_UNIX")
    path = str(tmp_path / "s.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(path)
    finally:
        client.close()
        server.close()


# -- UDP, name resolution and storage isolation (LOW-2, LOW-3; D-705) ---------


def test_udp_sendto_is_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.sendto(b"\x00", ("8.8.8.8", 53))
        with pytest.raises(NetworkBlockedError):
            sock.sendto(b"\x00", 0, ("8.8.8.8", 53))
    finally:
        sock.close()


def test_udp_sendmsg_is_blocked():
    if not hasattr(socket.socket, "sendmsg"):  # pragma: no cover - Windows
        pytest.skip("no sendmsg")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.sendmsg([b"\x00"], [], 0, ("8.8.8.8", 53))
    finally:
        sock.close()


def test_udp_to_loopback_is_allowed():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(b"ping", server.getsockname())
        assert server.recvfrom(16)[0] == b"ping"
    finally:
        client.close()
        server.close()


def test_getaddrinfo_is_blocked_for_public_names():
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)


def test_getaddrinfo_allows_localhost():
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("localhost", 80)


def test_dnspython_cannot_query_a_public_resolver():
    import dns.message
    import dns.query

    query = dns.message.make_query("example.com", "A")
    with pytest.raises(NetworkBlockedError):
        dns.query.udp(query, "8.8.8.8", timeout=1)


def test_tests_never_use_the_real_database_or_cache():
    import os
    import tempfile

    from src.settings import DATA_DIR, Settings

    tmp = os.path.realpath(tempfile.gettempdir())
    settings = Settings()  # type: ignore[call-arg]
    assert os.path.realpath(settings.database_url.removeprefix("sqlite:///")).startswith(tmp)
    assert os.path.realpath(settings.cache_dir).startswith(tmp)
    assert str(DATA_DIR) not in settings.database_url
