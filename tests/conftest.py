"""Shared test fixtures.

Nothing here touches the network or a real credential. Every external
dependency is either a fixture provider or a stub.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.classify.food_filter import FoodFilter
from src.classify.pipeline import ProductClassifier
from src.db.engine import get_engine
from src.db.tables import Base
from src.enrich.companies_house import FixtureCompanyRegistry
from src.enrich.providers import FixtureSearchProvider
from src.enrich.web import WebEnricher
from src.ingest.fixture import FixtureJournalSource
from src.models import CompanyMatch, ProductAssessment, TrademarkRecord, WebEnrichment
from src.pipeline_core import Pipeline
from src.score.launchtrace_score import LaunchTraceScorer
from src.settings import FIXTURES_DIR, Settings

FIXTURE_JOURNAL = FIXTURES_DIR / "journals" / "2025-050.xml"
MALFORMED_JOURNAL = FIXTURES_DIR / "malformed" / "malformed.xml"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        DATABASE_URL=f"sqlite:///{tmp_path / 'test.sqlite'}",
        SEND_MODE="review",
        JOURNAL_SOURCE="fixture",
        LLM_PROVIDER="none",
        SEARCH_PROVIDER="fixture",
        SITE_URL="https://launchtrace.test",
        CACHE_DIR=str(tmp_path / "cache"),
        ENVIRONMENT="test",
    )


@pytest.fixture
def db_session(settings: Settings):  # type: ignore[no-untyped-def]
    from sqlalchemy.orm import sessionmaker

    engine = get_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def food_filter() -> FoodFilter:
    return FoodFilter()


@pytest.fixture
def scorer() -> LaunchTraceScorer:
    return LaunchTraceScorer()


@pytest.fixture
def company_registry() -> FixtureCompanyRegistry:
    return FixtureCompanyRegistry()


@pytest.fixture
def web_enricher(settings: Settings) -> WebEnricher:
    return WebEnricher(provider=FixtureSearchProvider(), settings=settings)


@pytest.fixture
def pipeline(settings: Settings, tmp_path: Path, company_registry, web_enricher) -> Pipeline:  # type: ignore[no-untyped-def]
    return Pipeline(
        settings=settings,
        source=FixtureJournalSource(settings),
        registry=company_registry,
        classifier=ProductClassifier(settings, llm_provider=None),
        web=web_enricher,
        output_dir=tmp_path / "runs",
    )


@pytest.fixture
def llm_responses() -> dict[str, str]:
    return json.loads((FIXTURES_DIR / "llm" / "responses.json").read_text(encoding="utf-8"))


def make_record(**overrides) -> TrademarkRecord:  # type: ignore[no-untyped-def]
    base = {
        "trademark_number": "UK00003900001",
        "mark_text": "CRUMBLEDGE",
        "mark_type": "Word",
        "filing_date": date(2025, 9, 15),
        "publication_date": date(2025, 12, 12),
        "applicant_name": "Crumbledge Foods Ltd",
        "applicant_country": "GB",
        "nice_classes": [30],
        "goods_text": "Snack bars; cereal bars; oat bars; biscuits.",
        "goods_text_available": True,
        "journal_number": "2025-050",
        "source_url": "https://example.invalid/tm/UK00003900001",
    }
    base.update(overrides)
    return TrademarkRecord(**base)  # type: ignore[arg-type]


def make_company(**overrides) -> CompanyMatch:  # type: ignore[no-untyped-def]
    base = {
        "matched": True,
        "company_name": "CRUMBLEDGE FOODS LTD",
        "company_number": "14000001",
        "company_status": "Active",
        "incorporation_date": date(2025, 3, 1),
        "sic_codes": ["10720"],
        "region": "BRISTOL",
        "accounts_category": "MICRO ENTITY",
        "match_confidence": 98,
        "match_method": "exact_name",
        "provider": "fixture",
    }
    base.update(overrides)
    return CompanyMatch(**base)  # type: ignore[arg-type]


def make_assessment(**overrides) -> ProductAssessment:  # type: ignore[no-untyped-def]
    base = {
        "is_food_candidate": True,
        "consumer_product": True,
        "physical_product": True,
        "food_vertical": True,
        "product_category": "cereal_bars",
        "product_category_label": "Cereal, protein and energy bars",
        "packaged_product_probability": 0.85,
    }
    base.update(overrides)
    return ProductAssessment(**base)  # type: ignore[arg-type]


def make_web(**overrides) -> WebEnrichment:  # type: ignore[no-untyped-def]
    base = {"attempted": False, "provider": "none"}
    base.update(overrides)
    return WebEnrichment(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# commercial-operations fixtures
# ---------------------------------------------------------------------------


def make_prospect(**overrides):  # type: ignore[no-untyped-def]
    """A prospect with enough research on it to be scored and previewed."""
    from src.sales.models import Prospect

    base = {
        "prospect_id": "P001",
        "company_name": "Pouchworks Ltd",
        "website": "https://pouchworks.test/",
        "company_type": "ltd",
        "supplier_category": "flexible_packaging",
        "geography": "UK",
        "icp_reason": "Printed pouches for food startups, low MOQ and short runs.",
        "contact_route": "website_form",
        "date_added": date(2026, 1, 6),
    }
    base.update(overrides)
    return Prospect(**base)  # type: ignore[arg-type]


def make_lead(**overrides):  # type: ignore[no-untyped-def]
    """A qualifying opportunity, as the sales tooling sees it."""
    from src.sales.leads import Lead

    base = {
        "brand_name": "CRUMBLEDGE",
        "trademark_number": "UK00003900001",
        "product_category": "cereal_bars",
        "product_category_label": "Cereal, protein and energy bars",
        "company_name": "CRUMBLEDGE FOODS LTD",
        "company_number": "14000001",
        "company_incorporation_date": date(2025, 3, 1),
        "company_region": "BRISTOL",
        "launch_stage": "pre_launch",
        "intents": {
            "flexible_packaging": "HIGH",
            "labels": "HIGH",
            "cartons": "MEDIUM",
            "contract_manufacturing": "HIGH",
            "copacking": "HIGH",
            "distribution": "MEDIUM",
            "brokerage": "LOW",
            "fulfilment": "MEDIUM",
            "marketing": "MEDIUM",
        },
        "score": 78,
        "band": "MEDIUM",
        "reasons": ["UK company incorporated 6 months before this filing"],
        "source_url": "https://example.invalid/tm/UK00003900001",
        "filing_date": date(2025, 9, 15),
        "publication_date": date(2025, 12, 12),
        "journal_number": "2025-050",
    }
    base.update(overrides)
    return Lead(**base)  # type: ignore[arg-type]


@pytest.fixture
def prospect_store(db_session, tmp_path: Path):  # type: ignore[no-untyped-def]
    """An isolated prospect store, so no test can touch the real list.

    Live state goes to the test database; the seed file is a throwaway path, so
    nothing here can write to the researched list in the repository.
    """
    from src.sales.store import ProspectStore, SuppressionList

    return ProspectStore(
        prospects=[],
        session=db_session,
        suppressions=SuppressionList(),
        seed_path=tmp_path / "prospects_seed.csv",
    )


@pytest.fixture
def customer(db_session):  # type: ignore[no-untyped-def]
    """An active founding customer with one recipient."""
    from src.db.tables import Customer, CustomerPreference

    row = Customer(
        company="Pouchworks Ltd",
        plan_key="founding_monthly",
        subscription_status="active",
        supplier_type="flexible_packaging",
    )
    db_session.add(row)
    db_session.flush()
    db_session.add(CustomerPreference(customer_id=row.id, recipient_email="sales@pouchworks.test"))
    db_session.flush()
    return row
