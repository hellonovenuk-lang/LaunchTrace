"""Keeping individual applicants' names out of everything LaunchTrace outputs.

A trade mark applicant can be a private individual. Their name is in the UKIPO
journal and LaunchTrace stores it (it is how the "first trade mark for this
applicant" signal works), but it must never reach a customer, a prospect or the
public. Every renderer that wants to say *who* is behind a brand asks this
module instead of reading ``applicant_name`` itself.

The rule, deliberately conservative:

* a confirmed Companies House match is shown by its registered company name;
* otherwise the applicant's own name is shown only when it is not typed
  ``natural_person``, contains a *legal form* as a whole word or phrase
  ("Ltd", "Limited", "PLC", "LLP", "CIC", "GmbH" ... -- the narrow list in
  ``config/privacy.json``), and contains no sole-trader pattern ("trading as",
  "t/a", "& Co" without a legal form);
* everything else is treated as an individual and replaced by neutral text.

The broad ``corporate_suffixes`` list in ``config/exclusions.json`` (which
includes words like "foods", "trading", "co", "sa") is the *food filter's*
applicant typing and feeds scoring; it is deliberately not used here, because
"Jane Smith trading as Smith Foods" must never be shown (D-700).

Only *rendering* changes here. Scoring, banding and which leads exist are not
touched (see DECISIONS.md D-400).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from src.parse.normalise import normalise_text, strip_accents
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
    """Same test as ``FoodFilter.looks_corporate``: a broad corporate suffix token.

    Kept for callers that need the food filter's typing. It is **not** a
    privacy test: use ``is_individual_applicant`` / ``displayable_as_corporate``.
    """
    n = normalise_text(applicant_name)
    if not n:
        return False
    return bool(set(n.split()) & _corporate_suffixes())


def _phrase_pattern(phrase: str) -> str:
    # Whole word or phrase: not preceded by a letter, digit, dot, slash,
    # ampersand or hyphen; not followed by a letter, digit, slash or hyphen
    # ("Ltd-Smith" is a surname, not a legal form). A trailing dot after a form
    # ("Ltd.", "L.L.P.") still matches.
    return r"(?<![\w./&-])" + re.escape(phrase) + r"(?![\w/-])"


@lru_cache(maxsize=1)
def _privacy_rules() -> tuple[re.Pattern[str], list[tuple[str, re.Pattern[str]]]]:
    cfg = load_config("privacy.json")
    forms = sorted({str(f).strip().lower() for f in cfg["display_legal_forms"]}, key=len)
    legal = re.compile("|".join(_phrase_pattern(f) for f in reversed(forms)))
    patterns = [
        (phrase, re.compile(_phrase_pattern(phrase)))
        for phrase in (str(p).strip().lower() for p in cfg["sole_trader_patterns"])
    ]
    return legal, patterns


_AMPERSAND_CO = frozenset({"& co", "and co"})


def _prepare(name: str) -> str:
    t = strip_accents(name).lower()
    t = re.sub(r"[,()\[\]\"']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def has_legal_form(applicant_name: str | None) -> bool:
    """True when the name contains a legal form from ``config/privacy.json``."""
    if not applicant_name:
        return False
    legal, _ = _privacy_rules()
    return legal.search(_prepare(applicant_name)) is not None


def has_sole_trader_pattern(applicant_name: str | None) -> bool:
    """True for "X trading as Y", "X t/a Y", or "X & Co" with no legal form after it."""
    if not applicant_name:
        return False
    legal, patterns = _privacy_rules()
    text = _prepare(applicant_name)
    for phrase, pattern in patterns:
        for m in pattern.finditer(text):
            if phrase in _AMPERSAND_CO and legal.search(text, m.end()):
                continue
            return True
    return False


def displayable_as_corporate(applicant_name: str | None, applicant_type: Any = None) -> bool:
    """True only when an *unmatched* applicant's own name may be shown."""
    if not applicant_name or not applicant_name.strip():
        return False
    if _type_value(applicant_type) == NATURAL_PERSON:
        return False
    return has_legal_form(applicant_name) and not has_sole_trader_pattern(applicant_name)


def _type_value(applicant_type: Any) -> str:
    value = getattr(applicant_type, "value", applicant_type)
    return str(value or "").strip().lower()


def is_individual_applicant(
    applicant_name: str | None,
    applicant_type: Any = None,
) -> bool:
    """True when the applicant must be treated as a private individual.

    ``applicant_type`` may be an ``ApplicantType``, its string value, or None.
    Everything that is not ``displayable_as_corporate`` is an individual,
    whatever the stored type says: the conservative default.
    """
    return not displayable_as_corporate(applicant_name, applicant_type)


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

    Registered (Companies House confirmed) company name first; then an
    applicant name with a legal form and no sole-trader pattern;
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
    company_name = None
    if company is not None and getattr(company, "matched", True):
        company_name = getattr(company, "company_name", None)
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
    "displayable_as_corporate",
    "has_legal_form",
    "has_sole_trader_pattern",
    "is_individual_applicant",
    "looks_corporate",
    "safe_applicant_name",
]
