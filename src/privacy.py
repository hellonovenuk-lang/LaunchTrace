"""Keeping individual applicants' names out of everything LaunchTrace outputs.

A trade mark applicant can be a private individual. Their name is in the UKIPO
journal and LaunchTrace stores it (it is how the "first trade mark for this
applicant" signal works), but it must never reach a customer, a prospect or the
public. Every renderer that wants to say *who* is behind a brand asks this
module instead of reading ``applicant_name`` itself.

The rule, deliberately conservative:

* a confirmed Companies House match is shown by its registered company name;
* an applicant is treated as an individual when it is typed
  ``natural_person``, **or** when it is not matched and its name does not look
  corporate (the same suffix test the food filter uses to type applicants), so
  an untyped or rehydrated row cannot slip through;
* an individual's name is replaced by neutral text, never shown.

Only *rendering* changes here. Scoring, banding and which leads exist are not
touched (see DECISIONS.md D-400).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.parse.normalise import normalise_text
from src.settings import load_config

# Neutral, descriptive text. Not legal wording.
INDIVIDUAL_WITHHELD = "Individual applicant (name withheld)"

NATURAL_PERSON = "natural_person"
CORPORATE = "corporate"


@lru_cache(maxsize=1)
def _corporate_suffixes() -> frozenset[str]:
    cfg = load_config("exclusions.json")["natural_person_applicant"]
    return frozenset(str(s).lower() for s in cfg["corporate_suffixes"])


def looks_corporate(applicant_name: str | None) -> bool:
    """Same test as ``FoodFilter.looks_corporate``: a corporate suffix token."""
    n = normalise_text(applicant_name)
    if not n:
        return False
    return bool(set(n.split()) & _corporate_suffixes())


def _type_value(applicant_type: Any) -> str:
    value = getattr(applicant_type, "value", applicant_type)
    return str(value or "").strip().lower()


def is_individual_applicant(
    applicant_name: str | None,
    applicant_type: Any = None,
) -> bool:
    """True when the applicant must be treated as a private individual.

    ``applicant_type`` may be an ``ApplicantType``, its string value, or None.
    A name that does not look corporate is treated as an individual whatever
    the stored type says, unless the type is explicitly ``corporate`` *and* the
    name looks corporate.
    """
    if _type_value(applicant_type) == NATURAL_PERSON:
        return True
    return not looks_corporate(applicant_name)


def safe_applicant_name(applicant_name: str | None, applicant_type: Any = None) -> str | None:
    """The applicant name if it may be shown, else None."""
    if not applicant_name or not applicant_name.strip():
        return None
    if is_individual_applicant(applicant_name, applicant_type):
        return None
    return applicant_name


def display_party(
    company_name: str | None,
    applicant_name: str | None,
    applicant_type: Any = None,
    *,
    empty: str = "",
) -> str:
    """Who is behind a lead, as any output may show it.

    Registered company name first; then a corporate-looking applicant name;
    then neutral text for an individual; then ``empty`` when nothing is known.
    """
    if company_name and company_name.strip():
        return company_name
    safe = safe_applicant_name(applicant_name, applicant_type)
    if safe:
        return safe
    if applicant_name and applicant_name.strip():
        return INDIVIDUAL_WITHHELD
    return empty


def display_party_for(opp: Any, *, empty: str = "") -> str:
    """``display_party`` for an ``Opportunity`` (or anything shaped like one)."""
    company = getattr(opp, "company", None)
    company_name = getattr(company, "company_name", None) if company is not None else None
    return display_party(
        company_name,
        getattr(opp, "applicant_name", None),
        getattr(opp, "applicant_type", None),
        empty=empty,
    )


__all__ = [
    "INDIVIDUAL_WITHHELD",
    "display_party",
    "display_party_for",
    "is_individual_applicant",
    "looks_corporate",
    "safe_applicant_name",
]
