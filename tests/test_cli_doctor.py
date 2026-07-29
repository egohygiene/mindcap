from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from mindcap.cli import app
from mindcap.plugins.chatgpt.strategies.browser import (
    AuthenticationCheck,
    AuthenticationState,
)


def test_doctor_chatgpt_reports_safe_status_fields() -> None:
    runner = CliRunner()
    with (
        patch("mindcap.cli._find_stable_chrome", return_value=Path("/tmp/chrome")),
        patch("mindcap.cli.chatgpt_profile_dir", return_value=Path("/tmp/profile")),
        patch("mindcap.cli._is_profile_locked", return_value=False),
        patch("mindcap.cli._is_dedicated_chrome_running", return_value=False),
        patch(
            "mindcap.cli.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                state=AuthenticationState.VERIFIED,
                detail="ok",
            ),
        ),
        patch("mindcap.cli._is_artifact_root_git_ignored", return_value=True),
    ):
        result = runner.invoke(app, ["doctor", "chatgpt"])

    assert result.exit_code == 0
    assert "Authentication status" in result.stdout
    assert "verified" in result.stdout
    assert "Selected capture architecture" in result.stdout
