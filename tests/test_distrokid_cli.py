from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from mindcap_cli.app import app


def test_auth_distrokid_invokes_authenticator() -> None:
    runner = CliRunner()
    with patch("mindcap_cli.app.authenticate_distrokid") as mock_auth:
        result = runner.invoke(app, ["auth", "distrokid"])

    assert result.exit_code == 0
    mock_auth.assert_called_once_with()
    assert "Dedicated DistroKid profile saved" in result.stdout


def test_doctor_distrokid_invokes_doctor() -> None:
    runner = CliRunner()
    with patch("mindcap_cli.app.run_distrokid_doctor") as mock_doctor:
        result = runner.invoke(app, ["doctor", "distrokid", "--verbose"])

    assert result.exit_code == 0
    assert mock_doctor.called
