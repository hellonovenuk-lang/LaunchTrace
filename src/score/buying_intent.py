"""Supplier buying-intent mapping.

Reads ``config/buying_intent.json``.  Output is always phrased as inferred
relevance: "flexible packaging relevance: HIGH", never "needs packaging now".
"""

from __future__ import annotations

from src.models import BuyingIntent, LaunchStage, Relevance
from src.settings import load_config


def _shift(band: str, direction: str, order: list[str]) -> str:
    try:
        i = order.index(band)
    except ValueError:
        return band
    if direction == "up":
        i = min(i + 1, len(order) - 1)
    elif direction == "down":
        i = max(i - 1, 0)
    return order[i]


def map_buying_intent(
    product_category: str | None,
    launch_stage: LaunchStage = LaunchStage.UNKNOWN,
    config: dict | None = None,
) -> BuyingIntent:
    cfg = config or load_config("buying_intent.json")
    order: list[str] = cfg["band_order"]
    base: dict[str, str] = dict(cfg["default_relevance"])
    if product_category and product_category in cfg["product_group_relevance"]:
        base.update(cfg["product_group_relevance"][product_category])

    modifiers: dict[str, str] = cfg["stage_modifiers"].get(launch_stage.value, {})
    for key, direction in modifiers.items():
        if key.startswith("_"):
            continue
        if key in base:
            base[key] = _shift(base[key], direction, order)

    return BuyingIntent(**{k: Relevance(v) for k, v in base.items()})


def supplier_category_labels(config: dict | None = None) -> dict[str, str]:
    cfg = config or load_config("buying_intent.json")
    return {c["key"]: c["label"] for c in cfg["supplier_categories"]}
