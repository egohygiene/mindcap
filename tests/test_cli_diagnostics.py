"""Real command boundaries, output channels, and failure exit statuses."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import typer
from typer.testing import CliRunner

from mindcap.core.errors import AuthenticationRequiredError
from mindcap.sync.models import BatchRunConfig, BatchRunState, RunStatus
from mindcap_cli.app import app


@pytest.mark.parametrize("json_output", [False, True])
def test_expected_error_is_actionable_and_never_on_stdout(json_output: bool) -> None:
    args = ["capture", "suno", "not-a-workspace"]
    if json_output:
        args.append("--json")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert result.stdout == ""
    if json_output:
        assert json.loads(result.stderr)["error"]["code"] == "invalid_source"
    else:
        assert "Next step:" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("debug", [False, True])
def test_unexpected_error_is_caught_without_dumping_locals(debug: bool) -> None:
    args = ["--debug"] if debug else []
    with patch(
        "mindcap_cli.app.verify_bundle",
        side_effect=RuntimeError(
            "Failed https://user:private@cdn.test/audio?Signature=secret "
            "Authorization: Bearer hidden"
        ),
    ):
        result = CliRunner().invoke(app, [*args, "verify", "/synthetic/archive"])
    assert result.exit_code == 1
    assert "unexpected_error" in result.stderr
    assert "Next step:" in result.stderr
    for secret in ("private", "secret", "hidden", "Traceback (most recent"):
        assert secret not in result.output
    assert (" in verify" in result.stderr) == debug


def test_auth_error_uses_same_boundary_as_capture() -> None:
    with patch(
        "mindcap_cli.app.authenticate_distrokid",
        side_effect=AuthenticationRequiredError("Session expired"),
    ):
        result = CliRunner().invoke(app, ["auth", "distrokid"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "authentication_required" in result.stderr
    assert "mindcap auth" in result.stderr


@pytest.mark.parametrize("error", [KeyboardInterrupt(), typer.Abort()])
def test_interruption_is_clean_and_nonzero(error: BaseException) -> None:
    with patch("mindcap_cli.app.verify_bundle", side_effect=error):
        result = CliRunner().invoke(app, ["verify", "/synthetic/archive"])
    assert result.exit_code == 130
    assert result.stdout == ""
    assert "interrupted" in result.stderr
    assert "Traceback" not in result.output


def test_global_debug_file_applies_to_nested_command(tmp_path: Path) -> None:
    destination = tmp_path / "diagnostics.jsonl"
    result = CliRunner().invoke(
        app, ["--debug", "--log-file", str(destination), "plugins", "list"]
    )
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in destination.read_text().splitlines()]
    assert any(record["level"] == "DEBUG" for record in records)
    assert any(record.get("event") == "command_completed" for record in records)
    assert "Source plugin" in result.stdout
    assert "Starting" in result.stderr


def test_log_setup_failure_prevents_command_and_has_no_traceback(
    tmp_path: Path,
) -> None:
    with patch("mindcap_cli.app.verify_bundle") as verify:
        result = CliRunner().invoke(
            app,
            ["--log-file", str(tmp_path / "missing" / "log"), "verify", "/archive"],
        )
    assert result.exit_code == 1
    assert "diagnostic_log_unavailable" in result.stderr
    assert result.stdout == ""
    verify.assert_not_called()


def test_global_quiet_reaches_capture_but_preserves_warning(tmp_path: Path) -> None:
    plugin = Mock()
    plugin.default_strategy.return_value = "api"
    plugin.canonicalize.return_value = ("unit-1", None)
    plugin.strategy.return_value.capture.return_value = Mock(
        safe_metadata={"capture_complete": False},
        warnings=["A required asset could not be downloaded (403)."],
    )
    plugin.normalize.return_value = {}
    plugin.storage.return_value.persist.return_value = Mock(
        status="complete", source_id="unit-1", version=1, path=tmp_path / "v1"
    )
    with patch("mindcap_cli.app.build_registry") as registry:
        registry.return_value.get.return_value = plugin
        result = CliRunner().invoke(app, ["--quiet", "capture", "suno", "unit-1"])
    assert result.exit_code == 2, result.output
    assert result.stdout.strip() == str(tmp_path / "v1")
    assert "403" in result.stderr
    assert "Authenticating" not in result.output
    assert "INFO" not in result.stderr


def test_sync_json_stdout_excludes_progress_and_resume_advice(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    config = BatchRunConfig(provider="suno", collection_identifier="suno-account")
    state = BatchRunState(
        run_id="synthetic-run",
        provider="suno",
        collection_identifier="suno-account",
        config=config,
        config_fingerprint=config.fingerprint(),
        started_at=now,
        updated_at=now,
        status=RunStatus.INTERRUPTED,
    )
    with patch("mindcap.sync.runner.SyncRunner.run", return_value=state):
        result = CliRunner().invoke(
            app, ["sync", "suno", "--json", "--output", str(tmp_path)]
        )
    assert result.exit_code == 130, result.output
    assert json.loads(result.stdout)["run_id"] == "synthetic-run"
    assert "Authenticating" in result.stderr
    assert "mindcap sync suno --resume" in result.stderr


def test_nested_usage_error_keeps_usage_exit_code() -> None:
    result = CliRunner().invoke(app, ["inspect", "suno"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "invalid_usage" in result.stderr
    assert "--help" in result.stderr


@pytest.mark.parametrize("json_first", [False, True])
def test_json_usage_error_is_structured_even_before_json_is_converted(
    json_first: bool,
) -> None:
    args = ["--max-items", "token=private-value"]
    args = ["--json", *args] if json_first else [*args, "--json"]
    result = CliRunner().invoke(app, ["sync", "suno", *args])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "invalid_usage"
    assert "private-value" not in result.output


def test_top_level_usage_error_uses_safe_boundary() -> None:
    result = CliRunner().invoke(app, ["--token=private-value"])
    assert result.exit_code == 2
    assert "invalid_usage" in result.stderr
    assert "private-value" not in result.output
