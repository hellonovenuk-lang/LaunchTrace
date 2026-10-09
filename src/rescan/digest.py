"""The weekly "brands that moved" digest.

After a rescan, each recipient of an eligible customer gets one email listing
the brands whose stage moved meaningfully (``config/rescan.json`` ->
``transitions``) since their previous digest (first time: ``lookback_days``).

The rules are the weekly feed's, reused rather than restated:

* **who receives it** -- ``active_customers`` from ``src/delivery_service.py``
  (subscription status, past-due grace period, ``delivery_enabled``); a
  recipient address on the email suppression list is skipped;
* **which brands** -- only company-level confirmed brands (a Companies House
  company number and name, applicant typed corporate), never one matching a
  company/mark suppression rule or an opted-out company, filtered by the
  recipient's band / category / region preferences;
* **privacy** -- who is behind a brand is shown with ``src.privacy.display_party``
  (the registered company name); an applicant's name is not even stored on a
  brand;
* **sending** -- ``SEND_MODE=review`` renders to ``reports/outbox/`` and never
  sends; so does ``digest.send_enabled=false`` (the default until the copy has
  been reviewed); otherwise the normal ``EmailSender``, which itself writes to
  the outbox when there is no Resend key;
* **idempotency** -- one ``deliveries`` row per customer, recipient and ISO week
  (``kind="movers_digest"``), so the three Friday attempts send once.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.repository import active_suppressions, record_delivery
from src.db.tables import Brand, Customer, CustomerPreference, Delivery, StageChange
from src.deliver.email_render import RenderedEmail
from src.deliver.resend_client import EmailSender
from src.delivery_service import BAND_RANK, active_customers, recipients_for
from src.errors import RenderFailureError
from src.logging_setup import get_logger
from src.privacy import display_party
from src.rescan.changes import Dimension, dimensions, rescan_config
from src.settings import Settings, get_settings

log = get_logger(__name__)

KIND = "movers_digest"
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "deliver" / "templates"
DELIVERED = {"sent", "rendered_not_sent"}


# ---------------------------------------------------------------------------
# the movers
# ---------------------------------------------------------------------------


@dataclass
class Move:
    dimension: str
    from_label: str
    to_label: str
    detected_at: datetime
    negative: bool = False
    launched: bool = False


@dataclass
class Mover:
    """One brand that moved, as the digest may show it. Company level only."""

    brand_uid: str
    brand_name: str
    party: str
    company_number: str | None
    region: str | None
    product_category: str | None
    category_label: str | None
    band: str
    score: int
    website: str | None
    moves: list[Move] = field(default_factory=list)

    @property
    def negative(self) -> bool:
        return any(m.negative for m in self.moves)

    @property
    def launched(self) -> bool:
        return any(m.launched for m in self.moves)

    @property
    def latest(self) -> datetime:
        return max(m.detected_at for m in self.moves)

    @property
    def company_url(self) -> str | None:
        if not self.company_number:
            return None
        from src.enrich.companies_house import CH_COMPANY_URL

        return CH_COMPANY_URL.format(number=self.company_number)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def company_confirmed(brand: Brand) -> bool:
    """A confirmed Companies House company, never an individual."""
    return (
        bool((brand.company_number or "").strip())
        and bool((brand.company_name or "").strip())
        and (brand.applicant_type or "").lower() == "corporate"
    )


def _category_label(key: str | None) -> str | None:
    if not key:
        return None
    from src.feed.build import category_label

    return category_label(key)


def meaningful_changes(
    session: Session, since: datetime, until: datetime | None = None
) -> list[tuple[StageChange, Brand]]:
    """Rescan stage changes flagged meaningful, detected after ``since``."""
    stmt = (
        select(StageChange, Brand)
        .join(Brand, Brand.id == StageChange.brand_id)
        .where(StageChange.detected_at > since)
        .order_by(StageChange.detected_at, StageChange.id)
    )
    if until is not None:
        stmt = stmt.where(StageChange.detected_at <= until)
    out: list[tuple[StageChange, Brand]] = []
    for change, brand in session.execute(stmt).all():
        evidence = change.evidence if isinstance(change.evidence, dict) else {}
        if evidence.get("detected_by") == "rescan" and evidence.get("meaningful"):
            out.append((change, brand))
    return out


def movers_from(
    rows: list[tuple[StageChange, Brand]],
    *,
    require_company: bool = True,
    include_negative: bool = True,
    dims: dict[str, Dimension] | None = None,
) -> list[Mover]:
    """One ``Mover`` per brand; several moves in one dimension collapse to first-from, last-to."""
    dims = dims or dimensions()
    by_brand: dict[int, Mover] = {}
    spans: dict[tuple[int, str], dict[str, Any]] = {}
    for change, brand in rows:
        if require_company and not company_confirmed(brand):
            continue
        evidence = change.evidence if isinstance(change.evidence, dict) else {}
        negative = bool(evidence.get("negative"))
        if negative and not include_negative:
            continue
        key = str(evidence.get("dimension") or "")
        dim = dims.get(key)
        if dim is None:
            continue
        if brand.id not in by_brand:
            by_brand[brand.id] = Mover(
                brand_uid=brand.brand_uid,
                brand_name=brand.brand_name or "",
                party=display_party(brand.company_name, None, brand.applicant_type, empty=""),
                company_number=brand.company_number,
                region=brand.region,
                product_category=brand.product_category,
                category_label=_category_label(brand.product_category),
                band=brand.current_band or "SUPPRESS",
                score=int(brand.current_score or 0),
                website=brand.website,
            )
        span = spans.setdefault(
            (brand.id, key),
            {"from": evidence.get("old_value"), "negative": False, "launched": False},
        )
        span["to"] = evidence.get("new_value")
        span["at"] = _aware(change.detected_at)
        span["negative"] = negative
        span["launched"] = span["launched"] or bool(evidence.get("launched"))
    for (brand_id, key), span in spans.items():
        dim = dims[key]
        by_brand[brand_id].moves.append(
            Move(
                dimension=key,
                from_label=dim.label(span["from"]),
                to_label=dim.label(span["to"]),
                detected_at=span["at"],
                negative=span["negative"],
                launched=span["launched"],
            )
        )
    movers = [m for m in by_brand.values() if m.moves]
    movers.sort(key=lambda m: (m.negative, not m.launched, -m.score, m.brand_name))
    return movers


def filter_for_preference(movers: list[Mover], pref: CustomerPreference) -> list[Mover]:
    """The weekly feed's preference rules: minimum band, categories, regions."""
    minimum = BAND_RANK.get(pref.min_score_band or "MEDIUM", 1)
    out = []
    for mover in movers:
        if BAND_RANK.get(mover.band, 0) < minimum:
            continue
        if pref.product_categories and mover.product_category not in pref.product_categories:
            continue
        if pref.regions and (mover.region or "") not in pref.regions:
            continue
        out.append(mover)
    return out


def _suppressed(brand: Brand, suppressions: Any) -> bool:
    from src.parse.normalise import normalise_company_name

    low = {(brand.company_name or "").strip().lower(), (brand.company_number or "").strip().lower()}
    if low & (set(suppressions.company) | set(suppressions.applicant)):
        return True
    if (brand.brand_name or "").strip().lower() in suppressions.mark:
        return True
    return normalise_company_name(brand.company_name) in suppressions.company_normalised


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def digest_subject(count: int) -> str:
    if count == 1:
        return "1 tracked UK food brand moved this week"
    return f"{count} tracked UK food brands moved this week"


def render_movers_digest(
    movers: list[Mover],
    *,
    since: datetime,
    settings: Settings | None = None,
    max_brands: int | None = None,
) -> RenderedEmail:
    settings = settings or get_settings()
    cap = max_brands or int(rescan_config().get("digest", {}).get("max_brands_per_email", 25))
    shown = movers[:cap]
    up = [m for m in shown if not m.negative]
    down = [m for m in shown if m.negative]
    subject = digest_subject(len(movers))
    base = settings.site_url.rstrip("/")
    since_text = _aware(since).strftime("%-d %B %Y")
    try:
        html = (
            _env()
            .get_template("movers_digest.html.j2")
            .render(
                subject=subject,
                since=since_text,
                total=len(movers),
                up=up,
                down=down,
                hidden=len(movers) - len(shown),
                manage_url=f"{base}/billing/manage",
                unsubscribe_url=f"{base}/unsubscribe",
                privacy_url=f"{base}/privacy",
                from_name="LaunchTrace",
            )
        )
    except Exception as exc:
        raise RenderFailureError(f"Movers digest failed to render: {exc}") from exc
    text = render_movers_text(shown, since, settings, len(movers))
    return RenderedEmail(subject=subject, html=html, text=text)


def _move_line(move: Move) -> str:
    tag = " (launched)" if move.launched else ""
    return f"{move.from_label} -> {move.to_label}{tag}, {move.detected_at.strftime('%-d %b')}"


def render_movers_text(
    movers: list[Mover], since: datetime, settings: Settings, total: int | None = None
) -> str:
    total = len(movers) if total is None else total
    base = settings.site_url.rstrip("/")
    lines = [
        digest_subject(total),
        "",
        f"Brands LaunchTrace has seen before whose website or company record moved since "
        f"{_aware(since).strftime('%-d %B %Y')}.",
        "",
    ]
    for mover in movers:
        parts = [p for p in (mover.party, mover.category_label, mover.region) if p]
        lines.append(f"{mover.brand_name or mover.brand_uid} ({' · '.join(parts)})")
        for move in mover.moves:
            lines.append(f"    - {_move_line(move)}")
        if mover.website:
            lines.append(f"    {mover.website}")
    if total > len(movers):
        lines.append(f"... and {total - len(movers)} more.")
    lines += [
        "",
        "These are observed changes to public websites and the Companies House register, "
        "not confirmed purchasing intent.",
        "Source: Companies House data under the Open Government Licence v3.0; public RDAP, "
        "DNS and website checks.",
        f"Manage or cancel your subscription: {base}/billing/manage",
        f"Stop these emails: {base}/unsubscribe",
        f"Privacy: {base}/privacy",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------


@dataclass
class DigestSummary:
    week: str
    recipients: int = 0
    sent: int = 0
    rendered_not_sent: int = 0
    skipped_already_sent: int = 0
    skipped_empty: int = 0
    skipped_suppressed: int = 0
    failed: int = 0
    disabled: bool = False
    review_only: bool = True
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "recipients": self.recipients,
            "sent": self.sent,
            "rendered_not_sent": self.rendered_not_sent,
            "skipped_already_sent": self.skipped_already_sent,
            "skipped_empty": self.skipped_empty,
            "skipped_suppressed": self.skipped_suppressed,
            "failed": self.failed,
            "disabled": self.disabled,
            "review_only": self.review_only,
        }


def iso_week(moment: datetime) -> str:
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def idempotency_key(customer_id: int, week: str, recipient: str) -> str:
    digest = hashlib.sha256(recipient.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"{KIND}:{customer_id}:{week}:{digest}"


def _last_digest_at(session: Session, customer: Customer, recipient: str) -> datetime | None:
    rows = session.execute(
        select(Delivery.created_at)
        .where(
            Delivery.kind == KIND,
            Delivery.customer_id == customer.id,
            Delivery.recipient_email == recipient,
            Delivery.status.in_(sorted(DELIVERED)),
        )
        .order_by(Delivery.created_at.desc())
        .limit(1)
    ).all()
    return _aware(rows[0][0]) if rows and rows[0][0] is not None else None


def send_movers_digests(
    session: Session,
    settings: Settings | None = None,
    *,
    sender: EmailSender | None = None,
    now: datetime | None = None,
    config: dict[str, Any] | None = None,
) -> DigestSummary:
    """Prepare (and, if allowed, send) this week's digest for every eligible recipient."""
    settings = settings or get_settings()
    cfg = (config or rescan_config()).get("digest", {})
    now = now or datetime.now(UTC)
    week = iso_week(now)
    summary = DigestSummary(week=week)
    if not cfg.get("enabled", True):
        summary.disabled = True
        return summary

    live = settings.send_mode == "automatic" and bool(cfg.get("send_enabled", False))
    summary.review_only = not live
    if sender is None:
        sender = EmailSender(
            settings if live else settings.model_copy(update={"resend_api_key": ""})
        )
    elif not live and sender.live:
        # Never let an injected live sender send in review mode.
        sender = EmailSender(
            sender.settings.model_copy(update={"resend_api_key": ""}), outbox=sender.outbox
        )

    lookback = timedelta(days=int(cfg.get("lookback_days", 7)))
    include_negative = bool(cfg.get("include_negative", True))
    send_when_empty = bool(cfg.get("send_when_empty", False))
    max_brands = int(cfg.get("max_brands_per_email", 25))
    rules = active_suppressions(session)
    suppressed_emails = rules.get("email", set())

    from src.feed.build import Suppressions

    suppressions = Suppressions.from_session(session)
    dims = dimensions()
    earliest = now - lookback

    for customer in active_customers(session):
        for pref in recipients_for(session, customer):
            recipient = pref.recipient_email
            summary.recipients += 1
            if recipient.strip().lower() in suppressed_emails:
                summary.skipped_suppressed += 1
                continue
            key = idempotency_key(customer.id, week, recipient)
            existing = session.execute(
                select(Delivery).where(Delivery.idempotency_key == key)
            ).scalar_one_or_none()
            if existing is not None and existing.status in DELIVERED:
                summary.skipped_already_sent += 1
                continue

            since = _last_digest_at(session, customer, recipient) or earliest
            rows = meaningful_changes(session, since, now)
            by_uid = {brand.brand_uid: brand for _, brand in rows}
            movers = [
                m
                for m in movers_from(rows, include_negative=include_negative, dims=dims)
                if not _suppressed(by_uid[m.brand_uid], suppressions)
            ]
            movers = filter_for_preference(movers, pref)
            if not movers and not send_when_empty:
                summary.skipped_empty += 1
                continue

            rendered = render_movers_digest(
                movers, since=since, settings=settings, max_brands=max_brands
            )
            delivery, _ = record_delivery(
                session,
                run_id=f"{KIND}:{week}",
                journal_number="",
                recipient_email=recipient,
                idempotency_key=key,
                customer_id=customer.id,
                kind=KIND,
                opportunity_count=len(movers),
            )
            result = sender.send([recipient], rendered, idempotency_key=key)
            delivery.status = result.status
            delivery.provider_message_id = result.message_id
            delivery.error = result.error
            if result.status == "sent":
                delivery.sent_at = datetime.now(UTC)
                summary.sent += 1
            elif result.status == "rendered_not_sent":
                summary.rendered_not_sent += 1
                summary.details.append(f"{recipient}: written to {result.path}")
            else:
                summary.failed += 1
                summary.details.append(f"{recipient}: {result.error}")
    session.flush()
    log.info("movers_digest.finished", **summary.as_dict())
    return summary


__all__ = [
    "DigestSummary",
    "KIND",
    "Mover",
    "company_confirmed",
    "filter_for_preference",
    "idempotency_key",
    "iso_week",
    "meaningful_changes",
    "movers_from",
    "render_movers_digest",
    "send_movers_digests",
]
