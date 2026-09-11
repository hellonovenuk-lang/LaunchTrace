"""Is this a packaged-product business, or a restaurant with a trade mark?

The commercial question a packaging supplier is paying to have answered is not
"does this filing contain Nice class 43". Class 43 sits on plenty of genuine
packaged-food filings -- a seaweed manufacturer covering a market stall, an ice
cream maker covering its own parlour -- and is absent from plenty of obvious
restaurants. On the two journals audited, three of the strongest opportunities
carried class 43 and three of the clearest false positives did not. So class 43
is evidence here, worth one point, and nothing more.

What settles it is the evidence read together: what the goods actually are,
what Companies House says the company does, and what the web shows when it can
be attributed to this applicant. A food-manufacturing SIC is the strongest
product evidence available; a restaurant SIC the strongest service evidence;
neither decides alone, because a real manufacturer can run a shop and a shop
can register a manufacturing code it never uses.

Where the evidence does not establish packaged-product relevance, the record is
withheld. A supplier can act on "we did not find one"; they cannot act on a
guess presented as a lead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from src.classify.goods_analysis import GoodsProfile
from src.models import CompanyMatch, WebEnrichment
from src.parse.normalise import normalise_text
from src.settings import load_config


class CommercialMode(str, Enum):
    PACKAGED_PRODUCT = "packaged_product"
    MIXED_BUT_PRODUCT_RELEVANT = "mixed_but_product_relevant"
    FOOD_SERVICE = "food_service"
    NON_PRODUCT = "non_product"
    UNCERTAIN = "uncertain"


@dataclass
class CommercialAssessment:
    mode: CommercialMode = CommercialMode.UNCERTAIN
    product_evidence: int = 0
    service_evidence: int = 0
    product_reasons: list[str] = field(default_factory=list)
    service_reasons: list[str] = field(default_factory=list)
    verdict_reason: str = ""

    @property
    def product_relevant(self) -> bool:
        return self.mode in (
            CommercialMode.PACKAGED_PRODUCT,
            CommercialMode.MIXED_BUT_PRODUCT_RELEVANT,
        )


class CommercialModeAssessor:
    def __init__(self, config: dict | None = None) -> None:
        self.cfg = config or load_config("commercial_mode.json")
        sic = self.cfg["sic_profile"]
        self.manufacturing = tuple(sic["food_manufacturing"])
        self.food_trade = tuple(sic["food_trade"])
        self.food_service = tuple(sic["food_service"])
        self.non_operating = tuple(sic["non_operating"])
        self.pw = self.cfg["product_evidence_weights"]
        self.sw = self.cfg["service_evidence_weights"]
        self.web_hospitality = tuple(self.cfg["web_hospitality_terms"])
        self.web_hospitality_domains = tuple(self.cfg["web_hospitality_domains"])
        self.web_product = tuple(self.cfg["web_product_terms"])
        self.address = re.compile(self.cfg["street_address_pattern"], re.IGNORECASE)
        self.incidental = self.cfg["incidental_food"]

    # -- SIC reading -------------------------------------------------------
    @staticmethod
    def _has(codes: list[str], prefixes: tuple[str, ...]) -> list[str]:
        return [c for c in codes if any(c.strip().startswith(p) for p in prefixes)]

    def _incidental_food(self, profile: GoodsProfile, codes: list[str]) -> str | None:
        """Whether the food lines are a footnote to a non-food proposition.

        A cookware and cookery-book brand that also lists a few prepared meals is
        not a food opportunity. A genuine food brand that also protects T-shirts
        is. The difference is whether packaged food is a substantial part of what
        was filed -- and whether Companies House shows the company making or
        trading food at all, because for a food business merchandise classes are
        ordinary brand protection rather than the point of the filing.
        """
        if not profile.non_food_items or not profile.food_items:
            return None
        if profile.non_food_share < float(self.incidental["non_food_dominant_share"]):
            return None
        food_prefixes = self.manufacturing + self.food_trade
        if self._has(codes, food_prefixes):
            return None
        if profile.food_items >= int(self.incidental["min_food_items_to_survive_without_food_sic"]):
            return None
        return (
            f"{profile.non_food_share:.0%} of the goods are not food or drink "
            f"({', '.join(profile.non_food_hits[:4])}); only {profile.food_items} food items, "
            "and Companies House shows no food manufacturing or trade"
        )

    # -- assessment --------------------------------------------------------
    def assess(
        self,
        profile: GoodsProfile,
        nice_classes: list[int],
        company: CompanyMatch,
        web: WebEnrichment,
    ) -> CommercialAssessment:
        out = CommercialAssessment()
        codes = [c.strip() for c in (company.sic_codes or []) if c.strip()]

        incidental = self._incidental_food(profile, codes)
        if incidental:
            out.mode = CommercialMode.NON_PRODUCT
            out.service_reasons.append(incidental)
            out.verdict_reason = "The food goods are incidental to a predominantly non-food filing."
            return out

        # --- product evidence --------------------------------------------
        mfg = self._has(codes, self.manufacturing)
        if mfg:
            out.product_evidence += self.pw["food_manufacturing_sic"]
            out.product_reasons.append(
                f"Companies House shows food or drink manufacturing ({', '.join(mfg[:3])})"
            )
        trade = self._has(codes, self.food_trade)
        if trade:
            out.product_evidence += self.pw["food_trade_sic"]
            out.product_reasons.append(
                f"Companies House shows food wholesale, import or retail ({', '.join(trade[:3])})"
            )
        if profile.packaged_format_hits:
            out.product_evidence += self.pw["packaged_formats_in_goods"]
            out.product_reasons.append(
                "Goods describe packaged retail formats: "
                + ", ".join(profile.packaged_format_hits[:4])
            )
        if profile.food_share >= float(self.cfg["food_dominant_share"]):
            out.product_evidence += self.pw["food_dominant_goods"]
            out.product_reasons.append(
                f"{profile.food_share:.0%} of the goods are food or drink products"
            )
        if profile.food_items >= int(self.cfg["broad_food_range_items"]):
            out.product_evidence += self.pw["broad_food_range"]
            out.product_reasons.append(f"{profile.food_items} distinct food goods listed")
        if profile.trade_items:
            out.product_evidence += self.pw["trade_services_in_goods"]
            out.product_reasons.append("Filing covers retail or wholesale of its own goods")

        haystack = normalise_text(
            " ".join(
                [
                    web.attributed_text or "",
                    " ".join(web.attributed_urls or []),
                    web.brand_description or "",
                ]
            )
        )
        if web.entity_evidence_available and any(t in haystack for t in self.web_product):
            out.product_evidence += self.pw["corroborated_product_web_evidence"]
            out.product_reasons.append("Corroborated web evidence of a packaged retail range")

        # --- service evidence --------------------------------------------
        service = self._has(codes, self.food_service)
        non_op = self._has(codes, self.non_operating)
        if service:
            out.service_evidence += self.sw["food_service_sic"]
            out.service_reasons.append(
                f"Companies House shows restaurant, café or takeaway activity "
                f"({', '.join(service[:3])})"
            )
        if codes and len(service) + len(non_op) == len(codes):
            # Nothing the company is registered to do involves making or selling
            # a product. That is the single most telling signal there is.
            out.service_evidence += self.sw["all_sic_codes_are_service_or_non_operating"]
            out.service_reasons.append(
                "Every registered activity is food service or a non-trading vehicle"
            )
        elif non_op and not mfg and not trade:
            out.service_evidence += self.sw["non_operating_sic_only"]
            out.service_reasons.append(
                f"Companies House shows a holding, IP or other non-trading activity "
                f"({', '.join(non_op[:3])})"
            )
        if profile.hospitality_items:
            out.service_evidence += self.sw["hospitality_goods"]
            out.service_reasons.append(
                "Goods include serving food and drink: " + ", ".join(profile.hospitality_hits[:4])
            )
        if profile.total and profile.hospitality_items / max(
            profile.food_items + profile.hospitality_items, 1
        ) >= float(self.cfg["hospitality_dominant_share"]):
            out.service_evidence += self.sw["hospitality_dominant_goods"]
            out.service_reasons.append("Service items are a large share of the filing")
        if 43 in nice_classes:
            out.service_evidence += self.sw["class_43_present"]
            out.service_reasons.append(
                "Nice class 43 (food and drink services) is on the filing — evidence, not a bar"
            )
        if web.entity_evidence_available and (
            any(t in haystack for t in self.web_hospitality)
            or any(d in haystack for d in self.web_hospitality_domains)
            or self.address.search(haystack)
        ):
            out.service_evidence += self.sw["corroborated_hospitality_web_evidence"]
            out.service_reasons.append(
                "Corroborated web evidence of a physical outlet, menu or delivery listing"
            )

        # --- verdict ------------------------------------------------------
        floor = int(self.cfg["product_relevant_min_evidence"])
        margin = int(self.cfg["product_must_exceed_service_by"])
        if out.product_evidence >= floor and out.service_evidence == 0:
            out.mode = CommercialMode.PACKAGED_PRODUCT
            out.verdict_reason = "Packaged-product evidence with nothing pointing at food service."
        elif (
            out.product_evidence >= floor and out.product_evidence - out.service_evidence >= margin
        ):
            out.mode = CommercialMode.MIXED_BUT_PRODUCT_RELEVANT
            out.verdict_reason = (
                f"Both product and service evidence, and the product evidence is stronger "
                f"({out.product_evidence} against {out.service_evidence})."
            )
        elif out.service_evidence > out.product_evidence and (
            profile.hospitality_items or service or non_op
        ):
            out.mode = (
                CommercialMode.NON_PRODUCT
                if non_op and not service and not profile.hospitality_items
                else CommercialMode.FOOD_SERVICE
            )
            out.verdict_reason = (
                f"Service evidence outweighs product evidence "
                f"({out.service_evidence} against {out.product_evidence})."
            )
        else:
            out.mode = CommercialMode.UNCERTAIN
            out.verdict_reason = (
                f"Packaged-product relevance was not established "
                f"({out.product_evidence} product against {out.service_evidence} service)."
            )
        return out


_assessor: CommercialModeAssessor | None = None


def get_commercial_mode_assessor() -> CommercialModeAssessor:
    global _assessor
    if _assessor is None:
        _assessor = CommercialModeAssessor()
    return _assessor
