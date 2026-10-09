"""The "brands that moved" digest (src/rescan/digest.py) and the rescan CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from src.brands import record_stage_change
from src.db.tables import Brand, Customer, CustomerPreference, Delivery, Journal
from src.deliver.resend_client import EmailSender
from src.rescan.digest import (
    KIND,
    idempotency_key,
    iso_week,
    meaningful_changes,
    movers_from,
    render_movers_digest,
    send_movers_digests,
)
from src.settings import get_settings
from tests.test_rescan import NOW, RECENT_JOURNAL, _journals, ch_record, make_brand

BUYER = "buyer@pouchworks.test"


def moved(
    session,  # type: ignore[no-untyped-def]
    brand: Brand,
    old: str = "holding_page",
    new: str = "live_store",
    *,
    dimension: str = "web_presence",
    meaningful: bool = True,
    negative: bool = False,
    launched: bool | None = None,
    detected_at: datetime | None = None,
) -> None:
    prefix = "web" if dimension == "web_presence" else "company"
    row = record_stage_change(
        session,
        brand.id,
        f"{prefix}:{old}",
        f"{prefix}:{new}",
        {
            "detected_by": "rescan",
            "dimension": dimension,
            "old_value": old,
            "new_value": new,
            "meaningful": meaningful,
            "negative": negative,
            "launched": (new == "live_store") if launched is None else launched,
        },
        run_id="rescan_test",
    )
    if detected_at is not None:
        row.detected_at = detected_at
    session.flush()


def customer(
    session,  # type: ignore[no-untyped-def]
    email: str = BUYER,
    *,
    status: str = "active",
    enabled: bool = True,
    min_band: str = "MEDIUM",
    regions: list[str] | None = None,
    categories: list[str] | None = None,
    past_due_since: datetime | None = None,
) -> Customer:
    c = Customer(
        company="Pouchworks Ltd",
        subscription_status=status,
        delivery_enabled=enabled,
        past_due_since=past_due_since,
    )
    session.add(c)
    session.flush()
    session.add(
        CustomerPreference(
            customer_id=c.id,
            recipient_email=email,
            min_score_band=min_band,
            regions=regions or [],
            product_categories=categories or [],
        )
    )
    session.flush()
    return c


def confirmed_brand(session, name: str = "CRUMBLEDGE", **kw) -> Brand:  # type: ignore[no-untyped-def]
    kw.setdefault("company_number", "14000001")
    kw.setdefault("company_name", "CRUMBLEDGE FOODS LTD")
    kw.setdefault("website", "https://crumbledge.co.uk")
    return make_brand(session, name, **kw)


@pytest.fixture
def outbox(tmp_path: Path) -> Path:
    return tmp_path / "outbox"


@pytest.fixture
def sender(settings, outbox) -> EmailSender:  # type: ignore[no-untyped-def]
    return EmailSender(settings, outbox=outbox)


def html_files(outbox: Path) -> list[Path]:
    return sorted(outbox.glob("*.html")) if outbox.exists() else []


class TestSelection:
    def test_only_meaningful_rescan_changes_since(self, db_session):
        brand = confirmed_brand(db_session)
        moved(db_session, brand)
        moved(db_session, brand, "no_dns", "holding_page", meaningful=False)
        moved(db_session, brand, detected_at=NOW - timedelta(days=30))
        record_stage_change(
            db_session, brand.id, "pre_launch", "early_launch", {"detected_by": "weekly"}
        )
        rows = meaningful_changes(db_session, NOW - timedelta(days=7))
        assert len(rows) == 1

    def test_individuals_and_unconfirmed_brands_are_left_out(self, db_session):
        confirmed = confirmed_brand(db_session)
        person = make_brand(
            db_session, "PERSONAL", website="https://p.co.uk", applicant_type="natural_person"
        )
        unmatched = make_brand(db_session, "UNMATCHED", website="https://u.co.uk")
        for brand in (confirmed, person, unmatched):
            moved(db_session, brand)
        movers = movers_from(meaningful_changes(db_session, NOW - timedelta(days=7)))
        assert [m.brand_name for m in movers] == ["CRUMBLEDGE"]
        assert movers[0].party == "CRUMBLEDGE FOODS LTD"

    def test_several_moves_collapse_to_first_from_last_to(self, db_session):
        brand = confirmed_brand(db_session)
        moved(db_session, brand, "no_domain", "holding_page", detected_at=NOW - timedelta(days=3))
        moved(db_session, brand, "holding_page", "live_store", detected_at=NOW - timedelta(days=1))
        (mover,) = movers_from(meaningful_changes(db_session, NOW - timedelta(days=7)))
        assert len(mover.moves) == 1
        assert (mover.moves[0].from_label, mover.moves[0].to_label) == (
            "no website",
            "online shop live",
        )
        assert mover.launched


class TestSending:
    def test_eligible_customer_gets_an_outbox_file(self, db_session, settings, sender, outbox):
        brand = confirmed_brand(db_session)
        moved(db_session, brand)
        customer(db_session)
        summary = send_movers_digests(db_session, settings, sender=sender)
        assert summary.rendered_not_sent == 1 and summary.sent == 0
        (html,) = html_files(outbox)
        body = html.read_text(encoding="utf-8")
        assert "CRUMBLEDGE" in body and "CRUMBLEDGE FOODS LTD" in body
        assert "online shop live" in body and "Launched" in body
        assert "https://launchtrace.test/unsubscribe" in body
        assert "https://launchtrace.test/billing/manage" in body
        assert "https://launchtrace.test/privacy" in body
        delivery = db_session.execute(select(Delivery)).scalar_one()
        assert delivery.kind == KIND and delivery.status == "rendered_not_sent"
        assert delivery.opportunity_count == 1

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"status": "cancelled"},
            {"enabled": False},
            {"status": "past_due", "past_due_since": NOW - timedelta(days=30)},
            {"status": "incomplete"},
        ],
    )
    def test_ineligible_customer_gets_nothing(self, db_session, settings, sender, outbox, kwargs):
        moved(db_session, confirmed_brand(db_session))
        customer(db_session, **kwargs)
        summary = send_movers_digests(db_session, settings, sender=sender)
        assert summary.recipients == 0
        assert html_files(outbox) == []

    def test_suppressed_address_gets_nothing(self, db_session, settings, sender, outbox):
        from src.db.repository import add_suppression

        moved(db_session, confirmed_brand(db_session))
        customer(db_session)
        add_suppression(db_session, "email", BUYER, "opted out")
        summary = send_movers_digests(db_session, settings, sender=sender)
        assert summary.skipped_suppressed == 1
        assert html_files(outbox) == []

    def test_suppressed_company_is_not_listed(self, db_session, settings, sender, outbox):
        from src.db.repository import add_suppression

        moved(db_session, confirmed_brand(db_session))
        customer(db_session)
        add_suppression(db_session, "company", "CRUMBLEDGE FOODS LTD", "asked")
        summary = send_movers_digests(db_session, settings, sender=sender)
        assert summary.skipped_empty == 1
        assert html_files(outbox) == []

    def test_preferences_filter_band_category_and_region(self, db_session, settings, sender):
        moved(db_session, confirmed_brand(db_session, "LOWBAND", band="SUPPRESS"))
        moved(
            db_session, confirmed_brand(db_session, "WRONGCAT", company_number="1", category="tea")
        )
        moved(
            db_session,
            confirmed_brand(db_session, "WRONGREGION", company_number="2", region="KENT"),
        )
        moved(db_session, confirmed_brand(db_session, "MATCH", company_number="3"))
        customer(db_session, regions=["BRISTOL"], categories=["cereal_bars"])
        summary = send_movers_digests(db_session, settings, sender=sender)
        assert summary.rendered_not_sent == 1
        delivery = db_session.execute(select(Delivery)).scalar_one()
        assert delivery.opportunity_count == 1

    def test_nothing_moved_means_no_email(self, db_session, settings, sender, outbox):
        confirmed_brand(db_session)
        customer(db_session)
        summary = send_movers_digests(db_session, settings, sender=sender)
        assert summary.skipped_empty == 1
        assert html_files(outbox) == []
        assert db_session.execute(select(Delivery)).first() is None

    def test_idempotent_per_week(self, db_session, settings, sender, outbox):
        moved(db_session, confirmed_brand(db_session))
        customer(db_session)
        now = datetime.now(UTC)
        send_movers_digests(db_session, settings, sender=sender, now=now)
        again = send_movers_digests(
            db_session, settings, sender=sender, now=now + timedelta(hours=6)
        )
        assert again.skipped_already_sent == 1
        assert len(html_files(outbox)) == 1

    def test_next_week_covers_only_moves_since_the_last_digest(self, db_session, settings, sender):
        moved(db_session, confirmed_brand(db_session))
        customer(db_session)
        first = send_movers_digests(db_session, settings, sender=sender)
        assert first.rendered_not_sent == 1
        later = send_movers_digests(
            db_session, settings, sender=sender, now=NOW + timedelta(days=7)
        )
        assert later.skipped_empty == 1

    def test_idempotency_key_is_per_customer_week_and_recipient(self):
        week = iso_week(NOW)
        assert idempotency_key(1, week, BUYER) == idempotency_key(1, week, BUYER.upper())
        assert idempotency_key(1, week, BUYER) != idempotency_key(2, week, BUYER)
        assert len(idempotency_key(10**9, "2026-W52", "x" * 300 + "@a.test")) <= 128

    def test_review_mode_never_sends_even_with_a_key(
        self, db_session, settings, outbox, monkeypatch
    ):
        moved(db_session, confirmed_brand(db_session))
        customer(db_session)
        keyed = settings.model_copy(update={"resend_api_key": "re_test", "send_mode": "review"})

        def no_post(self, payload, key):  # type: ignore[no-untyped-def]
            raise AssertionError("review mode must not call Resend")

        monkeypatch.setattr(EmailSender, "_post", no_post)
        summary = send_movers_digests(db_session, keyed, sender=EmailSender(keyed, outbox=outbox))
        assert summary.review_only and summary.sent == 0 and summary.rendered_not_sent == 1
        assert len(html_files(outbox)) == 1

    def test_automatic_mode_still_review_only_until_send_enabled(
        self, db_session, settings, outbox, monkeypatch
    ):
        from src.rescan.changes import rescan_config

        moved(db_session, confirmed_brand(db_session))
        customer(db_session)
        live = settings.model_copy(update={"resend_api_key": "re_test", "send_mode": "automatic"})
        posts: list[dict] = []
        monkeypatch.setattr(
            EmailSender, "_post", lambda self, payload, key: posts.append(payload) or "id-1"
        )

        held = send_movers_digests(db_session, live, sender=EmailSender(live, outbox=outbox))
        assert held.review_only and posts == []

        cfg = json.loads(json.dumps(rescan_config()))
        cfg["digest"]["send_enabled"] = True
        db_session.execute(Delivery.__table__.delete())
        sent = send_movers_digests(
            db_session, live, sender=EmailSender(live, outbox=outbox), config=cfg
        )
        assert sent.sent == 1 and len(posts) == 1
        assert "Unsubscribe" in posts[0]["html"] and "/unsubscribe" in posts[0]["text"]

    def test_negative_moves_have_their_own_section(self, db_session, settings):
        good = confirmed_brand(db_session, "UP")
        gone = confirmed_brand(
            db_session, "GONE", company_number="14000099", company_name="GONE LTD"
        )
        moved(db_session, good)
        moved(db_session, gone, "trading", "dissolved", dimension="company_status", negative=True)
        movers = movers_from(meaningful_changes(db_session, NOW - timedelta(days=7)))
        rendered = render_movers_digest(movers, since=NOW - timedelta(days=7), settings=settings)
        assert "Stopped trading" in rendered.html
        assert rendered.html.index("UP") < rendered.html.index("GONE LTD")
        assert "dissolved" in rendered.text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'cli.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "")
    monkeypatch.setenv("COMPANY_REGISTRY_PROVIDER", "fixture")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("SEND_MODE", "review")
    monkeypatch.setenv("SITE_URL", "https://launchtrace.test")
    monkeypatch.setattr("src.deliver.resend_client.OUTBOX", tmp_path / "outbox")
    pruned: list[bool] = []
    monkeypatch.setattr("src.commands.prune_caches", lambda settings: pruned.append(True) or 0)
    get_settings.cache_clear()
    from src.db import init_db

    init_db(url)
    yield {"url": url, "outbox": tmp_path / "outbox", "pruned": pruned}
    get_settings.cache_clear()


def _seed(url: str) -> None:
    from src.db import session_scope

    with session_scope(url) as session:
        session.add(
            Journal(
                journal_number=RECENT_JOURNAL,
                source_name="fixture",
                publication_date=(NOW - timedelta(weeks=3)).date(),
                processing_status="processed",
            )
        )
        session.flush()
        confirmed_brand(session, company_number="14000001")
        customer(session)


class TestCli:
    @pytest.mark.parametrize(
        "argv",
        [
            ["rescan"],
            ["rescan", "--dry-run", "--limit", "5"],
            ["rescan", "--no-digest", "--json"],
            ["movers-digest"],
        ],
    )
    def test_commands_parse(self, argv):
        from src.pipeline import build_parser

        assert build_parser().parse_args(argv).command == argv[0]

    def test_rescan_runs_job_then_digest_then_prunes(self, cli_env, capsys):
        from src.pipeline import main

        _seed(cli_env["url"])
        assert main(["rescan"]) == 0
        out = capsys.readouterr().out
        assert "selected 1" in out and "Companies House lookups 1" in out
        assert "Movers digest" in out and "review (outbox only)" in out
        assert cli_env["pruned"] == [True]
        # The second Friday attempt finds nothing due.
        assert main(["rescan"]) == 0
        assert "selected 0" in capsys.readouterr().out

    def test_dry_run(self, cli_env, capsys):
        from src.pipeline import main

        _seed(cli_env["url"])
        assert main(["rescan", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "(dry run)" in out and "companies_house" in out
        assert "Movers digest" not in out

    def test_movers_digest_command(self, cli_env, capsys):
        from src.pipeline import main

        _seed(cli_env["url"])
        from src.db import session_scope

        with session_scope(cli_env["url"]) as session:
            brand = session.execute(select(Brand)).scalar_one()
            moved(session, brand)
        assert main(["movers-digest"]) == 0
        assert "written to outbox 1" in capsys.readouterr().out
        assert len(html_files(cli_env["outbox"])) == 1


def test_ch_record_helper_is_shared():
    assert ch_record("1")["company_number"] == "1"
    assert _journals is not None
