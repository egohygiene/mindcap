from __future__ import annotations

import io
import json
import logging
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mindcap.core.errors import AuthenticationRequiredError
from mindcap_cli.logging import (
    DiagnosticLogError,
    diagnostic_session,
    render_error,
)


def test_terminal_diagnostics_are_literal_redacted_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with diagnostic_session():
        logging.getLogger("mindcap.plugins.suno").warning(
            "[bold]HTTP 403[/bold] %s Authorization: Bearer credential-secret",
            "https://user:password-secret@cdn.example/audio.mp3?token=signed-secret",
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "WARNING" in captured.err
    assert "[bold]HTTP 403[/bold]" in captured.err
    assert "cdn.example/audio.mp3" in captured.err
    for secret in ("credential-secret", "password-secret", "signed-secret"):
        assert secret not in captured.err


@pytest.mark.parametrize(
    ("options", "expected", "absent"),
    [
        ({}, ("info event", "warning event"), ("debug event",)),
        ({"verbose": True}, ("info event", "warning event"), ("debug event",)),
        ({"debug": True}, ("debug event", "info event", "warning event"), ()),
        ({"quiet": True}, ("warning event",), ("debug event", "info event")),
    ],
)
def test_session_levels(
    options: dict[str, bool],
    expected: tuple[str, ...],
    absent: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with diagnostic_session(
        verbose=options.get("verbose", False),
        debug=options.get("debug", False),
        quiet=options.get("quiet", False),
    ):
        logger = logging.getLogger("mindcap.cli")
        logger.debug("debug event")
        logger.info("info event")
        logger.warning("warning event")
    output = capsys.readouterr().err
    assert all(message in output for message in expected)
    assert all(message not in output for message in absent)


def test_progress_events_reach_log_file_without_repeating_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "capture.jsonl"
    with diagnostic_session(log_file=path):
        logging.getLogger("mindcap.capture").warning(
            "asset warning",
            extra={"progress_event": True, "event": "capture_warning"},
        )
    assert capsys.readouterr().err == ""
    record = json.loads(path.read_text())
    assert record["message"] == "asset warning"
    assert record["event"] == "capture_warning"


def test_jsonl_logs_append_with_private_permissions_and_no_ansi(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    path.write_text('{"previous": true}\n')
    path.chmod(0o644)
    for _ in range(2):
        with diagnostic_session(log_file=path):
            logging.getLogger("mindcap.cli").info(
                "\x1b[31mGET https://cdn.example/a?signature=signed-secret#private "
                "Cookie: session=cookie-secret; other=other-secret"
            )
    raw = path.read_text()
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == 3
    assert records[0] == {"previous": True}
    assert records[1]["level"] == "INFO"
    assert records[1]["logger"] == "mindcap.cli"
    assert records[1]["timestamp"].endswith("+00:00")
    assert "\x1b" not in raw
    for secret in ("signed-secret", "private", "cookie-secret", "other-secret"):
        assert secret not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_symlink_log_refused_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("preserve me")
    link = tmp_path / "capture.jsonl"
    link.symlink_to(target)
    with (
        pytest.raises(DiagnosticLogError, match="symbolic link"),
        diagnostic_session(log_file=link),
    ):
        pytest.fail("Session must refuse a symbolic link")
    assert target.read_text() == "preserve me"


def test_hardlinked_log_refused_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("preserve me")
    link = tmp_path / "capture.jsonl"
    os.link(target, link)
    with (
        pytest.raises(DiagnosticLogError, match="without links"),
        diagnostic_session(log_file=link),
    ):
        pytest.fail("Session must refuse a hard link")
    assert target.read_text() == "preserve me"


@pytest.mark.parametrize("destination", ["missing/capture.jsonl", "."])
def test_bad_log_destination_fails_concisely(tmp_path: Path, destination: str) -> None:
    with (
        pytest.raises(DiagnosticLogError, match="Cannot open diagnostic log"),
        diagnostic_session(log_file=tmp_path / destination),
    ):
        pytest.fail("Session must refuse an invalid destination")


def test_fifo_log_destination_fails_without_waiting_for_reader(tmp_path: Path) -> None:
    destination = tmp_path / "capture.fifo"
    os.mkfifo(destination)
    with pytest.raises(DiagnosticLogError), diagnostic_session(log_file=destination):
        pytest.fail("Session must refuse a FIFO")


@pytest.mark.parametrize("fail", [False, True])
def test_session_restores_logging_and_closes_files(
    monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    namespace = logging.getLogger("mindcap")
    snapshot = (
        list(namespace.handlers),
        namespace.level,
        namespace.propagate,
        namespace.disabled,
    )
    stream = io.StringIO()
    monkeypatch.setattr("mindcap_cli.logging._open_log_file", lambda path: stream)
    try:
        with diagnostic_session(debug=True, log_file=Path("unused.jsonl")):
            logging.getLogger("mindcap.capture").debug("within session")
            if fail:
                raise ValueError("expected test failure")
    except ValueError:
        assert fail
    assert stream.closed
    assert (
        list(namespace.handlers),
        namespace.level,
        namespace.propagate,
        namespace.disabled,
    ) == snapshot


def test_session_leaves_root_and_http_loggers_untouched(tmp_path: Path) -> None:
    root = logging.getLogger()
    http = logging.getLogger("httpx")
    snapshot = (
        list(root.handlers),
        root.level,
        list(http.handlers),
        http.level,
        http.propagate,
        http.disabled,
    )
    path = tmp_path / "capture.jsonl"
    with diagnostic_session(debug=True, log_file=path):
        logging.getLogger("mindcap.capture").info("safe event")
        assert (
            list(root.handlers),
            root.level,
            list(http.handlers),
            http.level,
            http.propagate,
            http.disabled,
        ) == snapshot
    assert len(path.read_text().splitlines()) == 1


def test_json_error_uses_stderr_stable_code_and_sanitization(
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_error(
        AuthenticationRequiredError("Cookie: session=private-cookie"), json_output=True
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "authentication_required"
    assert payload["error"]["type"] == "AuthenticationRequiredError"
    assert "mindcap auth" in payload["error"]["hint"]
    assert "private-cookie" not in captured.err
    assert "\x1b" not in captured.err
    assert "frames" not in payload


def test_debug_error_has_only_sanitized_frame_locations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_with_private_local() -> None:
        private_capture_body = "private-conversation-body"
        assert private_capture_body
        raise RuntimeError("Failed https://cdn.example/a?token=signed-secret")

    path = tmp_path / "debug.jsonl"
    with diagnostic_session(debug=True, log_file=path):
        try:
            fail_with_private_local()
        except RuntimeError as error:
            render_error(error, json_output=True, debug=True)
    stderr = capsys.readouterr().err
    payload = json.loads(stderr)
    assert payload["error"]["type"] == "RuntimeError"
    assert any("in fail_with_private_local" in frame for frame in payload["frames"])
    assert all(frame.startswith("test_cli_logging.py:") for frame in payload["frames"])
    for output in (stderr, path.read_text()):
        assert "signed-secret" not in output
        assert "private-conversation-body" not in output
        assert "private_capture_body" not in output
        assert "raise RuntimeError" not in output
        assert "Traceback" not in output


def test_pretty_error_logged_once_without_markup_interpretation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "error.jsonl"
    with diagnostic_session(log_file=path):
        render_error(ValueError("[bold]bad input[/bold]"))
    stderr = capsys.readouterr().err
    assert stderr.count("[bold]bad input[/bold]") == 1
    assert "Mindcap error" in stderr
    assert "invalid_input" in stderr
    assert "Next step" in stderr
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["error"]["code"] == "invalid_input"


def test_bad_log_message_does_not_emit_logging_internal_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "error.jsonl"
    with diagnostic_session(log_file=path):
        logging.getLogger("mindcap.cli").error("bad %s %s", "secret-argument")
    output = capsys.readouterr().err + path.read_text()
    assert "could not be formatted" in output
    assert "secret-argument" not in output
    assert "Traceback" not in output
    assert "Logging error" not in output


def test_repeated_cli_invocations_restore_handlers(tmp_path: Path) -> None:
    from mindcap_cli.app import app

    namespace = logging.getLogger("mindcap")
    snapshot = (list(namespace.handlers), namespace.level, namespace.propagate)
    runner = CliRunner()
    path = tmp_path / "commands.jsonl"
    for _ in range(2):
        result = runner.invoke(app, ["--log-file", str(path), "plugins", "list"])
        assert result.exit_code == 0, result.output
        current = (list(namespace.handlers), namespace.level, namespace.propagate)
        assert current == snapshot
    assert path.exists()


def test_interrupted_and_usage_errors_have_actionable_codes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import typer

    from mindcap_cli._compat import click

    render_error(typer.Abort(), json_output=True)
    interrupted = json.loads(capsys.readouterr().err)["error"]
    assert interrupted["code"] == "interrupted"
    assert "Saved archives remain available" in interrupted["message"]
    render_error(click.exceptions.UsageError("Invalid option"), json_output=True)
    usage = json.loads(capsys.readouterr().err)["error"]
    assert usage["code"] == "invalid_usage"
    assert "--help" in usage["hint"]
