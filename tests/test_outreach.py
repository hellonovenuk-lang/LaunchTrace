"""Lead matching, previews, drafts, due-state logic and the no-send guarantee.

The no-send tests are the important ones. Everything else here is about
quality; those are about the promise that this repository cannot contact anyone
on its own.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.sales.matching import profile_for, score_fit, select_for_prospect
from src.sales.models import ProspectStatus
from src.sales.outreach import (
    next_follow_up_date,
    outreach_due,
    render_draft,
    write_draft,
)
from src.sales.preview import render_email_block, render_markdown, write_preview_csv
from src.sales.store import SuppressionEntry, SuppressionList
from src.settings import load_config
from tests.conftest import make_lead, make_prospect

PUBLISHED = date(2025, 12, 12)


class TestLeadMatching:
    def test_a_packaging_supplier_gets_packaging_relevant_leads(self):
        packaging = make_lead()
        irrelevant = make_lead(
            brand_name="MARGINAL",
            trademark_number="UK00003900002",
            company_number="14000002",
            company_name="MARGINAL LTD",
            intents={**packaging.intents, "flexible_packaging": "LOW"},
        )
        result = select_for_prospect(
            make_prospect(), [packaging, irrelevant], reference_date=PUBLISHED
        )
        assert [m.lead.brand_name for m in result.matches] == ["CRUMBLEDGE"]

    def test_different_suppliers_rank_the_same_leads_differently(self):
        bars = make_lead(product_category="cereal_bars")
        sauces = make_lead(
            brand_name="SAUCEY",
            trademark_number="UK00003900003",
            company_number="14000003",
            company_name="SAUCEY LTD",
            product_category="sauces_condiments",
            product_category_label="Sauces, condiments, spreads and seasonings",
        )
        leads = [bars, sauces]
        config = load_config("supplier_profiles.json")

        packaging_first = score_fit(bars, profile_for("flexible_packaging", config), config)[0]
        packaging_second = score_fit(sauces, profile_for("flexible_packaging", config), config)[0]
        labels_first = score_fit(sauces, profile_for("labels", config), config)[0]
        labels_second = score_fit(bars, profile_for("labels", config), config)[0]

        assert packaging_first > packaging_second, "bars suit a pouch converter"
        assert labels_first > labels_second, "sauces suit a label printer"
        assert len(leads) == 2

    def test_fewer_than_three_are_returned_rather_than_padded(self):
        result = select_for_prospect(make_prospect(), [make_lead()], reference_date=PUBLISHED)
        assert result.count == 1
        assert result.shortfall_note, "the operator must be told why it is short"

    def test_nothing_relevant_returns_nothing(self):
        unrelated = make_lead(intents={"flexible_packaging": "NONE", "labels": "NONE"})
        result = select_for_prospect(make_prospect(), [unrelated], reference_date=PUBLISHED)
        assert result.count == 0
        assert result.excluded

    def test_suppressed_and_below_band_leads_never_appear(self):
        suppressed = make_lead(suppressed=True)
        low = make_lead(
            trademark_number="UK00003900004", company_number="14000004", band="SUPPRESS", score=20
        )
        unmatched = make_lead(trademark_number="UK00003900005", company_number="")
        result = select_for_prospect(
            make_prospect(), [suppressed, low, unmatched], reference_date=PUBLISHED
        )
        assert result.count == 0
        assert "suppressed" in result.excluded
        assert "no verified Companies House match" in result.excluded

    def test_the_minimum_score_is_the_delivered_threshold(self):
        preview_cfg = load_config("supplier_profiles.json")["preview"]
        just_under = make_lead(score=int(preview_cfg["minimum_score"]) - 1)
        result = select_for_prospect(make_prospect(), [just_under], reference_date=PUBLISHED)
        assert result.count == 0
        assert "below the minimum score" in result.excluded

    def test_the_same_company_is_not_shown_twice(self):
        first = make_lead(brand_name="ONE", trademark_number="UK1")
        second = make_lead(brand_name="TWO", trademark_number="UK2")  # same company number
        third = make_lead(
            brand_name="THREE",
            trademark_number="UK3",
            company_number="14000009",
            company_name="OTHER LTD",
        )
        result = select_for_prospect(
            make_prospect(), [first, second, third], reference_date=PUBLISHED
        )
        companies = {m.lead.company_number for m in result.matches}
        assert len(companies) == result.count

    def test_stale_data_is_flagged_rather_than_silently_dropped(self):
        result = select_for_prospect(make_prospect(), [make_lead()])
        assert result.count == 1, "an old source still produces a preview"
        assert result.stale is True
        assert "days old" in render_markdown(result)

    def test_ranking_never_promotes_a_lead_that_did_not_qualify(self):
        below = make_lead(score=10, band="SUPPRESS")
        result = select_for_prospect(make_prospect(), [below], count=3, reference_date=PUBLISHED)
        assert result.count == 0


class TestPreviewRendering:
    def test_the_briefing_carries_the_evidence_a_reader_needs(self):
        result = select_for_prospect(make_prospect(), [make_lead()], reference_date=PUBLISHED)
        markdown = render_markdown(result)
        assert "CRUMBLEDGE" in markdown
        assert "14000001" in markdown, "the company number is the verification"
        assert "https://example.invalid/tm/UK00003900001" in markdown
        assert "not confirmed purchase intent" in markdown
        assert "Open Government Licence" in markdown

    def test_the_email_block_is_plain_and_specific(self):
        result = select_for_prospect(make_prospect(), [make_lead()], reference_date=PUBLISHED)
        block = render_email_block(result)
        assert block.startswith("- CRUMBLEDGE")
        assert "incorporated" in block
        assert "<" not in block, "this goes into a person's mailbox, not a template"

    def test_an_empty_preview_says_do_not_send(self):
        empty = select_for_prospect(
            make_prospect(), [make_lead(band="SUPPRESS")], reference_date=PUBLISHED
        )
        assert "do not send" in render_email_block(empty).lower()

    def test_a_do_not_contact_prospect_is_marked_in_the_briefing(self):
        prospect = make_prospect(status=ProspectStatus.OPTED_OUT, opted_out=True)
        result = select_for_prospect(prospect, [make_lead()], reference_date=PUBLISHED)
        assert "DO NOT CONTACT" in render_markdown(result)

    def test_the_csv_carries_the_reasoning(self, tmp_path):
        result = select_for_prospect(make_prospect(), [make_lead()], reference_date=PUBLISHED)
        path = write_preview_csv(result, tmp_path / "preview.csv")
        text = path.read_text(encoding="utf-8-sig")
        assert "why_relevant_to_this_supplier" in text
        assert "why_early_stage" in text
        assert "CRUMBLEDGE" in text


class TestDrafting:
    def test_the_lead_block_is_inserted_automatically(self):
        result = select_for_prospect(make_prospect(), [make_lead()], reference_date=PUBLISHED)
        draft = render_draft(make_prospect(), "email_1", preview=result)
        assert "CRUMBLEDGE" in draft.body
        assert "{{" not in draft.body, "every placeholder must be substituted"

    def test_the_supplier_service_is_named_for_this_prospect(self):
        result = select_for_prospect(make_prospect(), [make_lead()], reference_date=PUBLISHED)
        draft = render_draft(make_prospect(), "email_1", preview=result)
        assert "flexible packaging" in draft.body.lower()

    def test_a_draft_without_a_preview_warns_loudly(self):
        draft = render_draft(make_prospect(), "email_1")
        assert "DO NOT SEND" in draft.body
        assert draft.warnings

    def test_a_short_preview_produces_a_warning_not_padding(self):
        result = select_for_prospect(make_prospect(), [make_lead()], reference_date=PUBLISHED)
        draft = render_draft(make_prospect(), "email_1", preview=result)
        assert any("Only 1" in warning for warning in draft.warnings)

    def test_an_unverified_address_is_always_flagged(self):
        draft = render_draft(make_prospect(), "email_3")
        assert any("never guess" in warning for warning in draft.warnings)

    def test_the_sample_count_is_filled_in(self):
        draft = render_draft(make_prospect(), "email_2", sample_count=14)
        assert "14 emerging UK food brands" in draft.body
        assert "[N]" not in draft.body

    def test_every_template_renders(self):
        for key in ("email_1", "email_2", "email_3"):
            draft = render_draft(make_prospect(), key, sample_count=5)
            assert draft.subject
            assert draft.body.strip()

    def test_drafts_carry_an_opt_out_line(self):
        for key in ("email_1", "email_2", "email_3"):
            draft = render_draft(make_prospect(), key, sample_count=5)
            assert "no thanks" in draft.body.lower(), f"{key} must offer a way to stop"


class TestNoSendSafeguards:
    """The promise: this repository cannot contact a prospect on its own."""

    def test_drafting_for_an_opted_out_prospect_is_refused(self):
        prospect = make_prospect(status=ProspectStatus.OPTED_OUT, opted_out=True)
        with pytest.raises(PermissionError):
            render_draft(prospect, "email_1")

    def test_drafting_for_a_suppressed_prospect_is_refused(self):
        prospect = make_prospect(status=ProspectStatus.SUPPRESSED)
        with pytest.raises(PermissionError):
            render_draft(prospect, "email_1")

    def test_a_draft_is_written_to_disk_and_marked_as_a_draft(self, tmp_path):
        draft = render_draft(make_prospect(), "email_3")
        path = write_draft(draft, directory=tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "Nothing in LaunchTrace sends it" in text
        assert "send it yourself" in text

    def test_the_outreach_module_has_no_way_to_send(self):
        import src.sales.outreach as outreach

        source = __import__("pathlib").Path(outreach.__file__).read_text(encoding="utf-8")
        for forbidden in ("EmailSender", "smtplib", "resend", "httpx.post"):
            assert forbidden not in source, (
                f"{forbidden} in the outreach module would break the no-send guarantee"
            )


class TestDueStates:
    def test_a_ready_prospect_is_due_a_first_email(self):
        report = outreach_due([make_prospect(status=ProspectStatus.READY)])
        assert report.actions[0].action == "SEND FIRST EMAIL"
        assert report.actions[0].command

    def test_an_interested_prospect_is_due_the_sample(self):
        report = outreach_due([make_prospect(status=ProspectStatus.REPLIED_INTERESTED)])
        assert report.actions[0].action == "SEND FULL SAMPLE"

    def test_a_follow_up_becomes_due_after_a_week_and_not_before(self):
        prospect = make_prospect(
            status=ProspectStatus.EMAIL_1_SENT, email_1_sent_date=date(2026, 3, 1)
        )
        assert outreach_due([prospect], reference_date=date(2026, 3, 4)).actions == []
        later = outreach_due([prospect], reference_date=date(2026, 3, 10))
        assert later.actions[0].action == "SEND FOLLOW-UP"

    def test_an_opted_out_prospect_is_never_due_anything(self):
        prospect = make_prospect(status=ProspectStatus.OPTED_OUT, opted_out=True)
        report = outreach_due([prospect])
        assert report.actions == []
        assert prospect in report.do_not_contact

    def test_the_suppression_list_overrides_a_ready_status(self):
        prospect = make_prospect(status=ProspectStatus.READY)
        suppressions = SuppressionList(
            entries=[SuppressionEntry(value="pouchworks.test", kind="domain")]
        )
        report = outreach_due([prospect], suppressions=suppressions)
        assert report.actions == []
        assert report.blocked_by_suppression[0][0] is prospect

    def test_a_subscriber_is_reported_separately_from_the_work_list(self):
        prospect = make_prospect(status=ProspectStatus.SUBSCRIBED)
        report = outreach_due([prospect])
        assert report.actions == []
        assert prospect in report.subscribed

    def test_revenue_work_is_ordered_ahead_of_research(self):
        report = outreach_due(
            [
                make_prospect(prospect_id="P001", status=ProspectStatus.RESEARCHED),
                make_prospect(
                    prospect_id="P002",
                    company_name="Second Ltd",
                    website="https://second.test/",
                    status=ProspectStatus.REPLIED_INTERESTED,
                ),
            ]
        )
        assert report.actions[0].action == "SEND FULL SAMPLE"

    def test_only_one_follow_up_is_ever_scheduled(self):
        assert next_follow_up_date(ProspectStatus.EMAIL_1_SENT, date(2026, 3, 1))
        assert next_follow_up_date(ProspectStatus.SAMPLE_SENT, date(2026, 3, 1))
        assert next_follow_up_date(ProspectStatus.NO_RESPONSE, date(2026, 3, 1)) is None
        assert next_follow_up_date(ProspectStatus.NOT_NOW, date(2026, 3, 1)) is None
