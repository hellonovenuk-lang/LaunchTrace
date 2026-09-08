"""The operator CLI."""

from __future__ import annotations

import pytest

from src.pipeline import build_parser, main


class TestParser:
    @pytest.mark.parametrize(
        "argv",
        [
            ["weekly"],
            ["weekly", "--journal", "2025-052"],
            ["weekly", "--date", "2025-12-26", "--send"],
            ["backfill", "--weeks", "4"],
            ["smoke-test"],
            ["validate", "--weeks", "4"],
            ["status"],
            ["approve", "--run-id", "run_1"],
            ["send", "--run-id", "run_1", "--force"],
            ["regenerate-csv", "--journal", "2025-052"],
            ["opportunities", "--band", "HIGH"],
            ["customers"],
            ["add-customer", "--company", "X", "--email", "a@b.test"],
            ["suppress", "--type", "company", "--value", "X Ltd"],
            ["errors"],
            ["fetch-open-data", "--weeks", "4"],
            ["build-company-index", "--download"],
            ["init-db"],
            ["check-config"],
            ["probe-journal", "some.xml"],
        ],
    )
    def test_documented_commands_parse(self, argv):
        assert build_parser().parse_args(argv).command == argv[0]

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_unknown_command_is_rejected(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["teleport"])

    def test_invalid_band_is_rejected(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["opportunities", "--band", "AMAZING"])


class TestSmokeTest:
    def test_smoke_test_passes_end_to_end(self, tmp_path, capsys):
        assert main(["smoke-test", "--out", str(tmp_path / "smoke")]) == 0
        out = capsys.readouterr().out
        assert "SMOKE TEST: PASS" in out

    def test_smoke_test_writes_every_artefact(self, tmp_path):
        import json

        main(["smoke-test", "--out", str(tmp_path / "smoke")])
        summary = json.loads(
            (tmp_path / "smoke" / "2025-050" / "smoke_validation.json").read_text(encoding="utf-8")
        )
        assert summary["csv_written"] and summary["email_written"] and summary["qa_written"]
        assert summary["deliverable"] > 0


class TestCheckConfig:
    def test_reports_what_is_connected(self, capsys):
        assert main(["check-config"]) == 0
        out = capsys.readouterr().out
        assert "Send mode" in out
        assert "Journal source" in out

    def test_names_missing_credentials(self, capsys):
        main(["check-config"])
        out = capsys.readouterr().out
        assert "COMPANIES_HOUSE_API_KEY" in out or "All integrations are connected" in out


class TestOperatorCommands:
    def test_approve_rejects_an_unknown_run(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.sqlite'}")
        from src.settings import get_settings

        get_settings.cache_clear()
        assert main(["approve", "--run-id", "does_not_exist"]) == 1
        assert "No run found" in capsys.readouterr().out
        get_settings.cache_clear()

    def test_add_customer_enforces_the_plan_recipient_limit(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli2.sqlite'}")
        from src.settings import get_settings

        get_settings.cache_clear()
        code = main(
            [
                "add-customer",
                "--company",
                "X",
                "--email",
                "a@b.test",
                "b@b.test",
                "c@b.test",
                "d@b.test",
            ]
        )
        assert code == 1
        assert "allows 3 recipients" in capsys.readouterr().out
        get_settings.cache_clear()

    def test_add_customer_then_list(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli3.sqlite'}")
        from src.settings import get_settings

        get_settings.cache_clear()
        assert main(["add-customer", "--company", "PackCo Ltd", "--email", "a@b.test"]) == 0
        assert main(["customers"]) == 0
        assert "PackCo Ltd" in capsys.readouterr().out
        get_settings.cache_clear()

    def test_suppress_records_a_rule(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli4.sqlite'}")
        from src.settings import get_settings

        get_settings.cache_clear()
        assert (
            main(["suppress", "--type", "company", "--value", "Bad Ltd", "--reason", "noise"]) == 0
        )
        assert "Bad Ltd" in capsys.readouterr().out
        get_settings.cache_clear()


class TestProbe:
    def test_probe_reports_the_element_names(self, capsys):
        from tests.conftest import FIXTURE_JOURNAL

        assert main(["probe-journal", str(FIXTURE_JOURNAL)]) == 0
        assert "TradeMark" in capsys.readouterr().out
