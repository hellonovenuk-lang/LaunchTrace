"""What LaunchTrace costs to run, and what the margin is at £79.

Every figure comes from ``config/costs.json`` and every one carries a
confidence. Nothing here fetches live vendor pricing, and nothing invents a
price: an item marked ``assumed`` is an assumption and the report says so on
the line where it appears.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.settings import load_config


@dataclass
class CostLine:
    key: str
    label: str
    monthly_pence: float
    basis: str
    confidence: str
    source_note: str

    @property
    def assumed(self) -> bool:
        return self.confidence != "known"


@dataclass
class CostEstimate:
    """Monthly cost, split by what drives it."""

    fixed_pence: float = 0.0
    per_run_pence: float = 0.0
    per_customer_pence: float = 0.0
    customers: int = 0
    lines: list[CostLine] = field(default_factory=list)
    plan_price_pence: int = 7900
    runs_per_month: float = 4.33

    @property
    def weekly_run_pence(self) -> float:
        return round(self.per_run_pence / self.runs_per_month, 2) if self.runs_per_month else 0.0

    @property
    def total_monthly_pence(self) -> float:
        return round(self.fixed_pence + self.per_run_pence + self.per_customer_pence, 2)

    @property
    def per_customer_monthly_pence(self) -> float:
        """Total cost divided by paying customers. Undefined at zero customers."""
        if not self.customers:
            return 0.0
        return round(self.total_monthly_pence / self.customers, 2)

    @property
    def monthly_revenue_pence(self) -> float:
        return float(self.customers * self.plan_price_pence)

    @property
    def gross_margin_pence(self) -> float:
        return round(self.monthly_revenue_pence - self.total_monthly_pence, 2)

    @property
    def gross_margin_percent(self) -> float | None:
        if not self.monthly_revenue_pence:
            return None
        return round(100 * self.gross_margin_pence / self.monthly_revenue_pence, 1)

    @property
    def breakeven_customers(self) -> int:
        """Customers needed to cover cost, given that each one adds cost too."""
        marginal = self.per_customer_pence / self.customers if self.customers else 0.0
        contribution = self.plan_price_pence - marginal
        if contribution <= 0:  # pragma: no cover - only if a plan costs more than it earns
            return 0
        overhead = self.fixed_pence + self.per_run_pence
        return max(1, int(-(-overhead // contribution)))

    @property
    def assumed_lines(self) -> list[CostLine]:
        return [line for line in self.lines if line.assumed]


def estimate_costs(
    customers: int = 0,
    config: dict | None = None,
    llm_enabled: bool = False,
    search_enabled: bool = False,
) -> CostEstimate:
    """Estimate monthly cost at a given customer count.

    ``llm_enabled`` and ``search_enabled`` reflect what is actually configured:
    an unconnected provider costs nothing, and pretending otherwise would make
    the margin look worse than it is.
    """
    cfg = config or load_config("costs.json")
    runs = float(cfg.get("runs_per_month", 4.33))
    estimate = CostEstimate(
        customers=customers,
        plan_price_pence=int(cfg.get("plan_price_pence", 7900)),
        runs_per_month=runs,
    )

    for item in cfg["line_items"]:
        key = item["key"]
        if key == "llm_classification" and not llm_enabled:
            continue
        if key.startswith("search_enrichment") and not search_enabled:
            continue

        scales = item.get("scales_with", "fixed")
        fixed = float(item.get("monthly_fixed_pence", 0))
        monthly = fixed
        basis = "fixed monthly"

        if scales == "run":
            per_run = float(item.get("pence_per_unit", 0)) * float(
                item.get("units_per_weekly_run", 0)
            )
            monthly += per_run * runs
            basis = f"{item.get('units_per_weekly_run', 0)} × {item['unit']} per run"
            estimate.per_run_pence += per_run * runs
        elif scales == "customer":
            per_customer = float(item.get("pence_per_unit", 0)) * float(
                item.get("units_per_customer_per_month", 0)
            )
            percent = float(item.get("percent_of_revenue", 0))
            revenue_share = estimate.plan_price_pence * percent / 100.0
            per_customer += revenue_share
            monthly += per_customer * customers
            basis = f"per customer per month ({item['unit']})"
            estimate.per_customer_pence += per_customer * customers
        else:
            estimate.fixed_pence += fixed

        estimate.lines.append(
            CostLine(
                key=key,
                label=item["label"],
                monthly_pence=round(monthly, 2),
                basis=basis,
                confidence=item.get("confidence", "assumed"),
                source_note=item.get("source_note", ""),
            )
        )

    estimate.fixed_pence = round(estimate.fixed_pence, 2)
    estimate.per_run_pence = round(estimate.per_run_pence, 2)
    estimate.per_customer_pence = round(estimate.per_customer_pence, 2)
    return estimate


def format_pounds(pence: float) -> str:
    return f"£{pence / 100:,.2f}"


__all__ = ["CostEstimate", "CostLine", "estimate_costs", "format_pounds"]
