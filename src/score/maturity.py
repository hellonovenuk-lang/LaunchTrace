"""Launch-stage and brand-maturity assessment.

Two different questions, kept apart on purpose.

``assess_launch_stage`` asks how far along *this launch* looks.  It combines
company age at filing with whatever web evidence exists, and answers ``UNKNOWN``
when there is none -- company age alone is a hint, not a launch stage.

``assess_brand_maturity`` asks how established the *consumer brand* already is.
That is a separate question because a recently incorporated company does not
mean a new brand: a long-standing brand restructuring into a new legal entity
looks identical at Companies House and nothing like it on a supermarket shelf.
Only evidence attributed to this applicant is counted, so an unrelated company
sharing the name cannot make a new brand look established, or the reverse.
"""

from __future__ import annotations

from src.models import BrandMaturity, CompanyMatch, LaunchStage, RetailPresence, WebEnrichment
from src.settings import load_config


def assess_launch_stage(
    company: CompanyMatch, web: WebEnrichment, company_age_years: float | None
) -> LaunchStage:
    if web.attempted and web.major_retailer_presence:
        return LaunchStage.ESTABLISHED
    if web.attempted and web.website_maturity == "established":
        return LaunchStage.ESTABLISHED

    if web.attempted and web.website:
        if web.retail_presence in (RetailPresence.MARKETPLACE, RetailPresence.INDEPENDENT_RETAIL):
            return LaunchStage.SCALING
        if web.products_on_sale:
            return LaunchStage.EARLY_LAUNCH
        if web.launch_evidence:
            return LaunchStage.PRE_LAUNCH
        return LaunchStage.EARLY_LAUNCH

    if web.attempted and not web.website:
        if not web.entity_evidence_available:
            # We searched and learned nothing about this applicant. That is not
            # evidence of a pre-launch brand; it is evidence of nothing.
            return LaunchStage.UNKNOWN
        # Searched, found the brand, found no site of its own.
        if company_age_years is not None and company_age_years <= 2.0:
            return LaunchStage.PRE_LAUNCH
        return LaunchStage.UNKNOWN

    # Web enrichment was never attempted (no search provider configured).
    return LaunchStage.UNKNOWN


def assess_brand_maturity(
    web: WebEnrichment, config: dict | None = None
) -> tuple[BrandMaturity, list[str]]:
    """How established the consumer brand is, with the reasoning attached.

    Returns ``UNKNOWN`` unless there is attributed evidence to reason from, so a
    brand is never called established because of somebody else's footprint, and
    never called emerging merely because a search failed.
    """
    if not web.attempted:
        return BrandMaturity.UNKNOWN, ["Web research did not run for this record."]
    if not web.entity_evidence_available:
        return BrandMaturity.UNKNOWN, [
            "Nothing found on the web could be attributed to this applicant, so how "
            "established the brand is remains unknown."
        ]

    cfg = config or load_config("web_verification.json")["established_brand_evidence"]
    evidence: list[str] = []
    established = False

    retailers = web.distinct_retailers or []
    if len(retailers) >= int(cfg["min_distinct_major_retailers"]):
        established = True
        evidence.append(
            "Already listed by " + ", ".join(retailers[:4]) + " — national retail distribution"
        )
    elif retailers:
        evidence.append("Listed by " + ", ".join(retailers[:4]))

    if web.encyclopaedia_entry:
        established = True
        evidence.append(
            "Has an encyclopaedia entry of its own — a brand nobody has heard of does not "
            f"({web.encyclopaedia_entry})"
        )

    press_floor = int(cfg["min_press_mentions"])
    if web.press_mentions >= press_floor:
        established = True
        evidence.append(f"Covered by {web.press_mentions} separate news or trade publications")
    elif web.press_mentions:
        evidence.append(f"{web.press_mentions} press mention(s) found")

    phrases = web.established_evidence or []
    if len(phrases) >= int(cfg["min_established_phrases"]):
        established = True
        evidence.append("Describes itself in established terms: " + ", ".join(phrases[:4]))
    elif phrases:
        evidence.append("Some established-brand language found: " + ", ".join(phrases[:4]))

    if retailers and phrases:
        established = True

    if established:
        return BrandMaturity.ESTABLISHED, evidence

    if web.launch_evidence:
        evidence.append("Launch-stage signals: " + ", ".join(web.launch_evidence[:4]))
        return BrandMaturity.EMERGING, evidence
    if web.website or web.marketplace_presence:
        evidence.append("A small own-channel footprint and no sign of wide distribution")
        return BrandMaturity.EMERGING, evidence

    evidence.append("Too little evidence either way about how established the brand is.")
    return BrandMaturity.UNKNOWN, evidence


def resolve_retail_presence(web: WebEnrichment) -> RetailPresence:
    if not web.attempted:
        return RetailPresence.UNKNOWN
    return web.retail_presence
