"""Turning a stored opportunity into the language a salesperson reads.

The workbook is a sales tool, so nobody opening it should have to know what a
Nice class is, what a SIC code means, or how a LaunchTrace Score is built. This
module is the translation layer: it takes an opportunity that has already
qualified and produces the four sentences the customer actually reads — what
the product is, why the timing matters, why it is relevant to them, and a
suggested angle.

Three rules hold everywhere in here:

* **Nothing is invented.** A format is named only when its keyword appears in
  the stored goods text. A timing claim is made only when the score already
  recorded it. An unknown field renders as an honest blank, never as a guess.
* **Nothing is changed.** Scores, bands, qualification and fit ranking are read
  and never written. Dropping a lead because its wording is awkward would be
  changing the product, so it does not happen.
* **An angle is a suggestion.** Every angle describes what this supplier might
  offer, never what the brand has asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.parse.open_data import IPO_CASE_URL
from src.sales.matching import MatchedLead, PreviewResult
from src.settings import load_config

COMPANIES_HOUSE_URL = "https://find-and-update.company-information.service.gov.uk/company/{number}"

# Score-reason keys carry stable meanings, but the delivered CSV only keeps the
# rendered sentence, so the fingerprints below are matched against that text.
_FIRST_MARK = "first trade mark"
_NARROW_RANGE = "narrow class profile"
_ONLY_MARK = "single mark filed this week"
_NO_RETAILERS = "no established multiple-retailer listings"


@dataclass
class CustomerRow:
    """One opportunity, in the words the customer's sales team reads."""

    priority: str
    brand: str
    company: str
    location: str
    product_category: str
    product: str
    stage: str
    why_now: str
    why_relevant: str
    sales_angle: str
    website: str
    website_url: str
    trademark_number: str
    trademark_url: str
    company_number: str
    company_url: str
    filing_date: date | None
    publication_date: date | None
    category_key: str = ""
    fit_score: float = 0.0
    score: int = 0
    band: str = ""


@dataclass
class CustomerReport:
    """Everything the workbook needs, with no internal objects left in it."""

    customer_name: str
    report_type: str
    journal_number: str
    publication_date: date | None
    rows: list[CustomerRow]
    highlights: list[CustomerRow]
    # (taxonomy key, how many, short human label) — ordered most common first.
    category_counts: list[tuple[str, int, str]] = field(default_factory=list)
    signal: str = ""
    considered: int = 0

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def top_count(self) -> int:
        return sum(1 for row in self.rows if row.priority == "TOP MATCH")


def _months_between(earlier: date | None, later: date | None) -> int | None:
    """Company age in months, computed the way the score already computed it.

    Mirrors ``CompanyMatch.age_years_at`` followed by the scorer's rounding, so
    the workbook can never state an age that contradicts the stored reason a
    customer could be shown alongside it.
    """
    if not earlier or not later:
        return None
    years = round((later - earlier).days / 365.25, 2)
    if years < 0:
        return None
    return int(round(years * 12))


def _age_phrase(months: int | None) -> str:
    """How long before the filing the company was formed, in words."""
    if months is None:
        return ""
    if months == 0:
        return "incorporated in the same month as the filing"
    if months == 1:
        return "incorporated a month before the filing"
    if months < 24:
        return f"incorporated {months} months before the filing"
    years = months // 12
    return f"incorporated {years} years before the filing"


def _short_date(value: date | None) -> str:
    return value.strftime("%-d %b") if value else ""


def _has_reason(match: MatchedLead, needle: str) -> bool:
    return any(needle in reason.lower() for reason in match.lead.reasons)


def summarise_product(match: MatchedLead, config: dict) -> tuple[str, str]:
    """Compress a trade mark goods list to a readable head and full line.

    Returns ``(head, line)`` — for example ``("Coffee", "Coffee — whole bean,
    ground, espresso, drip bags and coffee bags")``. Only labels whose keyword
    appears in the stored goods text are used, so the line is always shorter
    than the filing and never says anything the filing does not.
    """
    lead = match.lead
    spec = config["product_summary"]
    rules = spec["categories"].get(lead.product_category)
    goods = (lead.goods_summary or "").lower()
    if not rules or not goods:
        label = lead.display_category or "Not categorised"
        return label, label

    head = ""
    for candidate in rules["heads"]:
        if any(token in goods for token in candidate["match"]):
            head = candidate["label"]
            break
    if not head:
        head = lead.display_category or "Not categorised"

    formats: list[str] = []
    head_words = {word for word in head.lower().split() if word not in {"and", "of"}}
    for candidate in rules["formats"]:
        if len(formats) >= int(spec["max_formats"]):
            break
        label = str(candidate["label"])
        if label in formats:
            continue
        # A format that only repeats a word already in the head adds nothing:
        # "Chocolate — chocolate confectionery" is noise, not detail. A few
        # formats are named that way and still mean something on their own, so
        # the config can mark them as worth keeping.
        if head_words & set(label.lower().split()) and not candidate.get("keep_with_head"):
            continue
        if any(token in goods for token in candidate["match"]):
            formats.append(label)

    if not formats:
        return head, head
    if len(formats) == 1:
        return head, f"{head} — {formats[0]}"
    return head, f"{head} — {', '.join(formats[:-1])} and {formats[-1]}"


def why_now(match: MatchedLead) -> str:
    """The timing evidence, in plain English, from what the score already said."""
    lead = match.lead
    parts: list[str] = []

    filed = _short_date(lead.filing_date)
    published = _short_date(lead.publication_date)
    if filed and published:
        parts.append(f"Filed {filed}, published {published}.")
    elif published:
        parts.append(f"Published {published}.")

    age = _age_phrase(_months_between(lead.company_incorporation_date, lead.filing_date))
    first_mark = _has_reason(match, _FIRST_MARK)
    # Two different facts that read almost the same but are not: a filing that
    # covers one narrow range, and an applicant who filed only this one mark.
    if _has_reason(match, _NARROW_RANGE):
        scope = ", covering a single product range"
    elif _has_reason(match, _ONLY_MARK):
        scope = ", and the only mark they filed this week"
    else:
        scope = ""
    if age:
        opener = "First trade mark from a company" if first_mark else "The company was"
        parts.append(f"{opener} {age}{scope}.")
    elif first_mark:
        parts.append(f"This is the first trade mark we have seen from the company{scope}.")

    presence: list[str] = []
    if lead.website:
        if lead.retail_presence in {"direct_only", "Direct / own site only"}:
            presence.append("Selling direct from its own site")
        else:
            presence.append("Already trading from its own site")
        # Worth saying only when there is a site to compare it against.
        if _has_reason(match, _NO_RETAILERS):
            presence.append("with no multiple-retailer listings found")
    else:
        presence.append("No website we could confirm")
    if lead.launch_stage in {"pre-launch", "pre_launch"}:
        presence.append("and the signals read pre-launch")
    parts.append(", ".join(presence) + ".")

    return " ".join(parts)


def why_relevant(
    match: MatchedLead, product_head: str, product_line: str, config: dict, profile_key: str
) -> str:
    """Why this brand matters to the supplier reading the workbook."""
    angles = config["supplier_angles"].get(
        profile_key, config["supplier_angles"]["flexible_packaging"]
    )
    entry = angles.get(match.lead.product_category, angles["_default"])
    sentence = str(entry["relevance"]).format(
        brand=match.lead.brand_name or match.lead.trademark_number,
        product=product_line,
        product_head=product_head,
    )

    clauses = config["stage_clause"]
    lead = match.lead
    brand = lead.brand_name or lead.display_company
    if lead.launch_stage in {"pre-launch", "pre_launch"}:
        tail = str(clauses["pre_launch"])
    elif lead.website:
        tail = str(clauses["selling_direct"])
    else:
        tail = str(clauses["no_website"])
    return f"{sentence} {tail.format(brand=brand)}"


def sales_angle(match: MatchedLead, product_head: str, config: dict, profile_key: str) -> str:
    """A concrete opening a salesperson could use. A suggestion, not a request."""
    angles = config["supplier_angles"].get(
        profile_key, config["supplier_angles"]["flexible_packaging"]
    )
    entry = angles.get(match.lead.product_category, angles["_default"])
    angle = entry["angle"]
    if isinstance(angle, dict):
        angle = angle.get(product_head) or angle.get("_default") or angles["_default"]["angle"]
    return str(angle)


def priority_for(match: MatchedLead, config: dict) -> str:
    """The customer-facing priority.

    Derived from the band the opportunity already earned and its fit with this
    supplier. It never promotes a lead that did not qualify, and the underlying
    score is left exactly as it was.
    """
    rules = config["priority"]
    if match.lead.band == rules["top_match_requires_band"]:
        return "TOP MATCH"
    if match.fit_score >= float(rules["strong_minimum_fit"]):
        return "STRONG"
    return "RELEVANT"


def _stage_label(lead_stage: str, config: dict) -> str:
    return str(config["stage_labels"].get(lead_stage.lower(), config["stage_labels"]["unknown"]))


def _location(match: MatchedLead, towns: dict[str, str]) -> str:
    town = towns.get(match.lead.company_number, "")
    if town:
        return town.title()
    return match.lead.company_region.title() if match.lead.company_region else ""


def build_row(
    match: MatchedLead,
    config: dict,
    profile_key: str,
    towns: dict[str, str],
) -> CustomerRow:
    head, line = summarise_product(match, config)
    lead = match.lead
    return CustomerRow(
        priority=priority_for(match, config),
        brand=lead.brand_name or lead.trademark_number,
        company=lead.display_company,
        location=_location(match, towns),
        product_category=lead.display_category,
        product=line,
        stage=_stage_label(lead.launch_stage, config),
        why_now=why_now(match),
        why_relevant=why_relevant(match, head, line, config, profile_key),
        sales_angle=sales_angle(match, head, config, profile_key),
        category_key=lead.product_category,
        website=lead.website or str(config["website_unconfirmed_label"]),
        website_url=lead.website,
        trademark_number=lead.trademark_number,
        trademark_url=IPO_CASE_URL.format(number=lead.trademark_number),
        company_number=lead.company_number,
        company_url=(
            COMPANIES_HOUSE_URL.format(number=lead.company_number) if lead.company_number else ""
        ),
        filing_date=lead.filing_date,
        publication_date=lead.publication_date,
        fit_score=match.fit_score,
        score=lead.score,
        band=lead.band,
    )


_PRIORITY_ORDER = {"TOP MATCH": 0, "STRONG": 1, "RELEVANT": 2}


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _signal_sentence(rows: list[CustomerRow], counts: list[tuple[str, int, str]]) -> str:
    """A plain-English read on where the week's opportunity sits.

    Written only from what the counts support. When no category stands out, it
    says so rather than manufacturing a pattern out of a flat week.
    """
    if not rows or not counts:
        return ""
    total = len(rows)
    _, top_n, top_short = counts[0]
    strongest = max(rows, key=lambda r: r.score)

    if top_n < 2:
        return (
            f"No single category stands out this week: the {total} relevant brands are spread "
            f"across {len(counts)} categories, one each. The strongest signal is "
            f"{strongest.brand}."
        )

    lead = f"{top_short.capitalize()} is the largest cluster this week — {top_n} of the {total} relevant brands"
    if strongest.category_key == counts[0][0]:
        lead += f", including the strongest signal in the set, {strongest.brand}"
    lead += "."

    second = ""
    if len(counts) > 1 and counts[1][1] >= 2:
        second = f" {counts[1][2].capitalize()} adds {counts[1][1]} more."

    singles = [short for _, n, short in counts if n == 1]
    tail = f" The rest are single brands in {_join(singles)}." if singles else ""
    return f"{lead}{second}{tail}"


def build_report(
    result: PreviewResult,
    config: dict | None = None,
    towns: dict[str, str] | None = None,
    highlight_count: int = 4,
) -> CustomerReport:
    """Assemble everything the workbook renders.

    ``result`` is the existing supplier match, used exactly as it came back.
    The highlights are its own first ``highlight_count`` picks, so the brief
    agrees with the customer-test selection rather than re-ranking it.
    """
    cfg = config or load_config("customer_report.json")
    profile_key = result.prospect.supplier_category
    town_map = towns or {}

    ordered = [build_row(match, cfg, profile_key, town_map) for match in result.matches]
    highlights = ordered[:highlight_count]

    rows = sorted(
        ordered,
        key=lambda r: (_PRIORITY_ORDER.get(r.priority, 9), -r.fit_score, -r.score),
    )

    shorts = cfg["category_short_labels"]
    tally: dict[str, int] = {}
    for row in rows:
        tally[row.category_key] = tally.get(row.category_key, 0) + 1
    counts = sorted(
        (
            (key, number, str(shorts.get(key, key.replace("_", " "))))
            for key, number in tally.items()
        ),
        key=lambda item: (-item[1], item[2]),
    )

    return CustomerReport(
        customer_name=result.prospect.company_name,
        report_type=str(cfg["copy"]["report_type"]),
        journal_number="",
        publication_date=result.newest_publication,
        rows=rows,
        highlights=highlights,
        category_counts=counts,
        signal=_signal_sentence(rows, counts),
        considered=result.considered,
    )


__all__ = [
    "COMPANIES_HOUSE_URL",
    "CustomerReport",
    "CustomerRow",
    "build_report",
    "build_row",
    "priority_for",
    "sales_angle",
    "summarise_product",
    "why_now",
    "why_relevant",
]
