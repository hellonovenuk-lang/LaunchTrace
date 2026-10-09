"""The public feed on the website: ``/feed/``, ``/feed.xml``, ``/feed.json``.

Generated on request from the database with the same builder and renderers as
the static build, so the two cannot drift. The pages link a stylesheet rather
than inlining it, which keeps the site's Content-Security-Policy intact.

The rendered site is kept in memory for ``web_cache_seconds``
(config/public_feed.json, 0 = rebuild on every request), so a burst of
requests does not rebuild the feed from the database each time (D-708).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from src.feed.build import CONFIG_NAME, build_feed
from src.feed.render import app_links, render_site, stylesheet
from src.settings import get_settings, load_config

MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".xml": "application/xml; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
_SPECIAL = {
    "feed.xml": "application/atom+xml; charset=utf-8",
    "rss.xml": "application/rss+xml; charset=utf-8",
}


def build_feed_router(
    db_session: Callable[..., Any], clock: Callable[[], float] = time.monotonic
) -> APIRouter:
    router = APIRouter()
    ttl = float(load_config(CONFIG_NAME).get("web_cache_seconds", 300) or 0)
    cache: dict[str, Any] = {"at": None, "files": None}

    def _files(session: Session) -> dict[str, bytes]:
        now = clock()
        if ttl > 0 and cache["files"] is not None and now - cache["at"] < ttl:
            return dict(cache["files"])
        feed = build_feed(session)
        files = render_site(feed, app_links(feed, get_settings().site_url))
        cache.update(at=now, files=files)
        return dict(files)

    def _serve(session: Session, name: str) -> Response:
        if name == "feed.css":
            return Response(stylesheet(), media_type=MEDIA_TYPES[".css"])
        files = _files(session)
        if name not in files:
            raise HTTPException(status_code=404, detail="Not found")
        suffix = name[name.rfind(".") :]
        media_type = _SPECIAL.get(name) or MEDIA_TYPES.get(suffix, "application/octet-stream")
        return Response(files[name], media_type=media_type)

    @router.get("/feed", include_in_schema=False)
    def feed_root() -> RedirectResponse:
        return RedirectResponse("/feed/", status_code=308)

    @router.get("/feed/", include_in_schema=False)
    def feed_index(session: Session = Depends(db_session)) -> Response:
        return _serve(session, "index.html")

    @router.get("/feed.xml", include_in_schema=False)
    def feed_atom(session: Session = Depends(db_session)) -> Response:
        return _serve(session, "feed.xml")

    @router.get("/feed.json", include_in_schema=False)
    def feed_json(session: Session = Depends(db_session)) -> Response:
        return _serve(session, "feed.json")

    @router.get("/feed/{name}", include_in_schema=False)
    def feed_file(name: str, session: Session = Depends(db_session)) -> Response:
        # A bare journal number is the week's page.
        if "." not in name or not name.endswith((".html", ".json", ".xml", ".css")):
            name = f"{name}.html"
        return _serve(session, name)

    return router
