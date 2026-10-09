"""The public feed on the website: ``/feed/``, ``/feed.xml``, ``/feed.json``.

Generated on request from the database with the same builder and renderers as
the static build, so the two cannot drift. The pages link a stylesheet rather
than inlining it, which keeps the site's Content-Security-Policy intact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from src.feed.build import build_feed
from src.feed.render import app_links, render_site, stylesheet
from src.settings import get_settings

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


def build_feed_router(db_session: Callable[..., Any]) -> APIRouter:
    router = APIRouter()

    def _files(session: Session) -> dict[str, bytes]:
        feed = build_feed(session)
        return render_site(feed, app_links(feed, get_settings().site_url))

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
