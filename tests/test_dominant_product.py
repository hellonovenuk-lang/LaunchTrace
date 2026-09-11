"""The category a supplier reads must be the product the company actually sells.

Counting raw keyword hits across a whole filing let an incidental word beat the
main line. An ice cream maker came out as a bakery because its list mentioned
"ice cream cakes"; a roasted-nut brand came out as sauces and seasonings
because it mentioned "spiced nuts". Each goods item is now assigned to one
group by the most specific keyword that matches it, with a bonus for matching
the item's head noun, and the dominant group owns the most items.
"""

from __future__ import annotations

import pytest

from src.classify.goods_analysis import GoodsAnalyser


@pytest.fixture
def analyser() -> GoodsAnalyser:
    return GoodsAnalyser()


class TestIncidentalWordsDoNotDecideTheCategory:
    def test_ice_cream_mentioning_cakes_is_not_a_bakery(self, analyser):
        profile = analyser.analyse(
            "Ice cream; Non-dairy ice cream; Ice cream sandwiches; Ice cream cakes; "
            "Ice lollies; Sorbets; Frozen confectionery."
        )
        assert profile.dominant_group == "chilled_frozen"

    def test_nuts_mentioning_spices_are_not_seasonings(self, analyser):
        profile = analyser.analyse(
            "Roasted nuts; Salted nuts; Blanched nuts; Spiced nuts; Dried nuts; "
            "Shelled nuts; Flavoured nuts; Seasoned nuts; Processed nuts; Prepared nuts; "
            "Preserved nuts. Edible spices; Spices."
        )
        assert profile.dominant_group == "snacks"

    def test_ready_meals_mentioning_rice_snacks_are_not_snacks(self, analyser):
        profile = analyser.analyse(
            "Prepared meals consisting principally of meat; prepared meals consisting "
            "principally of poultry; prepared meals consisting principally of fish; "
            "meat-based prepared meals; poultry-based prepared meals; fish-based prepared "
            "meals; vegetable-based prepared meals; prepared rice dishes; rice-based "
            "prepared meals. Rice crisps; rice cakes."
        )
        assert profile.dominant_group == "ambient_meals"


class TestTheHeadNounRuleDoesNotOverreach:
    def test_doughnuts_are_bakery_not_nuts(self, analyser):
        """'Doughnuts' contains 'nuts'. The more specific keyword has to win."""
        profile = analyser.analyse("Doughnuts; Donuts; Filled doughnuts; Chocolate doughnuts.")
        assert profile.dominant_group == "biscuits_bakery"

    def test_peanut_butter_is_a_spread(self, analyser):
        profile = analyser.analyse("Peanut butter; Jams; Marmalade; Honey; Spreads; Chutney.")
        assert profile.dominant_group == "sauces_condiments"

    def test_coconut_is_not_read_as_nuts(self, analyser):
        profile = analyser.analyse("Coconut milk; Oat milk; Plant-based milk; Yoghurt; Cheese.")
        assert profile.dominant_group == "chilled_frozen"


class TestGenuinelyMixedFilings:
    def test_a_spread_filing_says_so_rather_than_guessing(self, analyser):
        profile = analyser.analyse(
            "Chocolate bars; Chocolate confectionery. Biscuits; Cookies. "
            "Coffee; Tea. Sauces; Condiments."
        )
        assert profile.multi_category is True

    def test_a_focused_filing_is_not_called_mixed(self, analyser):
        profile = analyser.analyse(
            "Sauces; Sauces [condiments]; Spicy sauces; Savoury sauces; Ketchup; "
            "Sauce mixes; Condiments; Marinades; Chilli sauce; Ready-made sauces."
        )
        assert profile.multi_category is False
        assert profile.dominant_group == "sauces_condiments"


class TestGoodsProfileShares:
    def test_retail_services_do_not_dilute_the_food_share(self, analyser):
        """Intending to sell through shops is product evidence, not a dilution."""
        profile = analyser.analyse(
            "Ice cream; Sorbets; Frozen confectionery. Retail services relating to food; "
            "Wholesale services in relation to ice creams."
        )
        assert profile.food_share == 1.0

    def test_hospitality_items_are_counted_separately(self, analyser):
        profile = analyser.analyse(
            "Coffee; Tea. Restaurant services; Takeaway food and drink services; Catering."
        )
        assert profile.hospitality_items == 3
        assert profile.food_items == 2

    def test_non_food_goods_are_counted_separately(self, analyser):
        profile = analyser.analyse("T-shirts; hoodies; caps; tote bags. Chocolate; Biscuits.")
        assert profile.non_food_items == 4
        assert profile.food_items == 2
        assert profile.non_food_share > 0.6

    def test_an_empty_filing_is_handled(self, analyser):
        profile = analyser.analyse(None)
        assert profile.total == 0
        assert profile.dominant_group is None
        assert profile.food_share == 0.0
