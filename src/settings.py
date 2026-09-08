"""Application settings.

Everything that varies between environments is read from environment variables
(or a local .env file).  Nothing here contains a secret value -- only names and
safe defaults.
"""

from __future__ import annotations

import json
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
REPORTS_DIR = REPO_ROOT / "reports"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

SendMode = Literal["review", "automatic"]


class Settings(BaseSettings):
    """Runtime configuration for the LaunchTrace pipeline and website."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- environment -----------------------------------------------------
    environment: str = Field(default="development", alias="ENVIRONMENT")
    site_url: str = Field(default="http://localhost:8000", alias="SITE_URL")
    admin_email: str = Field(default="", alias="ADMIN_EMAIL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["json", "console"] = Field(default="console", alias="LOG_FORMAT")

    # --- delivery safety --------------------------------------------------
    send_mode: SendMode = Field(default="review", alias="SEND_MODE")

    # --- database ---------------------------------------------------------
    # Default is a local SQLite file so the project runs with zero setup.
    database_url: str = Field(default="", alias="DATABASE_URL")
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")

    # --- UKIPO ingest -----------------------------------------------------
    ukipo_journal_base_url: str = Field(
        default="https://www.ipo.gov.uk/types/tm/t-os/t-tmj/tm-journals",
        alias="UKIPO_JOURNAL_BASE_URL",
    )
    ukipo_journal_index_url: str = Field(
        default="https://www.ipo.gov.uk/t-tmj.htm", alias="UKIPO_JOURNAL_INDEX_URL"
    )
    ukipo_user_agent: str = Field(
        default="LaunchTrace/0.1 (+https://launchtrace.co.uk; weekly trade marks journal ingest)",
        alias="UKIPO_USER_AGENT",
    )
    ukipo_request_timeout_seconds: int = Field(default=120, alias="UKIPO_REQUEST_TIMEOUT_SECONDS")
    ukipo_max_retries: int = Field(default=4, alias="UKIPO_MAX_RETRIES")
    journal_source: Literal["ukipo_http", "open_data", "local", "fixture"] = Field(
        default="ukipo_http", alias="JOURNAL_SOURCE"
    )
    journal_local_dir: str = Field(default="", alias="JOURNAL_LOCAL_DIR")
    cache_dir: str = Field(default=str(DATA_DIR / "cache"), alias="CACHE_DIR")
    keep_raw_journal_files: bool = Field(default=False, alias="KEEP_RAW_JOURNAL_FILES")

    # --- Companies House ---------------------------------------------------
    companies_house_api_key: str = Field(default="", alias="COMPANIES_HOUSE_API_KEY")
    companies_house_base_url: str = Field(
        default="https://api.company-information.service.gov.uk",
        alias="COMPANIES_HOUSE_BASE_URL",
    )
    company_registry_provider: Literal["api", "bulk", "fixture", "auto"] = Field(
        default="auto", alias="COMPANY_REGISTRY_PROVIDER"
    )
    companies_house_bulk_index: str = Field(
        default=str(DATA_DIR / "companies_house" / "ch_index.sqlite"),
        alias="COMPANIES_HOUSE_BULK_INDEX",
    )
    companies_house_bulk_url: str = Field(
        default="https://download.companieshouse.gov.uk/en_output.html",
        alias="COMPANIES_HOUSE_BULK_URL",
    )

    # --- LLM ---------------------------------------------------------------
    llm_provider: Literal["anthropic", "openai", "none"] = Field(
        default="none", alias="LLM_PROVIDER"
    )
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="claude-haiku-4-5-20251001", alias="LLM_MODEL")
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_max_candidates_per_run: int = Field(default=400, alias="LLM_MAX_CANDIDATES_PER_RUN")
    llm_timeout_seconds: int = Field(default=60, alias="LLM_TIMEOUT_SECONDS")

    # --- web enrichment ----------------------------------------------------
    search_provider: Literal["tavily", "serper", "brave", "fixture", "none"] = Field(
        default="none", alias="SEARCH_PROVIDER"
    )
    search_api_key: str = Field(default="", alias="SEARCH_API_KEY")
    search_max_candidates_per_run: int = Field(default=150, alias="SEARCH_MAX_CANDIDATES_PER_RUN")
    search_timeout_seconds: int = Field(default=30, alias="SEARCH_TIMEOUT_SECONDS")

    # --- email -------------------------------------------------------------
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    email_from: str = Field(default="LaunchTrace <feed@launchtrace.co.uk>", alias="EMAIL_FROM")
    email_reply_to: str = Field(default="", alias="EMAIL_REPLY_TO")

    # --- Stripe ------------------------------------------------------------
    stripe_secret_key: str = Field(default="", alias="STRIPE_SECRET_KEY")
    stripe_publishable_key: str = Field(default="", alias="STRIPE_PUBLISHABLE_KEY")
    stripe_webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET")
    stripe_founding_price_id: str = Field(default="", alias="STRIPE_FOUNDING_PRICE_ID")
    stripe_standard_price_id: str = Field(default="", alias="STRIPE_STANDARD_PRICE_ID")

    # --- admin -------------------------------------------------------------
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    @field_validator("database_url", mode="after")
    @classmethod
    def _default_database_url(cls, v: str) -> str:
        if v:
            # SQLAlchemy needs the driver-qualified scheme for psycopg3.
            if v.startswith("postgres://"):
                v = "postgresql+psycopg://" + v[len("postgres://") :]
            elif v.startswith("postgresql://"):
                v = "postgresql+psycopg://" + v[len("postgresql://") :]
            return v
        (DATA_DIR / "local").mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DATA_DIR / 'local' / 'launchtrace.sqlite'}"

    # --- derived helpers ---------------------------------------------------
    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none" and bool(self.llm_api_key)

    @property
    def search_enabled(self) -> bool:
        if self.search_provider == "fixture":
            return True
        return self.search_provider != "none" and bool(self.search_api_key)

    @property
    def email_enabled(self) -> bool:
        return bool(self.resend_api_key)

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.stripe_secret_key)

    def missing_credentials(self) -> dict[str, str]:
        """Names the credentials that are absent, and what each unlocks."""
        missing: dict[str, str] = {}
        if not self.companies_house_api_key:
            missing["COMPANIES_HOUSE_API_KEY"] = (
                "Live Companies House API lookups (the free bulk snapshot provider is used instead)"
            )
        if not self.llm_enabled:
            missing["LLM_API_KEY"] = "LLM-assisted product classification (rule-only mode is used)"
        if not self.search_enabled:
            missing["SEARCH_API_KEY"] = "Web enrichment (websites, retail presence, maturity)"
        if not self.resend_api_key:
            missing["RESEND_API_KEY"] = "Sending email (HTML is rendered to disk instead)"
        if not self.stripe_secret_key:
            missing["STRIPE_SECRET_KEY"] = "Live subscription checkout"
        if not self.supabase_url and self.is_sqlite:
            missing["DATABASE_URL"] = "Hosted PostgreSQL / Supabase (local SQLite is used)"
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@cache
def load_config(name: str) -> dict[str, Any]:
    """Load a JSON business-rules file from config/."""
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing configuration file: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
