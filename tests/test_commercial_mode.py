"""Packaged-product business, or a restaurant with a trade mark?

Nice class 43 is the obvious rule and the wrong one. On the two journals
audited, three of the strongest opportunities carried class 43 — a seaweed
manufacturer, an ice cream maker, a nut packer, all covering a stall or a
counter — and three of the clearest false positives did not. These tests pin
the shape of that evidence rather than the classes, so the rule cannot quietly
collapse back into "class 43 means no".
"""

from __future__ import annotations

import pytest

from src.classify.commercial_mode import CommercialMode, CommercialModeAssessor
from src.classify.goods_analysis import GoodsAnalyser
from tests.conftest import make_company, make_web


@pytest.fixture
def assessor() -> CommercialModeAssessor:
    return CommercialModeAssessor()


@pytest.fixture
def analyser() -> GoodsAnalyser:
    return GoodsAnalyser()


def assess(assessor, analyser, goods, classes, sic, web=None):  # type: ignore[no-untyped-def]
    profile = analyser.analyse(goods)
    company = make_company(sic_codes=sic)
    return assessor.assess(profile, classes, company, web or make_web(attempted=False))


class TestClass43IsEvidenceNotADisqualifier:
    def test_a_manufacturer_covering_its_own_counter_survives_class_43(self, assessor, analyser):
        """A food manufacturer does not stop being one by also selling direct."""
        out = assess(
            assessor,
            analyser,
            "Seaweed; Edible seaweed; Dried edible seaweed; Snacks of edible seaweed; "
            "Processed edible seaweed; Prepared seaweed; Kelp; Dried kelps; Toasted laver; "
            "Sheets of dried laver; Seasoned laver; Dried seafood; Seaweed-based snack food. "
            "Retail services relating to food. Snack bar services.",
            [29, 30, 35, 43],
            ["10890", "46390"],
        )
        assert out.product_relevant
        assert out.mode == CommercialMode.MIXED_BUT_PRODUCT_RELEVANT

    def test_an_ice_cream_maker_with_a_parlour_survives(self, assessor, analyser):
        out = assess(
            assessor,
            analyser,
            "Ice cream; Non-dairy ice cream; Ice cream sandwiches; Ice cream cakes; "
            "Ice lollies; Sorbets; Frozen confectionery. Wholesale services in relation "
            "to ice creams. Ice cream parlour services.",
            [29, 30, 35, 43],
            ["10520"],
        )
        assert out.product_relevant

    def test_a_cafe_is_rejected_even_though_its_goods_are_food(self, assessor, analyser):
        """Same class 43, opposite answer — the SIC codes and the goods decide."""
        out = assess(
            assessor,
            analyser,
            "Coffee drinks; Cocoa drinks; Coffee beverages with milk; Tea beverages; "
            "Crepes; Pastry; Tea; Iced tea. Preparation of food and drink; "
            "Takeaway food and drink services.",
            [30, 43],
            ["56102", "56103"],
        )
        assert not out.product_relevant
        assert out.mode == CommercialMode.FOOD_SERVICE


class TestServiceBusinessesWithoutClass43:
    def test_a_takeaway_is_rejected_on_its_registered_activity_alone(self, assessor, analyser):
        """No class 43 at all — the register says what this business is."""
        out = assess(
            assessor,
            analyser,
            "Sushi; Ramen; Ramen noodles; Gyoza; Onigiri; Soba noodles; Udon noodles; "
            "Rice salad; Shrimp dumplings.",
            [30],
            ["56290"],
        )
        assert not out.product_relevant

    def test_the_same_goods_from_a_food_retailer_are_a_product_opportunity(
        self, assessor, analyser
    ):
        """The discriminator has to be the business, not the menu."""
        out = assess(
            assessor,
            analyser,
            "Sushi; Ramen; Ramen noodles; Gyoza; Onigiri; Soba noodles; Udon noodles; "
            "Rice salad; Shrimp dumplings; Chilled prepared meals; Packaged sushi.",
            [29, 30],
            ["47290"],
        )
        assert out.product_relevant

    def test_an_ip_holding_vehicle_is_not_a_packaged_food_opportunity(self, assessor, analyser):
        out = assess(
            assessor,
            analyser,
            "Tote bags; shopping bags; backpacks; rucksacks; holdalls. Clothing; "
            "T-shirts; hoodies; jackets; aprons; caps; hats; socks. Processed potatoes; "
            "french fries; potato-based snack foods; potato crisps.",
            [18, 25, 29, 30],
            ["77400"],
        )
        assert not out.product_relevant

    def test_a_shop_gives_itself_away_through_corroborated_web_evidence(self, assessor, analyser):
        """Goods and SIC both inconclusive; the web shows an address and a menu."""
        web = make_web(
            attempted=True,
            attributed_urls=["https://example.test/a"],
            attributed_text=(
                "Sweet Spot, 17 Old Dumbarton Road, Glasgow. Menu: frozen yogurt "
                "loaded with toppings, milkshakes and acai bowls."
            ),
        )
        out = assess(
            assessor,
            analyser,
            "Yogurt; Desserts of yogurt. Frozen yogurt; Dairy ice cream; Ice cream desserts; "
            "Ice cream cones. Retail services in relation to frozen yogurts.",
            [29, 30, 32, 35],
            ["96090"],
            web=web,
        )
        assert not out.product_relevant


class TestGenuinePackagedProducts:
    @pytest.mark.parametrize(
        "goods,classes,sic",
        [
            (
                "Sauces; Sauces [condiments]; Spicy sauces; Ketchup; Sauce mixes; Condiments; "
                "Chilli sauce; Savoury sauces; Ready-made sauces; Marinades.",
                [30],
                ["47290"],
            ),
            ("Energy Bar.", [29], ["46360"]),
            (
                "Seasoning marinade; Seasoning mixes; Popcorn seasoning; Seasonings; Spices; "
                "Dry seasonings; Blends of seasonings; Seasoned salt.",
                [30],
                ["46380"],
            ),
            ("Tea; Black tea; Tea leaves; Chai tea; Tea bags; Teas.", [30], ["46390"]),
        ],
    )
    def test_focused_food_filings_are_product_relevant(
        self, assessor, analyser, goods, classes, sic
    ):
        assert assess(assessor, analyser, goods, classes, sic).product_relevant

    def test_the_verdict_is_explainable(self, assessor, analyser):
        out = assess(assessor, analyser, "Sauces; Condiments; Ketchup; Spices.", [30], ["10840"])
        assert out.verdict_reason
        assert out.product_reasons


class TestIncidentalFood:
    def test_a_cookware_and_media_brand_is_not_a_food_opportunity(self, assessor, analyser):
        out = assess(
            assessor,
            analyser,
            "Knives; Kitchen knives; Tongs. Cookery books; Recipe books; Posters; "
            "Calendars; Stationery. Cookware; Bakeware; Cooking utensils; Frying pans; "
            "Chopping boards; Tableware; Dinnerware. Clothing; T-shirts; Aprons; Caps. "
            "Prepared meat dishes; Prepared vegetable dishes.",
            [8, 16, 21, 25, 29, 30],
            ["59112"],
        )
        assert not out.product_relevant

    def test_a_homeware_gifting_brand_with_a_chocolate_line_is_suppressed(self, assessor, analyser):
        out = assess(
            assessor,
            analyser,
            "Body lotions; Reed diffusers; Soap. Candles; Scented candles. Throws; "
            "Bed throws; Pillowcases; Covers for pillows. Chocolates; Chocolate biscuits; "
            "Biscuits; Tea; Coffee.",
            [3, 4, 24, 30],
            ["46499"],
        )
        assert out.mode == CommercialMode.NON_PRODUCT

    def test_a_food_company_keeps_its_merchandise_classes(self, assessor, analyser):
        """Real food brands protect T-shirts too. That must not cost them the feed."""
        out = assess(
            assessor,
            analyser,
            "Tote bags. Lunchboxes; Serving dishes; Serving spoons. Caps. "
            "Condiments; Food dressings; Sauces.",
            [18, 21, 25, 30],
            ["10840", "46390"],
        )
        assert out.product_relevant

    def test_a_coffee_brand_filing_mugs_and_clothing_survives(self, assessor, analyser):
        out = assess(
            assessor,
            analyser,
            "Drinkware; Mugs; Coffee mugs; Travel mugs; Tumblers; Water bottles. "
            "Clothing; T-shirts; hoodies; caps. Coffee; coffee beans; roasted coffee beans; "
            "ground coffee; coffee bags; coffee pods; coffee capsules.",
            [21, 25, 30],
            ["46370"],
        )
        assert out.product_relevant
