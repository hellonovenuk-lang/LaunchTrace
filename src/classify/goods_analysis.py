"""Reading a goods and services filing for what the business actually sells.

A trade mark filing is a list of goods, and the list is the most honest
description of a business the register contains.  Three questions are answered
here, all from the same reading:

*What is the dominant product?*  Counting raw keyword hits let an incidental
word beat the main line: an ice cream maker whose list mentions "ice cream
cakes" came out as a bakery, and a roasted-nut brand came out as sauces and
seasonings, because "cake" and "spice" happened to match.  Each goods item is
now assigned to one product group, by the most specific keyword that matches
it, with a bonus for matching the item's head noun -- so "ice cream cakes" is
ice cream and "spiced nuts" is nuts.  The dominant group is then the one that
owns the most items, not the one with the most keyword hits.

*Is this a product business or a service one?*  Items are separated into food
goods, hospitality services (restaurant, parlour, takeaway, catering), trade
services (retail, wholesale, mail order) and plainly non-food goods.  The
proportions say far more than the Nice classes do.

*Is the food incidental?*  A cookware and cookery-book brand that also lists a
few prepared meals is not a food opportunity, and the share of the filing its
food occupies is what shows that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.parse.normalise import normalise_text
from src.settings import load_config

# Goods lists are punctuated with semicolons within a class and full stops
# between classes. Either is an item boundary.
_ITEM_SPLIT = re.compile(r"[;.]")
# Bracketed glosses ("Ramen [Japanese noodle-based dish]") restate the item and
# would otherwise be counted as extra evidence for whatever they mention.
_PARENTHETICAL = re.compile(r"[\[(][^\])]*[\])]")

HEAD_NOUN_BONUS = 3


@dataclass
class GoodsItem:
    text: str
    kind: str  # food | hospitality | trade | non_food | unknown
    group: str | None = None
    matched: str | None = None


@dataclass
class GoodsProfile:
    """What one filing is mostly about."""

    items: list[GoodsItem] = field(default_factory=list)
    group_counts: dict[str, int] = field(default_factory=dict)
    dominant_group: str | None = None
    multi_category: bool = False
    packaged_format_hits: list[str] = field(default_factory=list)
    hospitality_hits: list[str] = field(default_factory=list)
    non_food_hits: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    def count(self, kind: str) -> int:
        return sum(1 for i in self.items if i.kind == kind)

    @property
    def food_items(self) -> int:
        return self.count("food")

    @property
    def hospitality_items(self) -> int:
        return self.count("hospitality")

    @property
    def trade_items(self) -> int:
        return self.count("trade")

    @property
    def non_food_items(self) -> int:
        return self.count("non_food")

    def share(self, kind: str) -> float:
        return self.count(kind) / self.total if self.total else 0.0

    @property
    def food_share(self) -> float:
        """Food weighed against the things that argue it is not a product business.

        Retail and wholesale services are left out of the denominator: intending
        to sell through shops is product evidence, not a dilution of it.
        """
        against = self.food_items + self.hospitality_items + self.non_food_items
        return self.food_items / against if against else 0.0

    @property
    def non_food_share(self) -> float:
        """Non-food goods as a share of the goods -- services excluded either way."""
        goods = self.food_items + self.non_food_items
        return self.non_food_items / goods if goods else 0.0


def _head_noun(item: str) -> str:
    words = item.split()
    return words[-1] if words else ""


class GoodsAnalyser:
    def __init__(self, taxonomy: dict | None = None, config: dict | None = None) -> None:
        self.taxonomy = taxonomy or load_config("food_taxonomy.json")
        self.cfg = config or load_config("commercial_mode.json")
        self.groups = [
            (g["key"], tuple(sorted(g["keywords"], key=len, reverse=True)))
            for g in self.taxonomy["product_groups"]
        ]
        self.hospitality = tuple(self.cfg["hospitality_goods_terms"])
        self.trade = tuple(self.cfg["trade_goods_terms"])
        self.formats = tuple(self.cfg["packaged_format_terms"])
        self.non_food: tuple[str, ...] = tuple(
            term
            for key, terms in self.cfg["non_food_goods_terms"].items()
            if not key.startswith("_")
            for term in terms
        )

    # -- item classification ----------------------------------------------
    def _match_group(self, item: str) -> tuple[str | None, str | None]:
        """The product group that best explains this one item.

        Longest keyword wins, because a longer keyword is a more specific
        reading, and a keyword covering the item's head noun is worth more than
        one covering a modifier. That is the whole difference between reading
        "ice cream cakes" as ice cream and reading it as cake.
        """
        head = _head_noun(item)
        best: tuple[int, str, str] | None = None
        for key, keywords in self.groups:
            for kw in keywords:
                if kw not in item:
                    continue
                score = len(kw) + (HEAD_NOUN_BONUS if head and kw in head else 0)
                if best is None or score > best[0]:
                    best = (score, key, kw)
                break  # keywords are length-ordered, so the first hit is the longest
        # A shorter keyword elsewhere in the list may still win on the head-noun
        # bonus, so every group's best candidate has to be compared.
        for key, keywords in self.groups:
            for kw in keywords:
                if kw not in item:
                    continue
                score = len(kw) + (HEAD_NOUN_BONUS if head and kw in head else 0)
                if best is None or score > best[0]:
                    best = (score, key, kw)
        return (best[1], best[2]) if best else (None, None)

    def classify_item(self, raw: str) -> GoodsItem:
        item = normalise_text(_PARENTHETICAL.sub(" ", raw))
        if not item:
            return GoodsItem(text=raw.strip(), kind="unknown")
        if any(term in item for term in self.hospitality):
            return GoodsItem(text=item, kind="hospitality")
        if any(term in item for term in self.trade):
            return GoodsItem(text=item, kind="trade")
        group, matched = self._match_group(item)
        if group:
            return GoodsItem(text=item, kind="food", group=group, matched=matched)
        if any(term in item for term in self.non_food):
            return GoodsItem(text=item, kind="non_food")
        return GoodsItem(text=item, kind="unknown")

    # -- whole filing ------------------------------------------------------
    def analyse(self, goods_text: str | None) -> GoodsProfile:
        profile = GoodsProfile()
        if not goods_text:
            return profile
        for raw in _ITEM_SPLIT.split(goods_text):
            if not raw.strip():
                continue
            profile.items.append(self.classify_item(raw))

        counts: dict[str, int] = {}
        for item in profile.items:
            if item.group:
                counts[item.group] = counts.get(item.group, 0) + 1
        profile.group_counts = counts
        if counts:
            top = max(counts.values())
            leaders = sorted(k for k, v in counts.items() if v == top)
            total_grouped = sum(counts.values())
            share = top / total_grouped if total_grouped else 0.0
            if len(leaders) > 1 or share < 0.4:
                # Genuinely spread across categories. Saying so is more useful to
                # a supplier than confidently naming the wrong one.
                profile.multi_category = True
                profile.dominant_group = leaders[0]
            else:
                profile.dominant_group = leaders[0]

        corpus = normalise_text(goods_text)
        profile.packaged_format_hits = sorted({f for f in self.formats if f in corpus})[:8]
        profile.hospitality_hits = sorted({h for h in self.hospitality if h in corpus})[:8]
        profile.non_food_hits = sorted({n for n in self.non_food if n in corpus})[:8]
        return profile


_analyser: GoodsAnalyser | None = None


def get_goods_analyser() -> GoodsAnalyser:
    global _analyser
    if _analyser is None:
        _analyser = GoodsAnalyser()
    return _analyser
