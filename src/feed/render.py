"""Turn a :class:`~src.feed.build.Feed` into HTML, Atom, RSS and JSON.

One set of files serves both the static build and the website's ``/feed``
routes; only :class:`Links` differs. The static build inlines its stylesheet
and points the footer and call to action at absolute ``SITE_URL`` addresses, so
the directory can be published anywhere (GitHub Pages) as-is. The website links
a stylesheet (its CSP forbids inline styles) and uses site-relative links.

Every renderer is a pure function of its inputs: no clocks, sorted input, fixed
serialisation. A build of the same database is byte-for-byte identical.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from lxml import etree

from src.feed.build import Feed, PublicEntry, Week

TEMPLATES_DIR = Path(__file__).parent / "templates"
FEED_ID = "urn:launchtrace:feed"
ATOM_NS = "http://www.w3.org/2005/Atom"
JSON_VERSION = "1.0"
MARKER_FILE = ".launchtrace-feed"
# Fixed so an empty feed is still deterministic (Atom requires <updated>).
EMPTY_UPDATED = "1970-01-01T00:00:00Z"

ATTRIBUTION = (
    "Trade mark data from the UK Intellectual Property Office and company data from "
    "Companies House, used under the Open Government Licence v3.0. LaunchTrace is not "
    "affiliated with or endorsed by the Intellectual Property Office or Companies House."
)
OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"


@dataclass(frozen=True)
class Links:
    """Where the page chrome points. ``feed_base`` is absolute, ending in '/'."""

    home: str
    privacy: str
    terms: str
    attribution: str
    unsubscribe: str
    cta: str
    feed_base: str
    inline_css: bool


def _site(site_url: str) -> str:
    return (site_url or "").rstrip("/")


def _feed_base(feed: Feed, site_url: str) -> str:
    base = feed.config.public_base_url.strip() or f"{_site(site_url)}/feed/"
    return base if base.endswith("/") else base + "/"


def static_links(feed: Feed, site_url: str) -> Links:
    """Absolute links to the website, so the files work from any host."""
    site = _site(site_url)
    return Links(
        home=f"{site}/",
        privacy=f"{site}/privacy",
        terms=f"{site}/terms",
        attribution=f"{site}/attribution",
        unsubscribe=f"{site}/unsubscribe",
        cta=f"{site}{feed.config.cta_path}",
        feed_base=_feed_base(feed, site_url),
        inline_css=True,
    )


def app_links(feed: Feed, site_url: str) -> Links:
    """Site-relative links for pages served by the website itself."""
    return Links(
        home="/",
        privacy="/privacy",
        terms="/terms",
        attribution="/attribution",
        unsubscribe="/unsubscribe",
        cta=feed.config.cta_path,
        feed_base=_feed_base(feed, site_url),
        inline_css=False,
    )


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def stylesheet() -> str:
    return (TEMPLATES_DIR / "feed.css").read_text(encoding="utf-8")


def render_index_html(feed: Feed, links: Links) -> str:
    return (
        _env()
        .get_template("index.html.j2")
        .render(feed=feed, config=feed.config, links=links, css=stylesheet())
    )


def render_week_html(feed: Feed, week: Week, links: Links) -> str:
    return (
        _env()
        .get_template("week.html.j2")
        .render(feed=feed, week=week, config=feed.config, links=links, css=stylesheet())
    )


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _week_payload(week: Week, links: Links) -> dict[str, Any]:
    return {
        "journal_number": week.journal_number,
        "publication_date": week.publication_date.isoformat(),
        "url": f"{links.feed_base}{week.slug}.html",
        "json_url": f"{links.feed_base}{week.slug}.json",
        "qualifying_count": week.qualifying_count,
        "entries": [e.as_dict() for e in week.entries],
    }


def _meta(feed: Feed) -> dict[str, Any]:
    return {
        "version": JSON_VERSION,
        "title": feed.config.site_title,
        "description": feed.config.site_description,
        "updated": feed.updated.isoformat() if feed.updated else None,
        "delay_weeks": feed.config.delay_weeks,
        "top_n_per_week": feed.config.top_n_per_week,
        "attribution": ATTRIBUTION,
        "licence_url": OGL_URL,
    }


def render_feed_json(feed: Feed, links: Links) -> str:
    payload = _meta(feed)
    payload["home_page_url"] = links.feed_base
    payload["weeks"] = [_week_payload(w, links) for w in feed.weeks]
    return _dumps(payload)


def render_week_json(feed: Feed, week: Week, links: Links) -> str:
    payload = _meta(feed)
    payload.update(_week_payload(week, links))
    return _dumps(payload)


# ---------------------------------------------------------------------------
# Atom and RSS
# ---------------------------------------------------------------------------


def _rfc3339(day: date | None) -> str:
    return f"{day.isoformat()}T00:00:00Z" if day else EMPTY_UPDATED


def _rfc822(day: date) -> str:
    return format_datetime(datetime(day.year, day.month, day.day, tzinfo=UTC), usegmt=True)


def _entry_title(e: PublicEntry) -> str:
    return f"{e.brand_name} ({e.company_name})"


def _entry_summary(e: PublicEntry) -> str:
    parts = [
        f"Company {e.company_name} ({e.company_number})",
        f"Category: {e.product_category}" if e.product_category else "",
        f"Stage: {e.launch_stage}",
        f"Filed: {e.filing_date}" if e.filing_date else "",
        f"Region: {e.region}" if e.region else "",
        e.reason,
    ]
    return ". ".join(p for p in parts if p) + "."


def _sub(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> Any:
    el = etree.SubElement(parent, tag, {k.rstrip("_"): v for k, v in attrs.items()})
    if text is not None:
        el.text = text
    return el


def render_atom(feed: Feed, links: Links) -> bytes:
    a = f"{{{ATOM_NS}}}"
    root = etree.Element(f"{a}feed", nsmap={None: ATOM_NS})
    _sub(root, f"{a}id", FEED_ID)
    _sub(root, f"{a}title", feed.config.site_title)
    _sub(root, f"{a}subtitle", feed.config.site_description)
    _sub(root, f"{a}updated", _rfc3339(feed.updated))
    _sub(
        root, f"{a}link", rel="self", type="application/atom+xml", href=f"{links.feed_base}feed.xml"
    )
    _sub(root, f"{a}link", rel="alternate", type="text/html", href=f"{links.feed_base}index.html")
    author = _sub(root, f"{a}author")
    _sub(author, f"{a}name", "LaunchTrace")
    _sub(root, f"{a}rights", ATTRIBUTION)
    for week in feed.weeks:
        page = f"{links.feed_base}{week.slug}.html"
        for e in week.entries:
            entry = _sub(root, f"{a}entry")
            _sub(entry, f"{a}id", e.id)
            _sub(entry, f"{a}title", _entry_title(e))
            _sub(entry, f"{a}updated", _rfc3339(week.publication_date))
            _sub(entry, f"{a}published", _rfc3339(week.publication_date))
            _sub(
                entry,
                f"{a}link",
                rel="alternate",
                type="text/html",
                href=f"{page}#c-{e.company_number}",
            )
            _sub(
                entry,
                f"{a}link",
                rel="related",
                href=e.companies_house_url,
                title="Companies House",
            )
            _sub(entry, f"{a}link", rel="via", href=e.ukipo_url, title="UKIPO record")
            if e.product_category:
                _sub(entry, f"{a}category", term=e.product_category)
            _sub(entry, f"{a}summary", _entry_summary(e), type="text")
    return etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)


def render_rss(feed: Feed, links: Links) -> bytes:
    root = etree.Element("rss", version="2.0")
    channel = _sub(root, "channel")
    _sub(channel, "title", feed.config.site_title)
    _sub(channel, "link", f"{links.feed_base}index.html")
    _sub(channel, "description", feed.config.site_description)
    _sub(channel, "language", "en-gb")
    _sub(channel, "copyright", ATTRIBUTION)
    if feed.updated:
        _sub(channel, "lastBuildDate", _rfc822(feed.updated))
    for week in feed.weeks:
        page = f"{links.feed_base}{week.slug}.html"
        for e in week.entries:
            item = _sub(channel, "item")
            _sub(item, "title", _entry_title(e))
            _sub(item, "link", f"{page}#c-{e.company_number}")
            _sub(item, "guid", e.id, isPermaLink="false")
            _sub(item, "pubDate", _rfc822(week.publication_date))
            if e.product_category:
                _sub(item, "category", e.product_category)
            _sub(item, "description", _entry_summary(e))
    return etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)


# ---------------------------------------------------------------------------
# the whole site
# ---------------------------------------------------------------------------


def render_site(feed: Feed, links: Links) -> dict[str, bytes]:
    """Every published file, by relative path, in a fixed order."""
    files: dict[str, bytes] = {
        "index.html": render_index_html(feed, links).encode("utf-8"),
        "feed.xml": render_atom(feed, links),
        "rss.xml": render_rss(feed, links),
        "feed.json": render_feed_json(feed, links).encode("utf-8"),
    }
    for week in feed.weeks:
        files[f"{week.slug}.html"] = render_week_html(feed, week, links).encode("utf-8")
        files[f"{week.slug}.json"] = render_week_json(feed, week, links).encode("utf-8")
    return files


def write_site(files: dict[str, bytes], out: Path, *, prune: bool = True) -> list[Path]:
    """Write the files. Stale feed files are removed only from a directory we made.

    A directory is ours if it is new, empty, or carries the marker file; that
    way ``--out .`` by mistake cannot delete anything that is not ours.
    """
    out.mkdir(parents=True, exist_ok=True)
    marker = out / MARKER_FILE
    ours = marker.exists() or not any(out.iterdir())
    if prune and ours:
        for path in sorted(out.iterdir()):
            if (
                path.is_file()
                and path.suffix in {".html", ".json", ".xml"}
                and path.name not in files
            ):
                path.unlink()
    written: list[Path] = []
    for name in sorted(files):
        path = out / name
        path.write_bytes(files[name])
        written.append(path)
    if ours:
        marker.write_text(
            "Generated by python -m src.pipeline build-feed. Files here may be replaced.\n",
            encoding="utf-8",
        )
    return written
