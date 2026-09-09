"""Drafting outreach, and working out who is due what.

Two responsibilities:

* fill an outreach template for one prospect, with their own lead block already
  inserted, and write it where the operator can read it;
* answer the only question an operator asks each morning — who is due for which
  action today.

**Nothing here sends anything.** There is no send function, no transport, and
no code path from this module to an email provider. That is deliberate and
documented in ``outreach/README.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from src.sales.matching import PreviewResult
from src.sales.models import Prospect, ProspectStatus, ReplyState, today
from src.sales.preview import render_email_block
from src.sales.store import SuppressionList
from src.settings import REPO_ROOT, REPORTS_DIR, load_config

TEMPLATE_DIR = REPO_ROOT / "outreach" / "templates"
DRAFT_DIR = REPORTS_DIR / "outreach_drafts"

# How long to wait before a follow-up is due. One follow-up only: after Email 3
# there is nothing further, by policy.
FOLLOW_UP_AFTER_EMAIL_1_DAYS = 7
FOLLOW_UP_AFTER_SAMPLE_DAYS = 7

TEMPLATES = {
    "email_1": "email_1_first_contact.md",
    "email_2": "email_2_full_sample.md",
    "email_3": "email_3_follow_up.md",
}


@dataclass
class DueAction:
    """One thing an operator could do today, and why."""

    prospect: Prospect
    action: str
    reason: str
    command: str = ""

    @property
    def priority_key(self) -> tuple[int, int, str]:
        order = {"A": 0, "B": 1, "C": 2, "SUPPRESS": 3}
        return (
            _ACTION_ORDER.index(self.action) if self.action in _ACTION_ORDER else 99,
            order.get(self.prospect.priority.value, 9),
            self.prospect.company_name.lower(),
        )


# Ordered by how close the action is to revenue, which is the order an operator
# should work through them.
_ACTION_ORDER = [
    "SEND FULL SAMPLE",
    "SEND SUBSCRIPTION OFFER",
    "HANDLE REPLY",
    "SEND FOLLOW-UP",
    "SEND FIRST EMAIL",
    "FINISH RESEARCH",
]


@dataclass
class DueReport:
    """Everything an operator needs to run a day of outreach."""

    generated_on: date
    actions: list[DueAction] = field(default_factory=list)
    do_not_contact: list[Prospect] = field(default_factory=list)
    subscribed: list[Prospect] = field(default_factory=list)
    waiting: list[Prospect] = field(default_factory=list)
    blocked_by_suppression: list[tuple[Prospect, str]] = field(default_factory=list)

    def by_action(self, action: str) -> list[DueAction]:
        return [a for a in self.actions if a.action == action]


def outreach_due(
    prospects: list[Prospect],
    suppressions: SuppressionList | None = None,
    reference_date: date | None = None,
) -> DueReport:
    """Work out who is due what today.

    Suppression is checked first and separately for every prospect, so a
    prospect whose company later lands on the suppression list drops out of the
    action list even if their row still says READY.
    """
    reference = reference_date or today()
    report = DueReport(generated_on=reference)

    for prospect in prospects:
        if not prospect.contactable:
            report.do_not_contact.append(prospect)
            continue

        blocked = suppressions.blocks(prospect) if suppressions else None
        if blocked:
            report.blocked_by_suppression.append((prospect, blocked))
            continue

        if prospect.status == ProspectStatus.SUBSCRIBED:
            report.subscribed.append(prospect)
            continue

        action = _action_for(prospect, reference)
        if action is None:
            report.waiting.append(prospect)
        else:
            report.actions.append(action)

    report.actions.sort(key=lambda a: a.priority_key)
    return report


def _action_for(prospect: Prospect, reference: date) -> DueAction | None:
    status = prospect.status
    pid = prospect.prospect_id

    if status == ProspectStatus.RESEARCHED:
        missing = []
        if not prospect.has_verified_email and prospect.contact_route != "website_form":
            missing.append("a checked contact route")
        if prospect.priority.value == "SUPPRESS":
            return None
        if missing:
            return DueAction(
                prospect=prospect,
                action="FINISH RESEARCH",
                reason=(
                    "Needs "
                    + " and ".join(missing)
                    + ". Open "
                    + (prospect.website or "their website")
                    + ", find the real address, and put it in outreach/prospects.csv"
                ),
                command=f"python -m src.admin prospects show --prospect-id {pid}",
            )
        return DueAction(
            prospect=prospect,
            action="FINISH RESEARCH",
            reason="Research looks complete — confirm and mark READY",
            command=f"python -m src.admin prospects set-status --prospect-id {pid} --status READY",
        )

    if status == ProspectStatus.READY:
        return DueAction(
            prospect=prospect,
            action="SEND FIRST EMAIL",
            reason="Ready for first contact with a prospect-specific preview",
            command=f"python -m src.admin prospect-preview --prospect-id {pid} --draft-email",
        )

    if status == ProspectStatus.EMAIL_1_SENT:
        sent = prospect.email_1_sent_date
        if sent and (reference - sent).days >= FOLLOW_UP_AFTER_EMAIL_1_DAYS:
            return DueAction(
                prospect=prospect,
                action="SEND FOLLOW-UP",
                reason=(
                    f"First email sent {(reference - sent).days} days ago with no reply. "
                    "One follow-up, then stop."
                ),
                command=f"python -m src.admin outreach-draft --prospect-id {pid} --template email_3",
            )
        return None

    if status in {ProspectStatus.REPLIED_INTERESTED, ProspectStatus.SAMPLE_REQUESTED}:
        return DueAction(
            prospect=prospect,
            action="SEND FULL SAMPLE",
            reason="Asked to see more — the full sample is the next step",
            command=f"python -m src.admin prepare-sample --prospect-id {pid}",
        )

    if status == ProspectStatus.SAMPLE_SENT:
        sent = prospect.sample_sent_date
        if sent and (reference - sent).days >= FOLLOW_UP_AFTER_SAMPLE_DAYS:
            return DueAction(
                prospect=prospect,
                action="SEND SUBSCRIPTION OFFER",
                reason=(
                    f"Sample sent {(reference - sent).days} days ago. Ask whether it was useful "
                    "and offer the subscription."
                ),
                command=f"python -m src.admin outreach-draft --prospect-id {pid} --template email_3",
            )
        return None

    if status == ProspectStatus.OFFER_SENT:
        if prospect.reply_state == ReplyState.POSITIVE:
            return DueAction(
                prospect=prospect,
                action="HANDLE REPLY",
                reason="Positive reply to the offer — send the Stripe checkout link",
                command=f"python -m src.admin prospects show --prospect-id {pid}",
            )
        return None

    return None


# ---------------------------------------------------------------------------
# drafting
# ---------------------------------------------------------------------------


@dataclass
class Draft:
    """A rendered outreach message, written to disk and never sent."""

    prospect: Prospect
    template_key: str
    subject: str
    body: str
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)


SUBJECTS = {
    "email_1": "A few new food brands that may be useful",
    "email_2": "This week's full LaunchTrace sample",
    "email_3": "Worth keeping the feed going?",
}


def _template_text(template_key: str) -> str:
    filename = TEMPLATES.get(template_key)
    if filename is None:
        raise KeyError(f"Unknown template {template_key!r}. Known: {', '.join(TEMPLATES)}")
    path = TEMPLATE_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing outreach template: {path}")
    return path.read_text(encoding="utf-8")


def _body_only(text: str) -> str:
    """Strip the operator guidance around the message itself.

    The templates are written for a human to read, with a checklist above and
    below the message. Only the part between the two rules gets drafted.
    """
    parts = text.split("\n---\n")
    return parts[1].strip() if len(parts) >= 3 else text.strip()


def _supplier_service_phrase(prospect: Prospect) -> str:
    labels = {p["key"]: p["label"] for p in load_config("supplier_profiles.json")["profiles"]}
    label = labels.get(prospect.supplier_category, "supply")
    return label.lower()


def render_draft(
    prospect: Prospect,
    template_key: str = "email_1",
    preview: PreviewResult | None = None,
    sample_count: int | None = None,
    operator_name: str = "[YOUR NAME]",
    operator_company: str = "LaunchTrace",
) -> Draft:
    """Fill one outreach template for one prospect.

    Substitution is by explicit ``{{PLACEHOLDER}}`` token, not by matching
    prose, so editing the wording of a template can never quietly break the
    lead insertion.

    Refuses outright for anyone who must not be contacted: the cheapest place
    to stop a mistake is before the text exists.
    """
    warnings: list[str] = []
    if not prospect.contactable:
        raise PermissionError(
            f"{prospect.prospect_id} ({prospect.company_name}) is "
            f"{prospect.status.value} and must not be contacted."
        )

    body = _body_only(_template_text(template_key))
    if body.startswith("**Subject:**"):
        body = body.split("\n", 1)[-1].strip()

    if template_key == "email_1":
        if preview is None:
            warnings.append(
                "No preview supplied, so the lead block is a placeholder. "
                "Generate one before sending — the examples are the whole email."
            )
        elif preview.count == 0:
            warnings.append("The preview found no suitable opportunities. Do not send this email.")
        elif preview.count < 3:
            warnings.append(
                f"Only {preview.count} genuinely relevant opportunit"
                f"{'y' if preview.count == 1 else 'ies'} were found. "
                "Send what is here rather than padding it."
            )
        if preview is not None and preview.stale:
            warnings.append(
                f"The opportunities are {preview.source_age_days} days old. Do not describe "
                "them as brands from this week — run a live week first."
            )

    replacements = {
        "{{GREETING}}": prospect.named_contact or "there",
        "{{SUPPLIER_SERVICE}}": _supplier_service_phrase(prospect),
        "{{OPERATOR_NAME}}": operator_name,
        "{{OPERATOR_COMPANY}}": operator_company,
        "{{LEAD_BLOCK}}": (
            render_email_block(preview)
            if preview is not None
            else "[NO PREVIEW GENERATED — DO NOT SEND]"
        ),
        "{{SAMPLE_COUNT}}": str(sample_count) if sample_count is not None else "[N]",
    }
    for token, value in replacements.items():
        body = body.replace(token, value)

    if not prospect.has_verified_email:
        warnings.append(
            "No verified contact address on this prospect. Find the real address on their "
            "website before sending — never guess one."
        )

    leftover = re.findall(r"\{\{[A-Z_]+\}\}", body)
    if leftover:
        warnings.append("Template placeholder not substituted: " + ", ".join(sorted(set(leftover))))

    remaining = [token for token in ("[PHONE]", "[WEBSITE]", "[N]") if token in body]
    if remaining:
        warnings.append("Still to fill in by hand: " + ", ".join(remaining))

    return Draft(
        prospect=prospect,
        template_key=template_key,
        subject=SUBJECTS.get(template_key, "LaunchTrace"),
        body=body,
        warnings=warnings,
    )


def write_draft(draft: Draft, directory: Path | None = None) -> Path:
    """Write a draft where the operator can read it. Still not sent."""
    directory = directory or DRAFT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{draft.prospect.prospect_id}_{draft.template_key}.md"
    header = [
        f"# Draft: {draft.template_key} to {draft.prospect.company_name}",
        "",
        f"**To:** {draft.prospect.generic_contact_email or '(no verified address yet)'}",
        f"**Subject:** {draft.subject}",
        "",
        "> This is a draft. Nothing in LaunchTrace sends it. Copy it into your own "
        "mailbox, check it, and send it yourself.",
        "",
    ]
    if draft.warnings:
        header += ["## Before you send", ""]
        header += [f"- {w}" for w in draft.warnings]
        header += [""]
    header += ["---", "", draft.body, ""]
    path.write_text("\n".join(header), encoding="utf-8")
    draft.path = path
    return path


def next_follow_up_date(status: ProspectStatus, from_date: date) -> date | None:
    if status == ProspectStatus.EMAIL_1_SENT:
        return from_date + timedelta(days=FOLLOW_UP_AFTER_EMAIL_1_DAYS)
    if status == ProspectStatus.SAMPLE_SENT:
        return from_date + timedelta(days=FOLLOW_UP_AFTER_SAMPLE_DAYS)
    return None


__all__ = [
    "Draft",
    "DueAction",
    "DueReport",
    "next_follow_up_date",
    "outreach_due",
    "render_draft",
    "write_draft",
]
