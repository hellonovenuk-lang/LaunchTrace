"""The /feed routes keep the rendered feed in memory for web_cache_seconds (D-708)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.feed.web as feed_web
from src.settings import load_config


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    builds: list[int] = []
    now = [1000.0]

    def fake_build(session: Any) -> object:
        builds.append(1)
        return object()

    monkeypatch.setattr(feed_web, "build_feed", fake_build)
    monkeypatch.setattr(
        feed_web,
        "render_site",
        lambda feed, links: {"index.html": f"build {len(builds)}".encode()},
    )
    monkeypatch.setattr(feed_web, "app_links", lambda feed, url: {})
    application = FastAPI()
    application.include_router(feed_web.build_feed_router(lambda: None, clock=lambda: now[0]))
    return TestClient(application), builds, now


def test_the_feed_is_built_once_per_ttl(app):
    client, builds, now = app
    ttl = float(load_config("public_feed.json")["web_cache_seconds"])
    assert ttl > 0
    assert client.get("/feed/").text == "build 1"
    assert client.get("/feed/").text == "build 1"
    assert len(builds) == 1
    now[0] += ttl + 1
    assert client.get("/feed/").text == "build 2"
    assert len(builds) == 2
