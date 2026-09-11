"""Supplements are not packaged food, whatever classes they were filed in.

The failure being regression-tested: a supplement brand files in Nice 5, 29 and
30, its goods text mentions honey and spreads in passing, and the keyword filter
hands a packaging supplier a vitamin company categorised as "sauces, condiments
and spreads". Class membership cannot decide what a product is; the goods and
services text can.
"""

from __future__ import annotations

from src.classify.food_filter import FoodFilter
from tests.conftest import make_record


class TestSupplementsAreOutOfScope:
    def test_a_supplement_filing_is_not_given_a_food_category(self, food_filter: FoodFilter):
        """The observed failure, in the shape it actually arrived in."""
        outcome = food_filter.assess(
            make_record(
                mark_text="VITAFLORA",
                nice_classes=[5, 29, 30],
                goods_text=(
                    "Dietary supplements for humans; food supplements; vitamin preparations; "
                    "mineral supplements; micronutrient preparations; probiotic supplements; "
                    "propolis for food purposes; royal jelly for dietary purposes; honey."
                ),
            )
        )
        assert outcome.candidate is False
        assert outcome.rejection_reason == "out_of_scope_product"
        assert outcome.assessment.product_category == "supplements_nutraceuticals"
        assert (
            outcome.assessment.product_category_label
            != "Sauces, condiments, spreads and seasonings"
        )

    def test_an_explicit_supplement_marker_with_the_pharmaceutical_class_is_decisive(
        self, food_filter: FoodFilter
    ):
        outcome = food_filter.assess(
            make_record(
                nice_classes=[5, 30],
                goods_text=(
                    "Nutritional supplements; cereal bars; oat bars; snack bars; biscuits; "
                    "cereal preparations."
                ),
            )
        )
        assert outcome.candidate is False
        assert outcome.rejection_reason == "out_of_scope_product"

    def test_pet_products_are_out_of_scope(self, food_filter: FoodFilter):
        outcome = food_filter.assess(
            make_record(
                nice_classes=[29, 31],
                goods_text="Pet food; dog treats; foodstuffs for animals; edible chews for animals.",
            )
        )
        assert outcome.candidate is False

    def test_cosmetics_are_out_of_scope(self, food_filter: FoodFilter):
        outcome = food_filter.assess(
            make_record(
                nice_classes=[3, 29],
                goods_text="Cosmetics; skincare preparations; body lotion; soaps; edible oils.",
            )
        )
        assert outcome.candidate is False


class TestRealFoodStillPasses:
    def test_an_ordinary_snack_filing_is_unaffected(self, food_filter: FoodFilter):
        outcome = food_filter.assess(make_record())
        assert outcome.candidate is True
        assert outcome.assessment.product_category == "cereal_bars"

    def test_a_food_brand_that_mentions_one_supplement_word_is_not_thrown_away(
        self, food_filter: FoodFilter
    ):
        """A protein flapjack is food. One passing word must not lose the record."""
        outcome = food_filter.assess(
            make_record(
                mark_text="OATFORGE",
                nice_classes=[30],
                goods_text=(
                    "Cereal bars; protein bars; oat bars; flapjacks; granola; muesli; "
                    "breakfast cereal; biscuits; snack bars containing amino acids."
                ),
            )
        )
        assert outcome.candidate is True
        assert outcome.assessment.product_category in {"cereal_bars", "cereal_granola"}

    def test_a_functional_food_powder_is_still_food_when_the_text_says_so(
        self, food_filter: FoodFilter
    ):
        outcome = food_filter.assess(
            make_record(
                mark_text="GREENSDAY",
                nice_classes=[29, 30],
                goods_text=(
                    "Greens powder for food purposes; superfood powders; functional food "
                    "preparations; nutritional shakes; smoothie powders; seeds, prepared."
                ),
            )
        )
        assert outcome.candidate is True

    def test_honey_and_spreads_alone_still_classify_as_condiments(self, food_filter: FoodFilter):
        outcome = food_filter.assess(
            make_record(
                mark_text="HIVEWORKS",
                nice_classes=[30],
                goods_text="Honey; jams; marmalade; preserves; spreads; syrup.",
            )
        )
        assert outcome.candidate is True
        assert outcome.assessment.product_category == "sauces_condiments"


class TestWithoutGoodsText:
    def test_no_goods_text_means_no_out_of_scope_guess(self, food_filter: FoodFilter):
        """Class assumptions are exactly what this is meant to replace.

        With no goods text there is no product evidence either way, so the
        record stays a candidate and the scorer's cap handles the thin evidence.
        """
        outcome = food_filter.assess(
            make_record(nice_classes=[5, 30], goods_text=None, goods_text_available=False)
        )
        assert outcome.candidate is True
