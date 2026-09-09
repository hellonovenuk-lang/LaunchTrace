"""Commercial metrics: the sales funnel, the product, and the operation.

A reporting command, not a dashboard. Everything is computed from the prospect
CSV, the database and the configuration each time it runs, so there is no
derived state to fall out of date.

Rates are reported with their denominator, and a rate on a denominator below
five is marked as such. Ten prospects and one reply is not a 10% reply rate,
and presenting it as one is how a founder talks themselves into a bad decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.customer_lifecycle import delivery_allowed, delivery_block_reason
from src.db.tables import Customer, Delivery, ErrorLog, LeadFeedback, OpportunityRow, PipelineRun
from src.sales.costs import CostEstimate, estimate_costs
from src.sales.feedback import FeedbackSummary, summarise_feedback
from src.sales.models import Priority, Prospect, ProspectStatus
from src.settings import Settings, get_settings, load_config

# Below this many observations a percentage is noise, not a rate.
MIN_MEANINGFUL_DENOMINATOR = 5


@dataclass
class Rate:
    numerator: int
    denominator: int
    label: str = ""

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def meaningful(self) -> bool:
        return self.denominator >= MIN_MEANINGFUL_DENOMINATOR

    def display(self) -> str:
        if not self.denominator:
            return "—"
        percent = f"{100 * (self.value or 0):.0f}%"
        detail = f"{percent} ({self.numerator}/{self.denominator})"
        return detail if self.meaningful else f"{detail} — too few to read as a rate"


@dataclass
class FunnelMetrics:
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_priority: dict[str, int] = field(default_factory=dict)
    qualified: int = 0
    email_1_sent: int = 0
    replies: int = 0
    sample_requests: int = 0
    samples_sent: int = 0
    offers_sent: int = 0
    subscribed: int = 0
    opted_out: int = 0
    suppressed: int = 0

    @property
    def reply_rate(self) -> Rate:
        return Rate(self.replies, self.email_1_sent, "replies per first email")

    @property
    def sample_rate(self) -> Rate:
        return Rate(self.samples_sent, self.email_1_sent, "samples per first email")

    @property
    def conversion_rate(self) -> Rate:
        return Rate(self.subscribed, self.samples_sent, "subscriptions per sample sent")

    @property
    def end_to_end_rate(self) -> Rate:
        return Rate(self.subscribed, self.email_1_sent, "subscriptions per first email")


@dataclass
class ProductMetrics:
    opportunities_total: int = 0
    high: int = 0
    medium: int = 0
    weeks_covered: int = 0
    per_week: float = 0.0
    feedback: FeedbackSummary = field(default_factory=FeedbackSummary)

    @property
    def usefulness(self) -> Rate:
        return Rate(self.feedback.positive, self.feedback.total, "leads called useful")

    @property
    def false_positive_signals(self) -> int:
        """Feedback that says the lead should not have been in the feed."""
        return sum(
            self.feedback.by_state.get(state, 0)
            for state in ("NOT_RELEVANT", "TOO_ESTABLISHED", "ALREADY_KNOWN")
        )


@dataclass
class OperationalMetrics:
    last_run_id: str = ""
    last_run_status: str = "none"
    last_run_journal: str = ""
    last_run_at: str = ""
    runs_total: int = 0
    runs_failed: int = 0
    deliveries_sent: int = 0
    deliveries_rendered_only: int = 0
    deliveries_failed: int = 0
    active_customers: int = 0
    past_due_customers: int = 0
    cancelled_customers: int = 0
    blocked_customers: list[tuple[str, str]] = field(default_factory=list)
    recent_errors: int = 0
    send_mode: str = "review"
    email_live: bool = False
    stripe_live: bool = False
    search_live: bool = False


@dataclass
class BusinessStatus:
    funnel: FunnelMetrics
    product: ProductMetrics
    operations: OperationalMetrics
    costs: CostEstimate
    mrr_pence: int = 0
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def mrr_display(self) -> str:
        return f"£{self.mrr_pence / 100:,.2f}"


def funnel_metrics(prospects: list[Prospect]) -> FunnelMetrics:
    """Count the funnel from the prospect list.

    Counts are cumulative by date stamp, not by current status: a prospect who
    is now SUBSCRIBED still counts as having been sent a first email.
    """
    metrics = FunnelMetrics(total=len(prospects))
    for prospect in prospects:
        metrics.by_status[prospect.status.value] = (
            metrics.by_status.get(prospect.status.value, 0) + 1
        )
        metrics.by_priority[prospect.priority.value] = (
            metrics.by_priority.get(prospect.priority.value, 0) + 1
        )
        if prospect.priority in {Priority.A, Priority.B} and prospect.contactable:
            metrics.qualified += 1
        if prospect.email_1_sent_date:
            metrics.email_1_sent += 1
        if prospect.status.rank >= ProspectStatus.REPLIED_INTERESTED.rank and (
            prospect.status
            not in {
                ProspectStatus.NO_RESPONSE,
                ProspectStatus.OPTED_OUT,
                ProspectStatus.SUPPRESSED,
            }
        ):
            metrics.replies += 1
        if prospect.sample_requested_date:
            metrics.sample_requests += 1
        if prospect.sample_sent_date:
            metrics.samples_sent += 1
        if prospect.offer_sent_date:
            metrics.offers_sent += 1
        if prospect.converted_date or prospect.status == ProspectStatus.SUBSCRIBED:
            metrics.subscribed += 1
        if prospect.opted_out:
            metrics.opted_out += 1
        if prospect.status == ProspectStatus.SUPPRESSED:
            metrics.suppressed += 1
    return metrics


def product_metrics(session: Session) -> ProductMetrics:
    rows = list(session.execute(select(OpportunityRow)).scalars())
    deliverable = [r for r in rows if not r.suppressed and r.score_band in {"HIGH", "MEDIUM"}]
    weeks = {r.journal_number for r in rows if r.journal_number}
    metrics = ProductMetrics(
        opportunities_total=len(deliverable),
        high=sum(1 for r in deliverable if r.score_band == "HIGH"),
        medium=sum(1 for r in deliverable if r.score_band == "MEDIUM"),
        weeks_covered=len(weeks),
        feedback=summarise_feedback(session),
    )
    metrics.per_week = round(len(deliverable) / len(weeks), 2) if weeks else 0.0
    return metrics


def operational_metrics(session: Session, settings: Settings | None = None) -> OperationalMetrics:
    settings = settings or get_settings()
    metrics = OperationalMetrics(
        send_mode=settings.send_mode,
        email_live=settings.email_enabled,
        stripe_live=settings.stripe_enabled,
        search_live=settings.search_enabled,
    )

    runs = list(
        session.execute(select(PipelineRun).order_by(PipelineRun.started_at.desc())).scalars()
    )
    metrics.runs_total = len(runs)
    metrics.runs_failed = sum(1 for r in runs if r.status in {"failed", "blocked"})
    if runs:
        latest = runs[0]
        metrics.last_run_id = latest.run_id
        metrics.last_run_status = latest.status
        metrics.last_run_journal = latest.journal_number or ""
        metrics.last_run_at = latest.started_at.strftime("%Y-%m-%d %H:%M")

    for delivery in session.execute(select(Delivery)).scalars():
        if delivery.status == "sent":
            metrics.deliveries_sent += 1
        elif delivery.status == "rendered_not_sent":
            metrics.deliveries_rendered_only += 1
        elif delivery.status == "failed":
            metrics.deliveries_failed += 1

    for customer in session.execute(select(Customer)).scalars():
        if customer.subscription_status == "cancelled":
            metrics.cancelled_customers += 1
        elif customer.subscription_status == "past_due":
            metrics.past_due_customers += 1
        if delivery_allowed(customer):
            metrics.active_customers += 1
        elif customer.subscription_status != "cancelled":
            reason = delivery_block_reason(customer)
            if reason:
                metrics.blocked_customers.append((customer.company, reason))

    metrics.recent_errors = len(list(session.execute(select(ErrorLog)).scalars()))
    return metrics


def paying_customers(session: Session) -> list[Customer]:
    """Customers actually being billed. Trialing and past-due are not revenue."""
    return [
        customer
        for customer in session.execute(select(Customer)).scalars()
        if customer.subscription_status == "active"
    ]


def business_status(
    session: Session, prospects: list[Prospect], settings: Settings | None = None
) -> BusinessStatus:
    settings = settings or get_settings()
    plans = {p["key"]: p for p in load_config("customer_plans.json")["plans"]}

    paying = paying_customers(session)
    mrr = sum(int(plans.get(c.plan_key, {}).get("price_pence", 0)) for c in paying)

    return BusinessStatus(
        funnel=funnel_metrics(prospects),
        product=product_metrics(session),
        operations=operational_metrics(session, settings),
        costs=estimate_costs(
            customers=len(paying),
            llm_enabled=settings.llm_enabled,
            search_enabled=settings.search_enabled,
        ),
        mrr_pence=mrr,
    )


def unrated_lead_count(session: Session) -> int:
    """Delivered opportunities nobody has said anything about yet."""
    rated = {
        row.dedupe_key for row in session.execute(select(LeadFeedback)).scalars() if row.dedupe_key
    }
    delivered = [
        row
        for row in session.execute(select(OpportunityRow)).scalars()
        if row.delivered and not row.suppressed
    ]
    return sum(1 for row in delivered if row.dedupe_key not in rated)


__all__ = [
    "BusinessStatus",
    "FunnelMetrics",
    "OperationalMetrics",
    "ProductMetrics",
    "Rate",
    "business_status",
    "funnel_metrics",
    "operational_metrics",
    "paying_customers",
    "product_metrics",
    "unrated_lead_count",
]
