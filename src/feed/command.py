"""``python -m src.pipeline build-feed``: write the public feed as static files."""

from __future__ import annotations

from pathlib import Path

from src.db import init_db, session_scope
from src.feed.build import build_feed, journal_slug
from src.feed.render import render_site, static_links, write_site
from src.logging_setup import get_logger
from src.settings import get_settings

log = get_logger(__name__)
DEFAULT_OUT = "public"


def build_static_site(out: Path, journal: str | None = None) -> list[Path]:
    """Build from the configured database into ``out``. Returns the files written.

    With ``journal``, only that week's page and JSON are (re)written and nothing
    is pruned; the index and feeds are left as they are.
    """
    settings = get_settings()
    init_db()
    with session_scope() as session:
        feed = build_feed(session)
    files = render_site(feed, static_links(feed, settings.site_url))
    log.info(
        "feed.built",
        weeks=len(feed.weeks),
        entries=sum(len(w.entries) for w in feed.weeks),
        pending_weeks=feed.pending_weeks,
        excluded=feed.exclusions,
    )
    if journal:
        slug = journal_slug(journal)
        files = {k: v for k, v in files.items() if k in {f"{slug}.html", f"{slug}.json"}}
        if not files:
            return []
        return write_site(files, out, prune=False)
    files[".nojekyll"] = b""
    return write_site(files, out)


def cmd_build_feed(args) -> int:  # type: ignore[no-untyped-def]
    out = Path(getattr(args, "out", None) or DEFAULT_OUT)
    journal = getattr(args, "journal", None)
    written = build_static_site(out, journal)
    if journal and not written:
        print(f"Journal {journal} is not public (yet): nothing written to {out}/")
        return 0
    print(f"Public feed: {len(written)} file(s) written to {out}/")
    return 0
