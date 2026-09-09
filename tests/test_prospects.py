"""The prospect tracker: ICP scoring, lifecycle, duplicates and suppression.

The suppression and duplicate tests matter more than the scoring ones. A wrong
ICP score costs ten wasted minutes; contacting someone who asked you not to
costs the relationship and is a compliance problem.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.sales.icp import apply_scores, score_prospect
from src.sales.models import (
    Priority,
    ProspectStatus,
    ReplyState,
    TransitionError,
    check_transition,
    normalise_company,
    normalise_domain,
)
from src.sales.store import (
    DuplicateProspectError,
    SuppressedProspectError,
    SuppressionEntry,
    load_prospects,
    load_store,
    load_suppressions,
    save_prospects,
    save_suppressions,
)
from tests.conftest import make_prospect


class TestIcpScoring:
    def test_strong_fit_scores_higher_than_weak_fit(self):
        strong = make_prospect(
            icp_reason="Printed pouches for food startups, low MOQ and short runs."
        )
        weak = make_prospect(
            prospect_id="P002",
            company_name="Generic Industrial Films",
            supplier_category="other",
            icp_reason="Industrial films for automotive and construction.",
        )
        assert score_prospect(strong).score > score_prospect(weak).score

    def test_every_score_carries_its_reasons(self):
        result = score_prospect(make_prospect())
        assert result.signals, "a score with no reasons cannot be argued with"
        assert all(signal.label for signal in result.signals)
        assert any("startup" in signal.matched for signal in result.signals if signal.matched)

    def test_wrong_sector_is_penalised(self):
        result = score_prospect(
            make_prospect(icp_reason="Aerospace and automotive components only.")
        )
        assert any(signal.key == "wrong_sector" for signal in result.negatives)

    def test_opted_out_is_hard_suppressed_regardless_of_fit(self):
        prospect = make_prospect(opted_out=True)
        result = score_prospect(prospect)
        assert result.priority is Priority.SUPPRESS
        assert result.score == 0
        assert result.hard_suppress_reason

    def test_not_trading_is_hard_suppressed(self):
        result = score_prospect(make_prospect(notes="Dissolved in 2024."))
        assert result.priority is Priority.SUPPRESS
        assert "dissolved" in (result.hard_suppress_reason or "").lower()

    def test_bands_follow_the_configured_thresholds(self):
        config = {
            "bands": {"A": 30, "B": 20, "C": 10},
            "category_weights": {"flexible_packaging": 25},
            "positive_signals": [],
            "negative_signals": [],
            "hard_suppress": {},
            "not_trading_markers": [],
        }
        assert score_prospect(make_prospect(), config).priority is Priority.B
        config["bands"]["A"] = 25
        assert score_prospect(make_prospect(), config).priority is Priority.A

    def test_apply_scores_writes_back_without_reviving_anyone(self):
        opted_out = make_prospect(
            prospect_id="P002", status=ProspectStatus.OPTED_OUT, opted_out=True
        )
        prospects = [make_prospect(), opted_out]
        apply_scores(prospects)
        assert prospects[0].priority is not Priority.SUPPRESS
        assert opted_out.status is ProspectStatus.OPTED_OUT
        assert opted_out.priority is Priority.SUPPRESS

    def test_scoring_never_mutates_the_prospect(self):
        prospect = make_prospect(icp_score=0, priority=Priority.C)
        score_prospect(prospect)
        assert prospect.icp_score == 0
        assert prospect.priority is Priority.C


class TestStatusTransitions:
    def test_the_happy_path_is_allowed(self):
        sequence = [
            ProspectStatus.RESEARCHED,
            ProspectStatus.READY,
            ProspectStatus.EMAIL_1_SENT,
            ProspectStatus.REPLIED_INTERESTED,
            ProspectStatus.SAMPLE_REQUESTED,
            ProspectStatus.SAMPLE_SENT,
            ProspectStatus.OFFER_SENT,
            ProspectStatus.SUBSCRIBED,
        ]
        for current, target in zip(sequence, sequence[1:], strict=False):
            check_transition(current, target)

    def test_skipping_the_first_email_is_refused(self):
        with pytest.raises(TransitionError):
            check_transition(ProspectStatus.RESEARCHED, ProspectStatus.SAMPLE_SENT)

    def test_opting_out_is_one_way(self):
        for target in (ProspectStatus.READY, ProspectStatus.SUBSCRIBED):
            with pytest.raises(TransitionError):
                check_transition(ProspectStatus.OPTED_OUT, target)

    def test_the_same_status_is_always_allowed(self):
        check_transition(ProspectStatus.READY, ProspectStatus.READY)

    def test_set_status_stamps_the_matching_date(self, prospect_store):
        prospect = prospect_store.add(make_prospect(prospect_id=""))
        prospect_store.set_status(prospect, ProspectStatus.READY)
        prospect_store.set_status(prospect, ProspectStatus.EMAIL_1_SENT, when=date(2026, 2, 3))
        assert prospect.email_1_sent_date == date(2026, 2, 3)

        prospect_store.set_status(prospect, ProspectStatus.SAMPLE_REQUESTED, when=date(2026, 2, 5))
        prospect_store.set_status(prospect, ProspectStatus.SAMPLE_SENT, when=date(2026, 2, 6))
        prospect_store.set_status(prospect, ProspectStatus.OFFER_SENT, when=date(2026, 2, 9))
        prospect_store.set_status(prospect, ProspectStatus.SUBSCRIBED, when=date(2026, 2, 12))
        assert prospect.sample_requested_date == date(2026, 2, 5)
        assert prospect.sample_sent_date == date(2026, 2, 6)
        assert prospect.offer_sent_date == date(2026, 2, 9)
        assert prospect.converted_date == date(2026, 2, 12)

    def test_a_suppressed_prospect_cannot_be_moved(self, prospect_store):
        prospect = prospect_store.add(make_prospect(prospect_id=""))
        prospect_store.set_status(prospect, ProspectStatus.SUPPRESSED, note="wrong sector")
        with pytest.raises(SuppressedProspectError):
            prospect_store.set_status(prospect, ProspectStatus.READY)

    def test_conversion_clears_any_pending_follow_up(self, prospect_store):
        prospect = prospect_store.add(make_prospect(prospect_id=""))
        prospect_store.set_status(prospect, ProspectStatus.READY)
        prospect_store.set_status(prospect, ProspectStatus.EMAIL_1_SENT)
        prospect.follow_up_due_date = date(2026, 3, 1)
        prospect_store.set_status(prospect, ProspectStatus.SAMPLE_REQUESTED)
        prospect_store.set_status(prospect, ProspectStatus.SAMPLE_SENT)
        prospect_store.set_status(prospect, ProspectStatus.SUBSCRIBED)
        assert prospect.follow_up_due_date is None


class TestDuplicateProtection:
    def test_the_same_company_name_is_refused(self, prospect_store):
        prospect_store.add(make_prospect(prospect_id=""))
        with pytest.raises(DuplicateProspectError):
            prospect_store.add(
                make_prospect(prospect_id="", company_name="Pouchworks Limited", website="")
            )

    def test_the_same_domain_is_refused(self, prospect_store):
        prospect_store.add(make_prospect(prospect_id=""))
        with pytest.raises(DuplicateProspectError):
            prospect_store.add(
                make_prospect(
                    prospect_id="",
                    company_name="Something Else Entirely",
                    website="http://www.pouchworks.test/contact",
                )
            )

    def test_the_same_company_number_is_refused(self, prospect_store):
        prospect_store.add(make_prospect(prospect_id="", companies_house_number="09876543"))
        with pytest.raises(DuplicateProspectError):
            prospect_store.add(
                make_prospect(
                    prospect_id="",
                    company_name="Different Name",
                    website="https://other.test/",
                    companies_house_number="9876543",
                )
            )

    def test_a_genuinely_different_business_is_accepted(self, prospect_store):
        prospect_store.add(make_prospect(prospect_id=""))
        second = prospect_store.add(
            make_prospect(
                prospect_id="",
                company_name="Labelcraft Ltd",
                website="https://labelcraft.test/",
                supplier_category="labels",
            )
        )
        assert second.prospect_id == "P002"
        assert len(prospect_store.prospects) == 2

    def test_duplicates_are_reported_until_one_is_suppressed(self, prospect_store):
        first = make_prospect(prospect_id="P001")
        second = make_prospect(prospect_id="P002", company_name="Pouchworks Ltd.", website="")
        prospect_store.prospects.extend([first, second])
        assert len(prospect_store.duplicates()) == 1
        prospect_store.set_status(second, ProspectStatus.SUPPRESSED, note="duplicate of P001")
        assert prospect_store.duplicates() == []
        assert len(prospect_store.duplicates(unresolved_only=False)) == 1

    def test_identity_normalisation(self):
        assert normalise_domain("https://WWW.Example.co.uk/contact") == "example.co.uk"
        assert normalise_company("The Pouchworks Group Limited") == normalise_company("Pouchworks")


class TestSuppression:
    def test_opt_out_records_every_identity(self, prospect_store):
        prospect = prospect_store.add(
            make_prospect(prospect_id="", generic_contact_email="sales@pouchworks.test")
        )
        prospect_store.opt_out(prospect, reason="asked not to be contacted")

        assert prospect.opted_out is True
        assert prospect.status is ProspectStatus.OPTED_OUT
        assert prospect.reply_state is ReplyState.OPT_OUT
        assert prospect.contactable is False
        assert "sales@pouchworks.test" in prospect_store.suppressions.emails
        assert "pouchworks.test" in prospect_store.suppressions.domains
        assert normalise_company("Pouchworks Ltd") in prospect_store.suppressions.companies

    def test_a_suppressed_business_cannot_be_re_imported(self, prospect_store):
        prospect_store.suppressions.add(
            SuppressionEntry(value="pouchworks.test", kind="domain", reason="opted out")
        )
        with pytest.raises(SuppressedProspectError):
            prospect_store.add(make_prospect(prospect_id=""))

    def test_re_import_is_blocked_even_under_a_different_name(self, prospect_store):
        prospect_store.suppressions.add(
            SuppressionEntry(value="Pouchworks Ltd", kind="company", reason="opted out")
        )
        with pytest.raises(SuppressedProspectError):
            prospect_store.add(
                make_prospect(
                    prospect_id="",
                    company_name="THE POUCHWORKS COMPANY LIMITED",
                    website="https://elsewhere.test/",
                )
            )

    def test_saving_can_never_shorten_the_suppression_list(self, tmp_path):
        path = tmp_path / "suppressions.csv"
        from src.sales.store import SuppressionList

        original = SuppressionList(
            entries=[
                SuppressionEntry(value="a@example.test", kind="email"),
                SuppressionEntry(value="b@example.test", kind="email"),
            ]
        )
        save_suppressions(original, path)
        # Someone saves a list that has lost an entry. The union survives.
        save_suppressions(
            SuppressionList(entries=[SuppressionEntry(value="a@example.test", kind="email")]),
            path,
        )
        assert len(load_suppressions(path).entries) == 2

    def test_an_older_suppression_file_still_loads(self, tmp_path):
        path = tmp_path / "suppressions.csv"
        path.write_text(
            "email,company_name,date_added,reason,added_by\n"
            "old@example.test,Old Ltd,2026-01-01,opted out,operator\n",
            encoding="utf-8",
        )
        loaded = load_suppressions(path)
        assert "old@example.test" in loaded.emails


class TestPersistence:
    def test_a_prospect_survives_a_round_trip(self, tmp_path):
        path = tmp_path / "prospects.csv"
        original = make_prospect(
            status=ProspectStatus.SAMPLE_SENT,
            priority=Priority.A,
            icp_score=71,
            buying_intent_categories=["flexible_packaging", "labels"],
            sample_sent_date=date(2026, 2, 6),
            opted_out=False,
            notes="Spoke to them at a trade show.",
        )
        save_prospects([original], path)
        restored = load_prospects(path)[0]

        assert restored.company_name == original.company_name
        assert restored.status is ProspectStatus.SAMPLE_SENT
        assert restored.priority is Priority.A
        assert restored.icp_score == 71
        assert restored.buying_intent_categories == ["flexible_packaging", "labels"]
        assert restored.sample_sent_date == date(2026, 2, 6)
        assert restored.opted_out is False
        assert restored.notes == original.notes

    def test_saving_keeps_the_previous_version(self, tmp_path):
        path = tmp_path / "prospects.csv"
        save_prospects([make_prospect()], path)
        save_prospects([make_prospect(company_name="Renamed Ltd")], path)
        assert (tmp_path / "backups" / "prospects.previous.csv").exists()

    def test_ids_are_assigned_without_reuse(self, prospect_store):
        prospect_store.prospects.append(make_prospect(prospect_id="P007"))
        added = prospect_store.add(
            make_prospect(prospect_id="", company_name="Newco", website="https://newco.test/")
        )
        assert added.prospect_id == "P008"


class TestTheRealProspectList:
    """The list shipped in the repository must stay usable."""

    def test_it_loads_and_is_free_of_unresolved_duplicates(self):
        store = load_store()
        assert len(store.prospects) >= 55
        assert store.duplicates() == [], "resolve duplicates before contacting anyone"

    def test_every_row_has_the_research_that_makes_it_worth_contacting(self):
        for prospect in load_store().prospects:
            assert prospect.company_name, "a row with no company name cannot be verified"
            assert prospect.icp_reason, f"{prospect.prospect_id} has no stated reason to contact"
            assert prospect.supplier_category

    def test_no_contact_address_was_invented(self):
        """An address must always come with a recorded source."""
        from src.sales.models import EmailSource

        for prospect in load_store().prospects:
            if prospect.generic_contact_email:
                assert prospect.email_source is not EmailSource.NONE, (
                    f"{prospect.prospect_id} has an address with no recorded source"
                )

    def test_every_row_is_scored_and_prioritised(self):
        store = load_store()
        assert all(p.priority for p in store.prospects)
        assert any(p.priority is Priority.A for p in store.prospects), (
            "no Priority A prospects means there is nobody to start with"
        )
