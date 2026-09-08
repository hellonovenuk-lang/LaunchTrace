"""Email rendering.

Rendering is entirely separate from sending, so the exact HTML a customer would
receive can be produced, snapshot-tested and reviewed with no email credential
configured at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.errors import RenderFailureError
from src.models import Opportunity, PipelineResult
from src.score.buying_intent import supplier_category_labels
from src.settings import Settings, get_settings, load_config

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass
class RenderedEmail:
    subject: str
    html: str
    text: str


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _top_intents(labels: dict[str, str], limit: int = 3):  # type: ignore[no-untyped-def]
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}

    def _fn(opp: Opportunity) -> list[tuple[str, str]]:
        pairs = [
            (labels.get(k, k), v) for k, v in opp.buying_intent.as_dict().items() if v in {"HIGH", "MEDIUM"}
        ]
        pairs.sort(key=lambda p: order.get(p[1], 0), reverse=True)
        return pairs[:limit]

    return _fn


def build_subject(count: int) -> str:
    if count == 1:
        return "1 emerging UK food brand detected this week"
    return f"{count} emerging UK food brands detected this week"


def render_weekly_email(
    result: PipelineResult,
    csv_url: str | None = None,
    settings: Settings | None = None,
    top_n: int = 5,
) -> RenderedEmail:
    settings = settings or get_settings()
    opportunities = sorted(result.deliverable, key=lambda o: o.score.value, reverse=True)
    subject = build_subject(len(opportunities))
    high_count = sum(1 for o in opportunities if o.score.band.value == "HIGH")

    intro = (
        f"{len(opportunities)} UK food brand{'s' if len(opportunities) != 1 else ''} came through this "
        "week's trade mark journal looking early enough to be worth a conversation. Each has been "
        "checked against Companies House and scored on how early-stage it appears."
    )
    if not opportunities:
        intro = (
            "No brands cleared this week's qualification thresholds. Nothing has been removed from "
            "your subscription — the feed simply had nothing worth your sales team's time this week."
        )

    try:
        template = _env().get_template("weekly_feed.html.j2")
        html = template.render(
            subject=subject,
            intro=intro,
            total=len(opportunities),
            high_count=high_count,
            top=opportunities[:top_n],
            top_intents=_top_intents(supplier_category_labels()),
            journal_number=result.journal.journal_number,
            publication_date=result.journal.publication_date.strftime("%-d %B %Y"),
            csv_url=csv_url,
            from_name="LaunchTrace",
            manage_url=f"{settings.site_url.rstrip('/')}/billing/manage",
        )
    except Exception as exc:
        raise RenderFailureError(f"Weekly email failed to render: {exc}") from exc

    return RenderedEmail(subject=subject, html=html, text=render_weekly_text(result, opportunities))


def render_weekly_text(result: PipelineResult, opportunities: list[Opportunity]) -> str:
    lines = [
        build_subject(len(opportunities)),
        "",
        f"Journal {result.journal.journal_number} — published {result.journal.publication_date.isoformat()}",
        "",
    ]
    for opp in opportunities[:10]:
        lines.append(
            f"{opp.score.value} {opp.score.band.value} — {opp.brand_name or opp.trademark_number}"
            f" ({opp.company.company_name or opp.applicant_name or 'unknown company'})"
        )
        for reason in opp.score.reason_texts[:2]:
            lines.append(f"    - {reason}")
    lines += [
        "",
        "Full list in the attached CSV.",
        "",
        "LaunchTrace scores are an early-stage opportunity signal, not a prediction of purchase.",
        "Source: UK Intellectual Property Office and Companies House data under the Open Government Licence v3.0.",
    ]
    return "\n".join(lines)


def render_welcome_email(
    company: str,
    contact_name: str | None,
    plan_key: str = "founding_monthly",
    settings: Settings | None = None,
) -> RenderedEmail:
    settings = settings or get_settings()
    plans = load_config("customer_plans.json")["plans"]
    plan = next((p for p in plans if p["key"] == plan_key), plans[0])
    html = _env().get_template("welcome.html.j2").render(
        company=company,
        contact_name=contact_name,
        plan_name=plan["name"],
        price_pence=plan["price_pence"],
        max_recipients=plan["max_recipients"],
        manage_url=f"{settings.site_url.rstrip('/')}/billing/manage",
    )
    return RenderedEmail(
        subject="You're subscribed to LaunchTrace Food",
        html=html,
        text=f"{company} is subscribed to LaunchTrace Food on the {plan['name']} plan. "
        f"Your first weekly feed arrives on the next Friday run.",
    )


def render_alert_email(
    title: str, run_id: str, journal_number: str, status: str, detail: str
) -> RenderedEmail:
    html = _env().get_template("alert.html.j2").render(
        title=title, run_id=run_id, journal_number=journal_number, status=status, detail=detail
    )
    return RenderedEmail(
        subject=f"[LaunchTrace] {title}",
        html=html,
        text=f"{title}\nRun: {run_id}\nJournal: {journal_number}\nStatus: {status}\n\n{detail}",
    )


def render_sample_email(
    company: str,
    contact_name: str | None,
    total: int,
    supplier_type_label: str | None = None,
    settings: Settings | None = None,
) -> RenderedEmail:
    settings = settings or get_settings()
    plans = load_config("customer_plans.json")["plans"]
    founding = next(p for p in plans if p["key"] == "founding_monthly")
    base = settings.site_url.rstrip("/")
    html = _env().get_template("sample.html.j2").render(
        company=company,
        contact_name=contact_name,
        total=total,
        supplier_type_label=supplier_type_label,
        price_pence=founding["price_pence"],
        subscribe_url=f"{base}/billing/checkout?plan=founding_monthly",
        unsubscribe_url=f"{base}/unsubscribe",
        site_url=base,
    )
    return RenderedEmail(
        subject="Your LaunchTrace Food sample",
        html=html,
        text=f"Your LaunchTrace Food sample: {total} emerging UK food brands. CSV attached.",
    )


def write_email_html(rendered: RenderedEmail, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered.html, encoding="utf-8")
    return path


