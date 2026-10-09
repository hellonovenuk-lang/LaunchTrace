"""The weekly GitHub Actions workflow: valid, injection-free, never silently loses the DB.

The recovery step's shell script is run for real against a fake ``gh`` on PATH
(no network): cache hit, restore from a backup artifact, first ever run, and the
refusal when history exists but cannot be found (D-702).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.settings import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "weekly-pipeline.yml"


def _steps() -> list[dict[str, Any]]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["run"]["steps"]


def _step(step_id: str) -> dict[str, Any]:
    return next(s for s in _steps() if s.get("id") == step_id)


def test_workflow_is_valid_yaml_with_actions_read():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert doc["jobs"]["run"]["permissions"]["actions"] == "read"


def test_no_input_is_interpolated_into_a_script():
    """Inputs reach `run:` scripts through env vars only (LOW-6 injection)."""
    for step in _steps():
        script = step.get("run") or ""
        assert not re.search(r"\$\{\{\s*(github\.event\.inputs|inputs)\.", script), step.get("name")


def test_recovery_runs_between_restore_and_pipeline():
    names = [s.get("id") or s.get("name") for s in _steps()]
    assert (
        names.index("Restore the local SQLite database")
        < names.index("dbrecover")
        < names.index("pipeline")
    )


def test_steps_that_write_the_database_skip_after_a_refused_recovery():
    for step in _steps():
        cond = str(step.get("if", ""))
        name = step.get("name", "")
        if "always()" in cond and any(
            word in name for word in ("Re-scan", "retention", "public feed", "SQLite")
        ):
            if name == "Upload the public feed":
                continue
            assert "steps.dbrecover.outcome != 'failure'" in cond, name


# ---------------------------------------------------------------------------
# the recovery script, run with a fake gh
# ---------------------------------------------------------------------------

FAKE_GH = """#!/usr/bin/env bash
# args are logged; behaviour comes from env FAKE_*.
echo "$@" >> "$FAKE_LOG"
case "$1 $2" in
  "api repos/"*) printf '%s' "$FAKE_ARTIFACT" ;;
  "run download")
    shift 2
    dir=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "--dir" ]; then dir="$2"; fi
      shift
    done
    if [ -n "$FAKE_DOWNLOAD" ]; then cp "$FAKE_DOWNLOAD" "$dir/launchtrace.sqlite"; else exit 1; fi
    ;;
  "run list") printf '%s' "$FAKE_PREVIOUS" ;;
  *) exit 2 ;;
esac
"""


@pytest.fixture
def run_recovery(tmp_path: Path):  # type: ignore[no-untyped-def]
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("bash not available")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "work"
    work.mkdir()
    script = _step("dbrecover")["run"]

    def run(**fake: str) -> tuple[int, dict[str, str], str, Path]:
        out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
        for f in (out, summary):
            f.write_text("", encoding="utf-8")
        env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_REPOSITORY": "owner/launchtrace",
            "CURRENT_RUN_ID": "999",
            "EVENT_NAME": fake.pop("EVENT_NAME", "schedule"),
            "FRESH_DATABASE": fake.pop("FRESH_DATABASE", ""),
            "FAKE_LOG": str(tmp_path / "gh.log"),
            "FAKE_ARTIFACT": "",
            "FAKE_DOWNLOAD": "",
            "FAKE_PREVIOUS": "3",
            **fake,
        }
        proc = subprocess.run(
            ["bash", "-e", "-c", script], cwd=work, env=env, capture_output=True, text=True
        )
        outputs = dict(
            line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if line
        )
        return proc.returncode, outputs, summary.read_text(encoding="utf-8"), work

    return run


def test_a_cache_hit_needs_nothing(run_recovery, tmp_path: Path):
    db = tmp_path / "work" / "data" / "local" / "launchtrace.sqlite"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"SQLite format 3\0")
    code, outputs, summary, _ = run_recovery()
    assert code == 0 and outputs["source"] == "cache" and summary == ""
    assert not (tmp_path / "gh.log").exists(), "gh is not called on a cache hit"


def test_restores_the_newest_backup_artifact(run_recovery, tmp_path: Path):
    backup = tmp_path / "backup.sqlite"
    backup.write_bytes(b"SQLite format 3\0backup")
    code, outputs, summary, work = run_recovery(
        FAKE_ARTIFACT="12345 launchtrace-db-41", FAKE_DOWNLOAD=str(backup)
    )
    assert code == 0 and outputs["source"] == "artifact"
    assert (work / "data/local/launchtrace.sqlite").read_bytes() == backup.read_bytes()
    assert "launchtrace-db-41" in summary
    assert "run download 12345" in (tmp_path / "gh.log").read_text(encoding="utf-8")


def test_refuses_an_empty_database_when_history_exists(run_recovery):
    code, outputs, summary, work = run_recovery(FAKE_PREVIOUS="3")
    assert code != 0
    assert "source" not in outputs
    assert "Run stopped" in summary
    assert not (work / "data/local/launchtrace.sqlite").exists()


def test_refuses_when_the_run_history_cannot_be_read(run_recovery):
    code, _, summary, _ = run_recovery(FAKE_PREVIOUS="")
    assert code != 0 and "Run stopped" in summary


def test_the_first_ever_run_starts_fresh_and_says_so(run_recovery):
    code, outputs, summary, work = run_recovery(FAKE_PREVIOUS="0")
    assert code == 0 and outputs["source"] == "fresh"
    assert "History lost" in summary and "first run" in summary
    assert (work / "data/local/.fresh-database").exists()


def test_a_person_can_ask_for_a_fresh_database(run_recovery):
    code, outputs, summary, _ = run_recovery(
        FAKE_PREVIOUS="3", EVENT_NAME="workflow_dispatch", FRESH_DATABASE="true"
    )
    assert code == 0 and outputs["source"] == "fresh"
    assert "fresh_database" in summary


def test_a_scheduled_run_cannot_ask_for_a_fresh_database(run_recovery):
    code, _, _, _ = run_recovery(FAKE_PREVIOUS="3", EVENT_NAME="schedule", FRESH_DATABASE="true")
    assert code != 0
