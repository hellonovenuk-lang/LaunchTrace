"""Launch-stage assessment.

Combines company age at filing with whatever web evidence exists.  When there is
no web evidence the answer is ``UNKNOWN`` -- company age alone is a hint, not a
launch stage.
"""

from __future__ import annotations

from src.models import CompanyMatch, LaunchStage, RetailPresence, WebEnrichment


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
        # Searched and found no brand website at all.
        if company_age_years is not None and company_age_years <= 2.0:
            return LaunchStage.PRE_LAUNCH
        return LaunchStage.UNKNOWN

    # Web enrichment was never attempted (no search provider configured).
    return LaunchStage.UNKNOWN


def resolve_retail_presence(web: WebEnrichment) -> RetailPresence:
    if not web.attempted:
        return RetailPresence.UNKNOWN
    return web.retail_presence
