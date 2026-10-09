"""No output LaunchTrace produces may contain an individual applicant's name.

One scenario -- a matched company, a natural-person applicant and an untyped
applicant whose name does not look corporate -- is pushed through every
customer-, prospect- and public-facing renderer. Each renderer is registered in
``OUTPUT_RENDERERS``; the test asserts:

* neither individual's name appears (case-insensitive) in any output, and
* the corporate company name appears somewhere, so the check cannot pass by
  rendering nothing.

EXTENSION POINT: a new output (for example the public feed in ``src/feed``,
added on another branch) is covered by adding one function decorated with
``@renderer("name")`` that takes the ``Scenario`` and returns the rendered
text. See ``_feed_renderer`` at the bottom for the placeholder.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from src.db.engine import get_engine
from src.db.repository import save_opportunities, save_run
from src.db.tables import Base, PipelineRun
from src.models import (
    ApplicantType,
    BuyingIntent,
    CompanyMatch,
    JournalRef,
    Opportunity,
    PipelineResult,
    Relevance,
    RunStatus,
    Score,
    ScoreBand,
    ScoreReason,
)
from src.privacy import INDIVIDUAL_WITHHELD, display_party, is_individual_applicant
from src.settings import Settings
from tests.conftest import make_prospect

INDIVIDUAL = "Zebedee Quillfeather"
UNTYPED_INDIVIDUAL = "Ottoline Brackenbury"
FORBIDDEN = (INDIVIDUAL, UNTYPED_INDIVIDUAL, "Quillfeather", "Brackenbury")
CORPORATE_COMPANY = "CRUMBLEDGE FOODS LTD"
JOURNAL = "2025-050"
PUBLISHED = date(2025, 12, 12)

_INTENT = BuyingIntent(
    flexible_packaging=Relevance.HIGH,
    labels=Relevance.HIGH,
    cartons=Relevance.MEDIUM,
    contract_manufacturing=Relevance.HIGH,
    copacking=Relevance.HIGH,
    distribution=Relevance.MEDIUM,
    fulfilment=Relevance.MEDIUM,
    marketing=Relevance.MEDIUM,
)


def _opp(
    key: str,
    number: str,
    brand: str,
    applicant: str,
    applicant_type: ApplicantType,
    score: int,
    band: ScoreBand,
    company: CompanyMatch | None = None,
) -> Opportunity:
    return Opportunity(
        dedupe_key=key,
        trademark_number=number,
        brand_name=brand,
        filing_date=date(2025, 9, 15),
        publication_date=PUBLISHED,
        journal_number=JOURNAL,
        goods_summary="Snack bars; cereal bars; oat bars.",
        product_category="cereal_bars",
        product_category_label="Cereal, protein and energy bars",
        applicant_name=applicant,
        applicant_type=applicant_type,
        nice_classes=[30],
        company=company or CompanyMatch(),
        score=Score(
            value=score,
            band=band,
            reasons=[
                ScoreReason(key="first", text="First trade mark for this applicant", weight=10)
            ],
        ),
        buying_intent=_INTENT,
        source_url=f"https://example.invalid/tm/{number}",
    )


def build_result() -> PipelineResult:
    corporate = _opp(
        "k-corp",
        "UK00003900001",
        "CRUMBLEDGE",
        "Crumbledge Foods Ltd",
        ApplicantType.CORPORATE,
        82,
        ScoreBand.HIGH,
        company=CompanyMatch(
            matched=True,
            company_name=CORPORATE_COMPANY,
            company_number="14000001",
            incorporation_date=date(2025, 3, 1),
            region="BRISTOL",
            match_confidence=98,
        ),
    )
    person = _opp(
        "k-person",
        "UK00003900002",
        "QUILLBERRY CRUNCH",
        INDIVIDUAL,
        ApplicantType.NATURAL_PERSON,
        74,
        ScoreBand.MEDIUM,
    )
    untyped = _opp(
        "k-untyped",
        "UK00003900003",
        "BRACKEN BITES",
        UNTYPED_INDIVIDUAL,
        ApplicantType.UNKNOWN,
        70,
        ScoreBand.MEDIUM,
    )
    return PipelineResult(
        run_id="run-privacy",
        journal=JournalRef(journal_number=JOURNAL, publication_date=PUBLISHED),
        status=RunStatus.COMPLETED,
        started_at=datetime(2025, 12, 12, tzinfo=UTC),
        opportunities=[corporate, person, untyped],
    )


@dataclass
class Scenario:
    result: PipelineResult
    settings: Settings
    database_url: str
    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch
    capsys: pytest.CaptureFixture[str]

    def session(self):  # type: ignore[no-untyped-def]
        return sessionmaker(bind=get_engine(self.database_url), expire_on_commit=False)()


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

Renderer = Callable[[Scenario], str]
OUTPUT_RENDERERS: dict[str, Renderer] = {}


def renderer(name: str) -> Callable[[Renderer], Renderer]:
    def register(fn: Renderer) -> Renderer:
        OUTPUT_RENDERERS[name] = fn
        return fn

    return register


def _leads(s: Scenario):  # type: ignore[no-untyped-def]
    from src.sales.leads import leads_from_db

    with s.session() as session:
        return leads_from_db(session, journal_number=JOURNAL)


def _all_matches_preview(s: Scenario):  # type: ignore[no-untyped-def]
    """Every lead as a match, so the renderer itself is tested, not the selector."""
    from src.sales.matching import MatchedLead, PreviewResult

    return PreviewResult(
        prospect=make_prospect(),
        matches=[
            MatchedLead(lead=lead, fit_score=50.0, supplier_line="Pouches for a bar range.")
            for lead in _leads(s)
        ],
        source="database",
        newest_publication=PUBLISHED,
    )


@renderer("weekly_email_html")
def _email_html(s: Scenario) -> str:
    from src.deliver.email_render import render_weekly_email

    return render_weekly_email(s.result, settings=s.settings).html


@renderer("weekly_email_text")
def _email_text(s: Scenario) -> str:
    from src.deliver.email_render import render_weekly_email

    rendered = render_weekly_email(s.result, settings=s.settings)
    return rendered.subject + "\n" + rendered.text


@renderer("weekly_csv")
def _weekly_csv(s: Scenario) -> str:
    from src.deliver.csv_export import write_opportunities_csv

    path = write_opportunities_csv(s.result.deliverable, s.tmp_path / "out" / "weekly.csv")
    return path.read_text(encoding="utf-8-sig")


@renderer("send_rehydrated_email_and_csv")
def _rehydrated(s: Scenario) -> str:
    """The `send --run-id` and `regenerate-csv` paths rebuild from the database."""
    from sqlalchemy import select

    from src.commands import _rehydrate_result
    from src.deliver.csv_export import write_opportunities_csv
    from src.deliver.email_render import render_weekly_email

    with s.session() as session:
        run = session.execute(select(PipelineRun)).scalars().first()
        assert run is not None
        result = _rehydrate_result(session, run)
    rendered = render_weekly_email(result, settings=s.settings)
    csv_path = write_opportunities_csv(result.deliverable, s.tmp_path / "out" / "regen.csv")
    return rendered.html + rendered.text + csv_path.read_text(encoding="utf-8-sig")


@renderer("cli_opportunities_listing")
def _cli_listing(s: Scenario) -> str:
    from types import SimpleNamespace

    from src.commands import cmd_opportunities
    from src.settings import get_settings

    s.monkeypatch.setenv("DATABASE_URL", s.database_url)
    get_settings.cache_clear()
    try:
        s.capsys.readouterr()
        cmd_opportunities(SimpleNamespace(journal=JOURNAL, band=None, limit=50))
        return s.capsys.readouterr().out
    finally:
        get_settings.cache_clear()


@renderer("sample_pack")
def _sample_pack(s: Scenario) -> str:
    from src.deliver.sample_pack import build_sample_pack

    pack = build_sample_pack(_leads(s), s.tmp_path / "sample", settings=s.settings)
    return pack.csv_path.read_text(encoding="utf-8-sig") + pack.html_path.read_text(
        encoding="utf-8"
    )


@renderer("sample_report_unfiltered")
def _sample_report_all(s: Scenario) -> str:
    """The report renderer fed every lead, not just those the pack qualifies."""
    from src.deliver.sample_pack import _write_sample_csv, render_sample_report

    leads = _leads(s)
    html = render_sample_report(leads, settings=s.settings)
    csv_path = _write_sample_csv(leads, s.tmp_path / "sample_all" / "all.csv")
    return html + csv_path.read_text(encoding="utf-8-sig")


@renderer("prospect_preview")
def _preview(s: Scenario) -> str:
    from src.sales.preview import render_email_block, render_markdown, write_preview_csv

    preview = _all_matches_preview(s)
    csv_path = write_preview_csv(preview, s.tmp_path / "preview.csv")
    return (
        render_markdown(preview)
        + render_email_block(preview)
        + csv_path.read_text(encoding="utf-8-sig")
    )


@renderer("prospect_preview_selected")
def _preview_selected(s: Scenario) -> str:
    from src.sales.matching import select_for_prospect
    from src.sales.preview import render_markdown

    return render_markdown(
        select_for_prospect(make_prospect(), _leads(s), count=5, reference_date=PUBLISHED)
    )


@renderer("outreach_draft")
def _draft(s: Scenario) -> str:
    from src.sales.outreach import render_draft, write_draft

    draft = render_draft(make_prospect(), preview=_all_matches_preview(s), sample_count=3)
    path = write_draft(draft, s.tmp_path / "drafts")
    return draft.subject + draft.body + path.read_text(encoding="utf-8")


@renderer("transactional_emails")
def _transactional(s: Scenario) -> str:
    from src.deliver.email_render import render_alert_email, render_sample_email
    from src.deliver.transactional import (
        render_cancellation_confirmed,
        render_onboarding,
        render_payment_failed,
    )

    parts = [
        render_onboarding("Pouchworks Ltd", settings=s.settings),
        render_payment_failed("Pouchworks Ltd", settings=s.settings),
        render_cancellation_confirmed("Pouchworks Ltd", settings=s.settings),
        render_sample_email("Pouchworks Ltd", None, total=3, settings=s.settings),
        render_alert_email("Run failed", s.result.run_id, JOURNAL, "failed", "detail"),
    ]
    return "".join(p.html + p.text for p in parts)


@renderer("website_and_api")
def _website(s: Scenario) -> str:
    from fastapi.testclient import TestClient

    from src.settings import get_settings

    s.monkeypatch.setenv("DATABASE_URL", s.database_url)
    s.monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    s.monkeypatch.setenv("SITE_URL", "https://launchtrace.test")
    get_settings.cache_clear()
    try:
        from src.web.app import create_app

        client = TestClient(create_app())
        pages = [
            client.get("/"),
            client.get("/privacy"),
            client.get("/terms"),
            client.get("/feedback", params={"tm": "UK00003900002"}),
            client.get("/admin", params={"token": "test-admin-token"}),
            client.get("/api/health"),
            client.get("/api/config"),
        ]
        return "".join(p.text for p in pages)
    finally:
        get_settings.cache_clear()


@renderer("public_feed")
def _feed_renderer(s: Scenario) -> str:
    """The public company-level feed (``src/feed``): every file it would publish.

    Brands are synced first because the feed reads leads through them, and the
    publication delay is set to zero so the scenario's only week is actually
    rendered -- with the default delay the feed would be empty and the check
    would pass vacuously.
    """
    from dataclasses import replace

    from src.brands import sync_brands
    from src.feed.build import build_feed, load_feed_config
    from src.feed.render import app_links, render_site, static_links

    with s.session() as session:
        sync_brands(session, s.result)
        session.commit()
        feed = build_feed(session, replace(load_feed_config(), delay_weeks=0))
    parts: list[str] = []
    for links in (static_links(feed, s.settings.site_url), app_links(feed, s.settings.site_url)):
        files = render_site(feed, links)
        parts.extend(body.decode("utf-8") for _, body in sorted(files.items()))
    return "\n".join(parts)


def _movers_rows(s: Scenario, session):  # type: ignore[no-untyped-def]
    """Sync the scenario's brands and give every one a meaningful rescan move."""
    from sqlalchemy import select

    from src.brands import record_stage_change, sync_brands
    from src.db.tables import Brand

    sync_brands(session, s.result)
    for brand in session.execute(select(Brand)).scalars():
        record_stage_change(
            session,
            brand.id,
            "web:holding_page",
            "web:live_store",
            {
                "detected_by": "rescan",
                "dimension": "web_presence",
                "old_value": "holding_page",
                "new_value": "live_store",
                "meaningful": True,
                "negative": False,
                "launched": True,
            },
        )
    session.flush()


@renderer("movers_digest")
def _movers_digest(s: Scenario) -> str:
    """The rescan's 'brands that moved' email, through the real selection and send path."""
    from src.db.tables import Customer, CustomerPreference
    from src.deliver.resend_client import EmailSender
    from src.rescan.digest import send_movers_digests

    outbox = s.tmp_path / "movers_outbox"
    with s.session() as session:
        _movers_rows(s, session)
        customer = Customer(company="Pouchworks Ltd", subscription_status="active")
        session.add(customer)
        session.flush()
        session.add(
            CustomerPreference(customer_id=customer.id, recipient_email="buyer@pouchworks.test")
        )
        session.flush()
        send_movers_digests(session, s.settings, sender=EmailSender(s.settings, outbox=outbox))
        session.commit()
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(outbox.glob("*")))


@renderer("movers_digest_unfiltered")
def _movers_digest_all(s: Scenario) -> str:
    """The digest renderer fed every brand that moved, confirmed company or not."""
    from datetime import timedelta

    from src.rescan.digest import meaningful_changes, movers_from, render_movers_digest

    with s.session() as session:
        _movers_rows(s, session)
        since = datetime.now(UTC) - timedelta(days=7)
        movers = movers_from(meaningful_changes(session, since), require_company=False)
        session.rollback()
    assert len(movers) == 3
    rendered = render_movers_digest(movers, since=since, settings=s.settings)
    return rendered.subject + rendered.html + rendered.text


# ---------------------------------------------------------------------------
# the scenario and the assertions
# ---------------------------------------------------------------------------


@pytest.fixture
def scenario(settings, tmp_path, monkeypatch, capsys):  # type: ignore[no-untyped-def]
    result = build_result()
    engine = get_engine(settings.database_url)
    Base.metadata.create_all(engine)
    from src.db.engine import init_db

    init_db(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        save_opportunities(session, result)
        save_run(session, result)
        session.commit()
    return Scenario(result, settings, settings.database_url, tmp_path, monkeypatch, capsys)


@pytest.fixture
def outputs(scenario) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {name: render(scenario) for name, render in OUTPUT_RENDERERS.items()}


def test_every_registered_output_is_free_of_individual_names(outputs):
    leaks = [
        f"{name}: {needle!r}"
        for name, text in outputs.items()
        for needle in FORBIDDEN
        if needle.lower() in text.lower()
    ]
    assert not leaks, "individual applicant names leaked into: " + ", ".join(leaks)


def test_the_corporate_company_does_appear(outputs):
    """Guard against a vacuous pass: real lead data was rendered."""
    showing = [name for name, text in outputs.items() if CORPORATE_COMPANY.lower() in text.lower()]
    for required in (
        "weekly_email_html",
        "weekly_email_text",
        "weekly_csv",
        "send_rehydrated_email_and_csv",
        "sample_pack",
        "prospect_preview",
        "outreach_draft",
        "website_and_api",
        "public_feed",
        "movers_digest",
        "movers_digest_unfiltered",
    ):
        assert required in showing, f"{required} did not render the corporate lead"


def test_individual_leads_are_still_delivered_just_unnamed(outputs, scenario):
    """Redaction, not removal: the leads exist and are shown without a name."""
    assert len(scenario.result.deliverable) == 3
    assert "QUILLBERRY CRUNCH" in outputs["weekly_email_html"]
    assert INDIVIDUAL_WITHHELD in outputs["weekly_email_html"]
    assert INDIVIDUAL_WITHHELD in outputs["weekly_email_text"]
    assert "BRACKEN BITES" in outputs["weekly_csv"]


def test_every_named_output_is_registered():
    expected = {
        "weekly_email_html",
        "weekly_email_text",
        "weekly_csv",
        "send_rehydrated_email_and_csv",
        "cli_opportunities_listing",
        "sample_pack",
        "sample_report_unfiltered",
        "prospect_preview",
        "prospect_preview_selected",
        "outreach_draft",
        "transactional_emails",
        "website_and_api",
        "public_feed",
        "movers_digest",
        "movers_digest_unfiltered",
    }
    assert expected <= set(OUTPUT_RENDERERS)


class TestPrivacyRules:
    def test_natural_person_is_individual_even_with_a_corporate_looking_name(self):
        assert is_individual_applicant("Smith Foods Ltd", ApplicantType.NATURAL_PERSON)
        assert is_individual_applicant("Smith Foods Ltd", "natural_person")

    def test_unknown_type_without_corporate_suffix_is_individual(self):
        assert is_individual_applicant("Jane Doe", None)
        assert is_individual_applicant("Jane Doe", "unknown")
        assert not is_individual_applicant("Jane Doe Foods Ltd", "unknown")

    def test_display_prefers_the_registered_company(self):
        assert display_party("ACME LTD", "Jane Doe", "natural_person") == "ACME LTD"

    def test_display_withholds_an_individual(self):
        assert display_party(None, "Jane Doe", "natural_person") == INDIVIDUAL_WITHHELD

    def test_display_keeps_a_corporate_unmatched_applicant(self):
        assert display_party(None, "Acme Foods Ltd", "corporate") == "Acme Foods Ltd"

    def test_display_falls_back_to_empty_text_when_nothing_is_known(self):
        assert display_party(None, None, None, empty="unknown company") == "unknown company"

    def test_one_company_once_does_not_collapse_different_individuals(self):
        from src.sales.leads import Lead

        a = Lead(brand_name="A", trademark_number="1", applicant_name="Jane Doe")
        b = Lead(brand_name="B", trademark_number="2", applicant_name="John Roe")
        assert a.display_company == b.display_company == INDIVIDUAL_WITHHELD
        assert a.identity_key != b.identity_key


class TestLogs:
    def test_a_failed_companies_house_lookup_does_not_log_an_individual(self):
        from structlog.testing import capture_logs

        from src.enrich.companies_house import CompanyRegistry
        from src.errors import ProviderError

        class Failing(CompanyRegistry):
            name = "failing"

            def find_candidates(self, applicant_name):  # type: ignore[no-untyped-def]
                raise ProviderError(f"429 for url ...?q={applicant_name}")

        with capture_logs() as logs:
            Failing().match(INDIVIDUAL)
            Failing().match("Crumbledge Foods Ltd")
        text = repr(logs)
        assert "quillfeather" not in text.lower()
        assert "Crumbledge Foods Ltd" in text, "corporate applicants are still logged"
