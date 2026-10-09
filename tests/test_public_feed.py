"""The public weekly feed (src/feed): what may be published, and how.

The fixture database deliberately holds every kind of lead the feed must not
show, each with a distinctive string, and the tests assert those strings appear
in no output file and no HTTP response.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from src.db.tables import (
    Brand,
    CompanyMatchRow,
    Journal,
    OpportunityRow,
    ProspectSuppression,
    SuppressionRule,
    TrademarkRecordRow,
)
from src.feed.build import (
    Candidate,
    FeedConfig,
    Suppressions,
    build_feed,
    load_feed_config,
    public_reason,
    public_region,
    publishable,
    reason_key,
)
from src.feed.render import render_site, static_links, write_site

OLD_WEEK = "2025-049"  # public with delay_weeks=1
NEW_WEEK = "2025-050"  # the newest journal: held back
OLD_DATE = date(2025, 12, 5)
NEW_DATE = date(2025, 12, 12)

# Strings that must never appear anywhere in the public output.
PERSON_NAME = "Philomena Quarrington-Vasquez"
POST_TOWN = "Zedbury Magna"
POSTCODE = "QX9 7ZZ"
POSTCODE_AREA = "QX9"
CONTACT_PAGE = "https://crumbledge.test/contact"
WEBSITE = "https://crumbledge.test"
FORBIDDEN = [
    PERSON_NAME,
    "Quarrington",
    POST_TOWN,
    "Zedbury",
    POSTCODE,
    CONTACT_PAGE,
    "crumbledge.test",
    "UNMATCHO",
    "WEAKMATCHO",
    "SUPPRESSO",
    "RULEDOUT",
    "MARKBLOCK",
    "OPTEDOUTO",
    "LOWBANDO",
    "REJECTO",
    "NEWWEEKBRAND",
    "Crumbledge Filing Name Ltd",  # the applicant as filed; only company_name is shown
]

_SEQ = {"n": 0}


def _add_lead(session: Any, **kw: Any) -> OpportunityRow:
    """One stored lead with its company match row, in the shape a run writes."""
    _SEQ["n"] += 1
    n = _SEQ["n"]
    journal = kw.pop("journal", OLD_WEEK)
    published = kw.pop("published", OLD_DATE if journal == OLD_WEEK else NEW_DATE)
    matched = kw.pop("matched", True)
    post_town = kw.pop("post_town", POST_TOWN)
    brand_uid = kw.pop("brand_uid", None)
    dedupe = f"dk{n:04d}"
    tm = kw.pop("trademark_number", f"UK0000390{n:04d}")
    base: dict[str, Any] = {
        "dedupe_key": dedupe,
        "run_id": f"run-{journal}",
        "journal_number": journal,
        "trademark_number": tm,
        "brand_name": f"BRAND{n}",
        "filing_date": date(2025, 9, 15),
        "publication_date": published,
        "goods_summary": "Snack bars",
        "product_category": "cereal_bars",
        "applicant_name": f"Applicant {n} Ltd",
        "applicant_type": "corporate",
        "company_name": f"COMPANY {n} LTD",
        "company_number": f"1400{n:04d}",
        "company_region": "SOUTH WEST",
        "website": WEBSITE,
        "contact_page": CONTACT_PAGE,
        "launch_stage": "pre_launch",
        "launchtrace_score": 70,
        "score_band": "MEDIUM",
        "score_reasons": [
            f"Website looks early-stage ({WEBSITE})",
            "UK company incorporated 6 months before this filing",
        ],
        "source_url": f"file:///home/runner/{POST_TOWN}/journal.xml",
        "review_state": "pending",
        "suppressed": False,
    }
    base.update(kw)
    if brand_uid:
        brand = Brand(
            brand_uid=brand_uid,
            brand_key=f"ch:{base['company_number']}",
            brand_name=base["brand_name"],
            first_seen_journal=journal,
            last_seen_journal=journal,
        )
        session.add(brand)
        session.flush()
        base["brand_id"] = brand.id
    row = OpportunityRow(**base)
    session.add(row)
    session.add(
        CompanyMatchRow(
            dedupe_key=dedupe,
            applicant_name=base["applicant_name"],
            matched=matched,
            company_name=base["company_name"],
            company_number=base["company_number"],
            region=base["company_region"],
            post_town=post_town,
        )
    )
    session.add(
        TrademarkRecordRow(
            journal_number=journal,
            dedupe_key=dedupe,
            trademark_number=tm,
            mark_text=base["brand_name"],
            applicant_name=base["applicant_name"],
            applicant_postcode_area=POSTCODE_AREA,
            applicant_region=POSTCODE,
        )
    )
    session.flush()
    return row


def populate(session: Any) -> None:
    session.add(Journal(journal_number=OLD_WEEK, publication_date=OLD_DATE))
    session.add(Journal(journal_number=NEW_WEEK, publication_date=NEW_DATE))
    # Included: corporate, confirmed match. Highest score in the week.
    _add_lead(
        session,
        brand_name="CRUMBLEDGE",
        applicant_name="Crumbledge Filing Name Ltd",
        company_name="CRUMBLEDGE FOODS LTD",
        company_number="14000001",
        launchtrace_score=90,
        score_band="HIGH",
        brand_uid="b_00000000000000aa",
    )
    # Included: other corporates, for ordering and top-N.
    for i, score in enumerate([80, 75, 72, 71, 60, 55]):
        _add_lead(session, brand_name=f"GOODBRAND{i}", launchtrace_score=score)
    # Same company, second mark, lower score: one entry per company.
    _add_lead(
        session,
        brand_name="CRUMBLEDGE MINI",
        company_name="CRUMBLEDGE FOODS LTD",
        company_number="14000001",
        launchtrace_score=50,
    )
    # Excluded: an individual applicant, even with a company number on the row.
    _add_lead(
        session,
        brand_name="PHILOSNACK",
        applicant_name=PERSON_NAME,
        applicant_type="natural_person",
        company_name=f"{PERSON_NAME.upper()} LTD",
        launchtrace_score=99,
        score_band="HIGH",
        score_reasons=[f"First trade mark we have seen from {PERSON_NAME}"],
    )
    _add_lead(
        session,
        brand_name="PERSONNOCO",
        applicant_name=PERSON_NAME,
        applicant_type="natural_person",
        company_name=None,
        company_number=None,
        launchtrace_score=98,
    )
    # Excluded: corporate but no company match.
    _add_lead(session, brand_name="UNMATCHO", company_number=None, company_name=None)
    # Excluded: a company number on the lead, but the match is not confident.
    _add_lead(session, brand_name="WEAKMATCHO", matched=False, launchtrace_score=97)
    # Excluded: suppressed / rejected / low band.
    _add_lead(session, brand_name="SUPPRESSO", suppressed=True, launchtrace_score=96)
    _add_lead(session, brand_name="REJECTO", review_state="rejected", launchtrace_score=96)
    _add_lead(session, brand_name="LOWBANDO", score_band="SUPPRESS", launchtrace_score=95)
    # Excluded by suppression rules (company and mark) and a company opt-out.
    _add_lead(
        session, brand_name="RULEDOUT", company_name="RULED OUT FOODS LTD", launchtrace_score=94
    )
    session.add(SuppressionRule(rule_type="company", value="Ruled Out Foods Ltd", active=True))
    _add_lead(session, brand_name="MarkBlock", launchtrace_score=93)
    session.add(SuppressionRule(rule_type="mark", value="markblock", active=True))
    _add_lead(
        session, brand_name="OPTEDOUTO", company_name="OPTED OUT LIMITED", launchtrace_score=92
    )
    session.add(
        ProspectSuppression(kind="company", value="opted out", company_name="Opted Out Ltd")
    )
    # Excluded by the delay: the newest week.
    _add_lead(session, journal=NEW_WEEK, brand_name="NEWWEEKBRAND", launchtrace_score=99)
    session.flush()


@pytest.fixture
def feed_db(monkeypatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    """A migrated database file holding the fixture leads; settings point at it."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'feed.sqlite'}")
    monkeypatch.setenv("SITE_URL", "https://launchtrace.test")
    from src.db import init_db, session_scope
    from src.settings import get_settings

    get_settings.cache_clear()
    init_db()
    with session_scope() as session:
        populate(session)
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def session(feed_db):  # type: ignore[no-untyped-def]
    from src.db import get_session

    s = get_session()
    yield s
    s.close()


def _config(**overrides: Any) -> FeedConfig:
    raw = dict(json.loads(Path("config/public_feed.json").read_text(encoding="utf-8")))
    raw.update(overrides)
    return FeedConfig.from_dict(raw)


def _all_text(files: dict[str, bytes]) -> str:
    return "\n".join(v.decode("utf-8") for v in files.values())


def _brands(feed: Any) -> list[str]:
    return [e.brand_name for w in feed.weeks for e in w.entries]


# ---------------------------------------------------------------------------
# what is included
# ---------------------------------------------------------------------------


class TestSelection:
    def test_only_the_delayed_week_is_public(self, session):
        feed = build_feed(session, _config())
        assert [w.journal_number for w in feed.weeks] == [OLD_WEEK]
        assert feed.pending_weeks == 1
        assert feed.updated == OLD_DATE

    def test_delay_zero_publishes_the_newest_week(self, session):
        feed = build_feed(session, _config(delay_weeks=0))
        assert [w.journal_number for w in feed.weeks] == [NEW_WEEK, OLD_WEEK]
        assert "NEWWEEKBRAND" in _brands(feed)

    def test_a_long_delay_publishes_nothing(self, session):
        assert build_feed(session, _config(delay_weeks=2)).weeks == ()

    def test_corporate_matched_lead_is_first(self, session):
        week = build_feed(session, _config()).weeks[0]
        first = week.entries[0]
        assert first.brand_name == "CRUMBLEDGE"
        assert first.company_name == "CRUMBLEDGE FOODS LTD"
        assert first.company_number == "14000001"
        assert first.product_category == "Cereal, protein and energy bars"
        assert first.launch_stage == "Pre-launch"
        assert first.filing_date == "2025-09-15"
        assert first.region == "South West"
        assert first.id == f"urn:launchtrace:feed:{OLD_WEEK}:b_00000000000000aa"
        assert first.companies_house_url.endswith("/company/14000001")
        assert first.ukipo_url.startswith("https://www.ipo.gov.uk/")
        assert first.ukipo_url.endswith(first.trademark_number)

    def test_top_n_and_one_entry_per_company(self, session):
        week = build_feed(session, _config()).weeks[0]
        assert len(week.entries) == 5
        assert week.qualifying_count == 7  # CRUMBLEDGE (once) + six GOODBRANDs
        assert _brands(build_feed(session, _config()))[:3] == [
            "CRUMBLEDGE",
            "GOODBRAND0",
            "GOODBRAND1",
        ]
        assert "CRUMBLEDGE MINI" not in _brands(build_feed(session, _config(top_n_per_week=50)))
        assert len(build_feed(session, _config(top_n_per_week=2)).weeks[0].entries) == 2

    def test_every_excluded_kind_is_absent(self, session):
        brands = _brands(build_feed(session, _config(top_n_per_week=100)))
        for name in (
            "PHILOSNACK",
            "PERSONNOCO",
            "UNMATCHO",
            "WEAKMATCHO",
            "SUPPRESSO",
            "REJECTO",
            "LOWBANDO",
            "RULEDOUT",
            "MarkBlock",
            "OPTEDOUTO",
            "NEWWEEKBRAND",
        ):
            assert name not in brands

    def test_exclusions_are_counted_for_the_operator(self, session):
        ex = build_feed(session, _config()).exclusions
        assert ex["not_corporate"] == 2
        assert ex["no_company"] == 1
        assert ex["company_not_confirmed"] == 1
        assert ex["suppressed"] == 2
        assert ex["band"] == 1
        assert ex["suppression_rule"] == 3
        assert ex["delay"] == 1

    def test_band_rules(self, session):
        feed = build_feed(session, _config(min_band="HIGH", bands_allowed=["HIGH"]))
        assert _brands(feed) == ["CRUMBLEDGE"]
        # SUPPRESS is never published, whatever the config says.
        cfg = _config(min_band="SUPPRESS", bands_allowed=["HIGH", "MEDIUM", "SUPPRESS"])
        assert "LOWBANDO" not in _brands(build_feed(session, cfg))

    def test_inactive_suppression_rule_does_not_apply(self, session):
        from sqlalchemy import update

        session.execute(update(SuppressionRule).values(active=False))
        session.flush()
        brands = _brands(build_feed(session, _config(top_n_per_week=100)))
        assert "RULEDOUT" in brands and "MarkBlock" in brands
        assert "OPTEDOUTO" not in brands  # the opt-out is a separate list

    def test_shipped_config_loads(self):
        cfg = load_feed_config()
        assert cfg.top_n_per_week == 5 and cfg.delay_weeks == 1 and cfg.min_band == "MEDIUM"


# ---------------------------------------------------------------------------
# the single filter and the field sanitisers
# ---------------------------------------------------------------------------


def _candidate(**kw: Any) -> Candidate:
    base: dict[str, Any] = {
        "journal_number": OLD_WEEK,
        "publication_date": OLD_DATE,
        "trademark_number": "UK00003900001",
        "brand_name": "CRUMBLEDGE",
        "applicant_name": "Crumbledge Foods Ltd",
        "applicant_type": "corporate",
        "company_name": "CRUMBLEDGE FOODS LTD",
        "company_number": "14000001",
        "match_confirmed": True,
        "post_town": None,
        "region": "SOUTH WEST",
        "product_category": "snacks",
        "launch_stage": "pre_launch",
        "filing_date": None,
        "score": 80,
        "band": "HIGH",
        "reasons": [],
        "review_state": "pending",
        "suppressed": False,
        "source_url": None,
        "brand_uid": None,
    }
    base.update(kw)
    return Candidate(**base)


class TestPublishable:
    cfg = _config()

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({}, None),
            ({"applicant_type": "natural_person"}, "not_corporate"),
            ({"applicant_type": "unknown"}, "not_corporate"),
            ({"company_number": None}, "no_company"),
            ({"company_number": "  "}, "no_company"),
            ({"company_name": None}, "no_company"),
            ({"match_confirmed": False}, "company_not_confirmed"),
            ({"suppressed": True}, "suppressed"),
            ({"review_state": "suppressed"}, "suppressed"),
            ({"review_state": "rejected"}, "suppressed"),
            ({"band": "SUPPRESS"}, "band"),
            ({"band": ""}, "band"),
            ({"publication_date": None}, "no_publication_date"),
            ({"brand_name": ""}, "no_brand_name"),
        ],
    )
    def test_rules(self, change, reason):
        assert publishable(_candidate(**change), self.cfg, Suppressions()) == reason

    @pytest.mark.parametrize(
        "sup",
        [
            Suppressions(company=frozenset({"crumbledge foods ltd"})),
            Suppressions(company=frozenset({"14000001"})),
            Suppressions(applicant=frozenset({"crumbledge foods ltd"})),
            Suppressions(mark=frozenset({"crumbledge"})),
            Suppressions(mark=frozenset({"uk00003900001"})),
            Suppressions(company_normalised=frozenset({"crumbledge foods"})),
        ],
    )
    def test_suppressions(self, sup):
        assert publishable(_candidate(), self.cfg, sup) == "suppression_rule"


class TestSanitisers:
    @pytest.mark.parametrize(
        ("region", "town", "expected"),
        [
            ("SOUTH WEST", None, "South West"),
            ("Greater London", "London", "Greater London"),
            ("BRISTOL", "Bristol", None),  # a town, not a region
            ("QX9 7ZZ", None, None),
            ("QX9", None, None),
            ("Area 51", None, None),
            ("", None, None),
            (None, None, None),
        ],
    )
    def test_region(self, region, town, expected):
        assert public_region(region, town) == expected

    def test_reason_keys(self):
        assert reason_key("UK company incorporated 6 months before this filing") == (
            "company_incorporated_within_12m"
        )
        assert reason_key("Website looks early-stage (https://x.test)") == "early_stage_website"
        assert reason_key("Something we never wrote") is None

    def test_reason_skips_urls_names_and_unlisted_keys(self):
        cfg = _config()
        c = _candidate(
            applicant_name="Zed Foods Ltd",
            reasons=[
                "Website looks early-stage (https://zed.test)",
                "First trade mark we have seen from this applicant",
                "Specific packaged food category: Zed Foods Ltd snacks",
                "Specific packaged food category: Snacks",
            ],
        )
        assert public_reason(c, cfg) == "Specific packaged food category: Snacks"

    def test_reason_fallback(self):
        cfg = _config()
        c = _candidate(reasons=["Evidence of active launch preparation (www.x.test)"])
        assert public_reason(c, cfg) == cfg.fallback_reason

    def test_local_source_urls_are_never_published(self, session):
        week = build_feed(session, _config()).weeks[0]
        for e in week.entries:
            assert e.ukipo_url.startswith("https://www.ipo.gov.uk/tmcase/Results/1/")


# ---------------------------------------------------------------------------
# rendered output
# ---------------------------------------------------------------------------


def _site(session) -> dict[str, bytes]:  # type: ignore[no-untyped-def]
    feed = build_feed(session, _config(delay_weeks=0, top_n_per_week=100))
    return render_site(feed, static_links(feed, "https://launchtrace.test"))


class TestRenderedOutput:
    def test_forbidden_strings_appear_nowhere(self, session):
        # Even with every week public and no top-N limit.
        text = _all_text(_site(session))
        for needle in [n for n in FORBIDDEN if n != "NEWWEEKBRAND"]:
            assert needle.lower() not in text.lower(), needle

    def test_files(self, session):
        names = set(_site(session))
        assert {"index.html", "feed.xml", "rss.xml", "feed.json"} <= names
        assert {f"{OLD_WEEK}.html", f"{OLD_WEEK}.json", f"{NEW_WEEK}.html"} <= names

    def test_atom_is_valid(self, session):
        root = etree.fromstring(_site(session)["feed.xml"])
        ns = {"a": "http://www.w3.org/2005/Atom"}
        assert root.tag == "{http://www.w3.org/2005/Atom}feed"
        for tag in ("id", "title", "updated", "author/a:name"):
            assert root.find(f"a:{tag}", ns) is not None, tag
        assert root.findtext("a:updated", namespaces=ns) == "2025-12-12T00:00:00Z"
        self_link = root.find("a:link[@rel='self']", ns)
        assert self_link is not None
        assert self_link.get("href") == "https://launchtrace.test/feed/feed.xml"
        entries = root.findall("a:entry", ns)
        assert entries
        ids = [e.findtext("a:id", namespaces=ns) for e in entries]
        assert len(ids) == len(set(ids))
        for entry in entries:
            assert entry.findtext("a:title", namespaces=ns)
            assert entry.findtext("a:updated", namespaces=ns)
            alt = entry.find("a:link[@rel='alternate']", ns)
            assert alt is not None and alt.get("href", "").startswith("https://")

    def test_rss_parses(self, session):
        root = etree.fromstring(_site(session)["rss.xml"])
        assert root.tag == "rss" and root.get("version") == "2.0"
        items = root.findall("channel/item")
        assert items and all(i.findtext("guid") for i in items)

    def test_json_schema(self, session):
        files = _site(session)
        top = json.loads(files["feed.json"])
        assert set(top) == {
            "version",
            "title",
            "description",
            "updated",
            "delay_weeks",
            "top_n_per_week",
            "attribution",
            "licence_url",
            "home_page_url",
            "weeks",
        }
        week = top["weeks"][-1]
        assert set(week) == {
            "journal_number",
            "publication_date",
            "url",
            "json_url",
            "qualifying_count",
            "entries",
        }
        entry_keys = {
            "id",
            "brand_name",
            "company_name",
            "company_number",
            "product_category",
            "launch_stage",
            "filing_date",
            "region",
            "reason",
            "trademark_number",
            "ukipo_url",
            "companies_house_url",
        }
        for w in top["weeks"]:
            for e in w["entries"]:
                assert set(e) == entry_keys
        per_week = json.loads(files[f"{OLD_WEEK}.json"])
        assert per_week["journal_number"] == OLD_WEEK
        assert per_week["entries"] == week["entries"]

    def test_footer_and_cta_are_absolute_in_the_static_build(self, session):
        html = _site(session)["index.html"].decode()
        for path in ("/privacy", "/unsubscribe", "/attribution"):
            assert f'href="https://launchtrace.test{path}"' in html
        assert 'href="https://launchtrace.test/#sample"' in html
        assert "Open Government Licence" in html
        assert "<style>" in html and "stylesheet" not in html

    def test_internal_links_are_relative(self, session):
        html = _site(session)[f"{OLD_WEEK}.html"].decode()
        assert 'href="index.html"' in html and f'href="{OLD_WEEK}.json"' in html
        assert "csv" not in html.lower()

    def test_build_is_deterministic(self, session):
        assert _site(session) == _site(session)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def _run_cli(out: Path, journal: str | None = None) -> int:
    from src.feed.command import cmd_build_feed

    return cmd_build_feed(argparse.Namespace(out=str(out), journal=journal))


def _snapshot(out: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(out.iterdir()) if p.is_file()}


class TestCommand:
    def test_static_build_is_byte_identical(self, feed_db):
        a, b = feed_db / "a", feed_db / "b"
        assert _run_cli(a) == 0
        assert _run_cli(b) == 0
        assert _run_cli(b) == 0
        assert _snapshot(a) == _snapshot(b)
        names = set(_snapshot(a))
        assert {"index.html", "feed.xml", "rss.xml", "feed.json", f"{OLD_WEEK}.html"} <= names
        assert f"{NEW_WEEK}.html" not in names  # delayed by the shipped config
        text = "\n".join(v.decode("utf-8", "replace") for v in _snapshot(a).values())
        for needle in FORBIDDEN:
            assert needle.lower() not in text.lower(), needle

    def test_journal_option(self, feed_db, capsys):
        out = feed_db / "one"
        assert _run_cli(out, OLD_WEEK) == 0
        assert sorted(p.name for p in out.iterdir() if not p.name.startswith(".")) == [
            f"{OLD_WEEK}.html",
            f"{OLD_WEEK}.json",
        ]
        assert _run_cli(out, NEW_WEEK) == 0
        assert "not public" in capsys.readouterr().out

    def test_stale_files_are_pruned_only_in_our_directory(self, feed_db):
        out = feed_db / "site"
        _run_cli(out)
        (out / "2001-001.html").write_text("old", encoding="utf-8")
        _run_cli(out)
        assert not (out / "2001-001.html").exists()
        foreign = feed_db / "foreign"
        foreign.mkdir()
        (foreign / "keep.html").write_text("mine", encoding="utf-8")
        write_site({"index.html": b"x"}, foreign)
        assert (foreign / "keep.html").exists()

    def test_cli_entry_point(self, feed_db):
        from src.pipeline import main

        assert main(["build-feed", "--out", str(feed_db / "cli")]) == 0
        assert (feed_db / "cli" / "index.html").exists()


def test_empty_database_builds_an_index(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.sqlite'}")
    monkeypatch.setenv("SITE_URL", "https://launchtrace.test")
    from src.settings import get_settings

    get_settings.cache_clear()
    try:
        assert _run_cli(tmp_path / "out") == 0
    finally:
        get_settings.cache_clear()
    out = tmp_path / "out"
    assert "No weeks published yet" in (out / "index.html").read_text(encoding="utf-8")
    root = etree.fromstring((out / "feed.xml").read_bytes())
    assert root.findtext("{http://www.w3.org/2005/Atom}updated") == "1970-01-01T00:00:00Z"
    assert json.loads((out / "feed.json").read_text(encoding="utf-8"))["weeks"] == []


# ---------------------------------------------------------------------------
# the website
# ---------------------------------------------------------------------------


@pytest.fixture
def client(feed_db, monkeypatch):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from src.web.app import create_app

    return TestClient(create_app())


class TestWebRoutes:
    def test_routes_return_200_with_headers(self, client):
        for path, kind in [
            ("/feed/", "text/html"),
            ("/feed/index.html", "text/html"),
            (f"/feed/{OLD_WEEK}.html", "text/html"),
            (f"/feed/{OLD_WEEK}", "text/html"),
            ("/feed.xml", "application/atom+xml"),
            ("/feed/feed.xml", "application/atom+xml"),
            ("/feed/rss.xml", "application/rss+xml"),
            ("/feed.json", "application/json"),
            ("/feed/feed.json", "application/json"),
            (f"/feed/{OLD_WEEK}.json", "application/json"),
            ("/feed/feed.css", "text/css"),
        ]:
            r = client.get(path)
            assert r.status_code == 200, path
            assert r.headers["content-type"].startswith(kind), path
            assert "Content-Security-Policy" in r.headers
            assert r.headers["X-Frame-Options"] == "DENY"

    def test_delayed_and_unknown_weeks_404(self, client):
        assert client.get(f"/feed/{NEW_WEEK}.html").status_code == 404
        assert client.get("/feed/nope.json").status_code == 404

    def test_bare_feed_redirects(self, client):
        r = client.get("/feed", follow_redirects=False)
        assert r.status_code in (307, 308) and r.headers["location"] == "/feed/"

    def test_same_entries_as_static_build(self, client, feed_db):
        _run_cli(feed_db / "static")
        static = json.loads((feed_db / "static" / f"{OLD_WEEK}.json").read_text(encoding="utf-8"))
        served = client.get(f"/feed/{OLD_WEEK}.json").json()
        assert served["entries"] == static["entries"]
        assert client.get("/feed.xml").content == (feed_db / "static" / "feed.xml").read_bytes()

    def test_pages_link_relative_footer_and_no_inline_style(self, client):
        html = client.get(f"/feed/{OLD_WEEK}.html").text
        for path in ("/privacy", "/unsubscribe", "/attribution"):
            assert f'href="{path}"' in html
        assert 'href="/#sample"' in html
        assert "<style>" not in html  # the CSP forbids inline styles
        assert 'href="feed.css"' in html

    def test_no_forbidden_string_in_any_response(self, client):
        paths = ["/feed/", f"/feed/{OLD_WEEK}.html", "/feed.xml", "/feed/rss.xml", "/feed.json"]
        paths.append(f"/feed/{OLD_WEEK}.json")
        text = "\n".join(client.get(p).text for p in paths)
        for needle in FORBIDDEN:
            assert needle.lower() not in text.lower(), needle


# ---------------------------------------------------------------------------
# end to end: a database written by the real pipeline on the fixture journal
# ---------------------------------------------------------------------------


def test_build_feed_on_a_pipeline_database(monkeypatch, tmp_path, pipeline):
    """The fixture journal through the real pipeline, persisted as a weekly run does."""
    from sqlalchemy import select

    from src.brands import sync_brands
    from src.db import init_db, session_scope
    from src.db.repository import save_opportunities, save_trademark_records, upsert_journal
    from src.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'e2e.sqlite'}")
    monkeypatch.setenv("SITE_URL", "https://launchtrace.test")
    get_settings.cache_clear()
    try:
        init_db()
        result = pipeline.run(write_outputs=False)
        assert result.opportunities
        with session_scope() as session:
            artifact = pipeline.last_artifact
            assert artifact is not None
            journal_row = upsert_journal(session, artifact, result.counts.raw_records)
            save_trademark_records(session, pipeline.last_records, journal_row.id)
            save_opportunities(session, result)
            sync_brands(session, result)
        with session_scope() as session:
            people = {
                r.applicant_name
                for r in session.execute(select(OpportunityRow)).scalars()
                if r.applicant_type != "corporate" and r.applicant_name
            }
            feed = build_feed(session, _config(delay_weeks=0))
            # The shipped one-week delay holds back the only week there is.
            assert build_feed(session, _config()).weeks == ()
        assert feed.weeks, feed.exclusions
        entries = feed.weeks[0].entries
        assert entries
        files = render_site(feed, static_links(feed, "https://launchtrace.test"))
        write_site(files, tmp_path / "public")
        text = _all_text(files).lower()
        for entry in entries:
            assert entry.company_number and entry.company_name.lower() in text
        for name in people:
            assert name.lower() not in text
        assert "file://" not in text
    finally:
        get_settings.cache_clear()
