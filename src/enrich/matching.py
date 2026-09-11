"""Company name matching.

Trade mark applicant names and Companies House registered names rarely match
character for character.  This module normalises both sides, scores candidates,
and -- importantly -- refuses to claim a match it is not confident about.  An
uncertain match is recorded as uncertain, not silently promoted.

The rule that matters most here is about *partial* names.  A company whose
distinctive words are a subset of the applicant's used to score as a strong
match however small that subset was, so "Melissa Bent" matched "MELISSA 27
LIMITED" and "The Secretary of State for Defence" matched "SECRETARY LTD".
Those are not near-misses to be shown with a caveat: they attach a stranger's
company number, registered office and incorporation date to the record, and the
incorporation date is what the score reads as the brand's age.  A shared word is
now weighed against how much of the whole name it accounts for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.models import CompanyMatch
from src.parse.normalise import (
    company_name_key,
    normalise_company_name,
    normalise_text,
    similarity,
)

EXACT_CONFIDENCE = 98
NORMALISED_CONFIDENCE = 92
TOKEN_SET_CONFIDENCE = 88
STRONG_TOKEN_CONFIDENCE = 78
WEAK_TOKEN_CONFIDENCE = 55
MIN_ACCEPTABLE_CONFIDENCE = 55

# How much of the longer name a subset must account for before it is a match
# rather than a shared word. Below this, an extra distinctive word means a
# different company on the register, and usually is one.
SUBSET_STRONG_SIMILARITY = 0.6

# Words that carry no distinguishing power, so a match on them alone is not one.
STOPWORDS = {"the", "and", "of", "uk", "gb", "great", "britain", "london", "group", "holdings"}


@dataclass
class CandidateCompany:
    """A Companies House record being considered as a match."""

    company_name: str
    company_number: str
    company_status: str | None = None
    company_category: str | None = None
    incorporation_date: date | None = None
    dissolution_date: date | None = None
    sic_codes: tuple[str, ...] = ()
    region: str | None = None
    post_town: str | None = None
    country: str | None = None
    accounts_category: str | None = None
    source_url: str | None = None


def distinctive_tokens(name: str | None) -> set[str]:
    return {t for t in normalise_company_name(name).split() if t not in STOPWORDS and len(t) > 2}


def score_candidate(applicant_name: str, candidate: CandidateCompany) -> tuple[int, str, list[str]]:
    """Return (confidence 0-100, method, evidence)."""
    evidence: list[str] = []
    a_raw = normalise_text(applicant_name)
    c_raw = normalise_text(candidate.company_name)
    if a_raw and a_raw == c_raw:
        return (
            EXACT_CONFIDENCE,
            "exact_name",
            ["Applicant name matches the registered name exactly"],
        )

    a_key, c_key = company_name_key(applicant_name), company_name_key(candidate.company_name)
    if a_key and a_key == c_key:
        return (
            NORMALISED_CONFIDENCE,
            "normalised_name",
            ["Names match once legal suffixes and punctuation are removed"],
        )

    a_tokens, c_tokens = (
        distinctive_tokens(applicant_name),
        distinctive_tokens(candidate.company_name),
    )
    if not a_tokens or not c_tokens:
        return 0, "no_signal", ["Applicant name has no distinctive tokens to match on"]

    jaccard = similarity(applicant_name, candidate.company_name)
    overlap = a_tokens & c_tokens
    evidence.append(
        f"Shared distinctive words: {', '.join(sorted(overlap)) or 'none'} "
        f"(token similarity {jaccard:.2f})"
    )

    if a_tokens == c_tokens:
        return (
            TOKEN_SET_CONFIDENCE,
            "token_set_match",
            evidence + ["Every distinctive word matches, in either order"],
        )

    if a_tokens < c_tokens or c_tokens < a_tokens:
        # A subset is only worth as much as the share of the longer name it
        # accounts for. One word in common is a coincidence, not an identity:
        # treating it as one attaches a stranger's incorporation date, company
        # number and SIC codes to this applicant, and everything downstream then
        # reasons about the wrong company.
        if jaccard >= SUBSET_STRONG_SIMILARITY:
            return STRONG_TOKEN_CONFIDENCE, "token_subset", evidence
        missing = sorted((a_tokens | c_tokens) - (a_tokens & c_tokens))
        return (
            int(jaccard * 60),
            "partial_name_only",
            evidence
            + [
                f"Rejected: '{candidate.company_name}' shares only part of the applicant's name "
                f"(missing {', '.join(missing)}), which is a different registered company"
            ],
        )

    if jaccard >= 0.7:
        return STRONG_TOKEN_CONFIDENCE, "token_similarity", evidence
    if jaccard >= 0.5:
        return WEAK_TOKEN_CONFIDENCE, "weak_token_similarity", evidence
    return int(jaccard * 60), "insufficient_similarity", evidence


def best_match(
    applicant_name: str | None,
    candidates: list[CandidateCompany],
    provider: str,
    min_confidence: int = MIN_ACCEPTABLE_CONFIDENCE,
) -> CompanyMatch:
    """Pick the best candidate, or return an explicit no-match."""
    if not applicant_name:
        return CompanyMatch(
            matched=False,
            match_method="no_applicant_name",
            provider=provider,
            match_evidence=["Record has no applicant name"],
        )
    if not candidates:
        return CompanyMatch(
            matched=False,
            match_method="no_candidates",
            provider=provider,
            candidates_considered=0,
            match_evidence=["No Companies House candidates returned for this applicant name"],
        )

    scored = [(score_candidate(applicant_name, c), c) for c in candidates]
    scored.sort(key=lambda pair: pair[0][0], reverse=True)
    (confidence, method, evidence), best = scored[0]

    # An ambiguous top-2 is a reason to be less confident, not more.
    if len(scored) > 1:
        runner_up = scored[1][0][0]
        if confidence - runner_up < 5 and confidence < EXACT_CONFIDENCE:
            confidence = max(0, confidence - 15)
            evidence.append(
                f"Ambiguous: {len(scored)} candidates scored within 5 points, confidence reduced"
            )

    if confidence < min_confidence:
        return CompanyMatch(
            matched=False,
            match_method=method,
            match_confidence=confidence,
            candidates_considered=len(candidates),
            provider=provider,
            match_evidence=evidence
            + [
                f"Best candidate '{best.company_name}' scored {confidence}, below the {min_confidence} threshold"
            ],
        )

    return CompanyMatch(
        matched=True,
        company_name=best.company_name,
        company_number=best.company_number,
        company_status=best.company_status,
        company_category=best.company_category,
        incorporation_date=best.incorporation_date,
        dissolution_date=best.dissolution_date,
        sic_codes=list(best.sic_codes),
        region=best.region,
        post_town=best.post_town,
        country=best.country,
        accounts_category=best.accounts_category,
        match_confidence=confidence,
        match_method=method,
        match_evidence=evidence,
        candidates_considered=len(candidates),
        provider=provider,
        source_url=best.source_url
        or (
            f"https://find-and-update.company-information.service.gov.uk/company/{best.company_number}"
            if best.company_number
            else None
        ),
    )
