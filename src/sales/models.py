"""The prospect record and its lifecycle.

One row per supplier company in ``outreach/prospects.csv``. That file is the
source of truth: it is git-tracked, an operator can edit it in a spreadsheet,
and no code writes to it except the deliberate save in ``src/sales/store.py``.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def today() -> date:
    return datetime.now(UTC).date()


class ProspectStatus(str, Enum):
    """Where a prospect stands in LaunchTrace's own sales process.

    The order of this enum is the order of the funnel, which is what
    ``rank`` relies on for reporting.
    """

    RESEARCHED = "RESEARCHED"
    READY = "READY"
    EMAIL_1_SENT = "EMAIL_1_SENT"
    REPLIED_INTERESTED = "REPLIED_INTERESTED"
    SAMPLE_REQUESTED = "SAMPLE_REQUESTED"
    SAMPLE_SENT = "SAMPLE_SENT"
    OFFER_SENT = "OFFER_SENT"
    SUBSCRIBED = "SUBSCRIBED"
    NOT_NOW = "NOT_NOW"
    NO_RESPONSE = "NO_RESPONSE"
    OPTED_OUT = "OPTED_OUT"
    SUPPRESSED = "SUPPRESSED"

    @property
    def rank(self) -> int:
        return _STATUS_ORDER.index(self)

    @property
    def is_terminal(self) -> bool:
        """Terminal means no further outreach, ever or for now."""
        return self in _TERMINAL

    @property
    def contactable(self) -> bool:
        """False means no code path and no operator should contact them."""
        return self not in {ProspectStatus.OPTED_OUT, ProspectStatus.SUPPRESSED}


_STATUS_ORDER: list[ProspectStatus] = [
    ProspectStatus.RESEARCHED,
    ProspectStatus.READY,
    ProspectStatus.EMAIL_1_SENT,
    ProspectStatus.REPLIED_INTERESTED,
    ProspectStatus.SAMPLE_REQUESTED,
    ProspectStatus.SAMPLE_SENT,
    ProspectStatus.OFFER_SENT,
    ProspectStatus.SUBSCRIBED,
    ProspectStatus.NOT_NOW,
    ProspectStatus.NO_RESPONSE,
    ProspectStatus.OPTED_OUT,
    ProspectStatus.SUPPRESSED,
]

_TERMINAL = {
    ProspectStatus.SUBSCRIBED,
    ProspectStatus.NOT_NOW,
    ProspectStatus.NO_RESPONSE,
    ProspectStatus.OPTED_OUT,
    ProspectStatus.SUPPRESSED,
}

# Which statuses a status may legally move to. Anything else is a mistake worth
# catching, because the funnel numbers are only meaningful if the states are.
ALLOWED_TRANSITIONS: dict[ProspectStatus, set[ProspectStatus]] = {
    ProspectStatus.RESEARCHED: {ProspectStatus.READY, ProspectStatus.SUPPRESSED},
    ProspectStatus.READY: {
        ProspectStatus.EMAIL_1_SENT,
        ProspectStatus.RESEARCHED,
        ProspectStatus.SUPPRESSED,
    },
    ProspectStatus.EMAIL_1_SENT: {
        ProspectStatus.REPLIED_INTERESTED,
        ProspectStatus.SAMPLE_REQUESTED,
        ProspectStatus.NOT_NOW,
        ProspectStatus.NO_RESPONSE,
        ProspectStatus.OPTED_OUT,
        ProspectStatus.SUPPRESSED,
    },
    ProspectStatus.REPLIED_INTERESTED: {
        ProspectStatus.SAMPLE_REQUESTED,
        ProspectStatus.SAMPLE_SENT,
        ProspectStatus.NOT_NOW,
        ProspectStatus.OPTED_OUT,
        ProspectStatus.SUPPRESSED,
    },
    ProspectStatus.SAMPLE_REQUESTED: {
        ProspectStatus.SAMPLE_SENT,
        ProspectStatus.NOT_NOW,
        ProspectStatus.OPTED_OUT,
        ProspectStatus.SUPPRESSED,
    },
    ProspectStatus.SAMPLE_SENT: {
        ProspectStatus.OFFER_SENT,
        ProspectStatus.SUBSCRIBED,
        ProspectStatus.NOT_NOW,
        ProspectStatus.NO_RESPONSE,
        ProspectStatus.OPTED_OUT,
        ProspectStatus.SUPPRESSED,
    },
    ProspectStatus.OFFER_SENT: {
        ProspectStatus.SUBSCRIBED,
        ProspectStatus.NOT_NOW,
        ProspectStatus.NO_RESPONSE,
        ProspectStatus.OPTED_OUT,
        ProspectStatus.SUPPRESSED,
    },
    # A subscriber who cancels becomes a customer-lifecycle problem, not a
    # prospecting one, so the only move left here is suppression.
    ProspectStatus.SUBSCRIBED: {ProspectStatus.SUPPRESSED, ProspectStatus.OPTED_OUT},
    ProspectStatus.NOT_NOW: {
        ProspectStatus.READY,
        ProspectStatus.OPTED_OUT,
        ProspectStatus.SUPPRESSED,
    },
    ProspectStatus.NO_RESPONSE: {
        ProspectStatus.READY,
        ProspectStatus.OPTED_OUT,
        ProspectStatus.SUPPRESSED,
    },
    # Opting out is one-way. Nothing reopens it except a human editing the CSV.
    ProspectStatus.OPTED_OUT: set(),
    ProspectStatus.SUPPRESSED: set(),
}


class TransitionError(ValueError):
    """A status change that the funnel does not allow."""


def check_transition(current: ProspectStatus, target: ProspectStatus) -> None:
    if current == target:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise TransitionError(
            f"{current.value} cannot become {target.value}. "
            f"Allowed from {current.value}: "
            + (
                ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, set())))
                or "none"
            )
        )


class Priority(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    SUPPRESS = "SUPPRESS"


class ReplyState(str, Enum):
    NONE = "none"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    OPT_OUT = "opt_out"


class EmailSource(str, Enum):
    """Where a contact address came from. Never a guess."""

    NONE = "none"
    WEBSITE_VERIFIED = "website_verified"
    COMPANIES_HOUSE = "companies_house"
    INBOUND = "inbound"
    OPERATOR_KNOWN = "operator_known"


SUPPLIER_CATEGORIES = [
    "flexible_packaging",
    "labels",
    "cartons",
    "contract_manufacturing",
    "copacking",
    "distribution",
    "brokerage",
    "fulfilment",
    "marketing",
    "other",
]

CONTACT_ROUTES = ["generic_email", "website_form", "phone", "linkedin", "unknown"]


def normalise_domain(website: str | None) -> str:
    """The identity used for duplicate detection. Empty means 'cannot tell'."""
    if not website:
        return ""
    text = website.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.split("/")[0].strip()


def normalise_company(name: str | None) -> str:
    """Company name reduced to a comparison key, suffixes and punctuation gone."""
    if not name:
        return ""
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(
        r"\b(limited|ltd|llp|plc|group|holdings|uk|co|company|the)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


class Prospect(BaseModel):
    """One supplier company LaunchTrace might sell to.

    Deliberately flat: it round-trips to a CSV row so the owner can open the
    list in a spreadsheet without any tooling.
    """

    model_config = ConfigDict(use_enum_values=False)

    prospect_id: str = ""
    company_name: str
    website: str = ""
    companies_house_number: str = ""
    company_type: str = "unknown"
    supplier_category: str = "other"
    supplier_subcategory: str = ""
    geography: str = ""
    icp_reason: str = ""
    products_services: str = ""
    buying_intent_categories: list[str] = Field(default_factory=list)
    contact_route: str = "unknown"
    generic_contact_email: str = ""
    named_contact: str = ""
    decision_maker_role: str = ""
    email_source: EmailSource = EmailSource.NONE
    company_size_hint: str = ""

    status: ProspectStatus = ProspectStatus.RESEARCHED
    priority: Priority = Priority.C
    icp_score: int = 0

    date_added: date | None = None
    email_1_sent_date: date | None = None
    sample_requested_date: date | None = None
    sample_sent_date: date | None = None
    offer_sent_date: date | None = None
    converted_date: date | None = None
    follow_up_due_date: date | None = None

    stripe_customer_id: str = ""
    reply_state: ReplyState = ReplyState.NONE
    opted_out: bool = False
    suppression_reason: str = ""
    notes: str = ""

    @field_validator("supplier_category", mode="before")
    @classmethod
    def _known_category(cls, v: str) -> str:
        text = (v or "other").strip().lower()
        return text if text in SUPPLIER_CATEGORIES else "other"

    @field_validator("buying_intent_categories", mode="before")
    @classmethod
    def _split_intents(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [p.strip() for p in v.split("|") if p.strip()]
        if isinstance(v, list):
            return [str(p).strip() for p in v if str(p).strip()]
        return []

    # -- derived -----------------------------------------------------------

    @property
    def domain(self) -> str:
        return normalise_domain(self.website)

    @property
    def company_key(self) -> str:
        return normalise_company(self.company_name)

    @property
    def contactable(self) -> bool:
        """The single question every outreach step must ask first."""
        return not self.opted_out and self.status.contactable

    @property
    def has_verified_email(self) -> bool:
        return bool(self.generic_contact_email) and self.email_source != EmailSource.NONE

    def searchable_text(self, fields: list[str]) -> str:
        """The text ICP scoring reads, joined and lowercased."""
        parts: list[str] = []
        for name in fields:
            value = getattr(self, name, "")
            if isinstance(value, list):
                parts.append(" ".join(str(v) for v in value))
            elif isinstance(value, Enum):
                parts.append(str(value.value))
            elif value:
                parts.append(str(value))
        return " ".join(parts).lower()
