from __future__ import annotations

from typer.testing import CliRunner

from mindcap import __version__
from mindcap.cli import app


def test_version_option_prints_installed_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_version_command_prints_installed_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout
