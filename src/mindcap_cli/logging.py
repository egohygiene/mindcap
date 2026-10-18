"""Invocation-scoped, credential-safe CLI diagnostics.

Human diagnostics and JSON error envelopes go to stderr. Optional diagnostic
files contain JSON Lines and never Rich markup, terminal escapes, source lines,
locals, or arbitrary LogRecord extras. Only the ``mindcap`` namespace is owned
here; HTTP libraries and the application's root logger are left alone.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from typer import Abort

from mindcap.core.diagnostics import redact_text
from mindcap.core.errors import (
    AuthenticationRequiredError,
    CaptureFailedError,
    InvalidSourceError,
    MindcapError,
    NormalizationError,
    ProfileLockedError,
    StableChromeNotFoundError,
    VerificationError,
)
from mindcap_cli._compat import click

_STYLES = {
    logging.DEBUG: "dim cyan",
    logging.INFO: "bright_cyan",
    logging.WARNING: "bold bright_yellow",
    logging.ERROR: "bold bright_red",
    logging.CRITICAL: "bold bright_red",
}


class DiagnosticLogError(MindcapError):
    """The requested local diagnostic file cannot be opened safely."""


def _console() -> Console:
    return Console(stderr=True, markup=False, highlight=False)


def _frame_locations(traceback: TracebackType | None) -> list[str]:
    locations = []
    while traceback is not None:
        code = traceback.tb_frame.f_code
        locations.append(
            redact_text(
                f"{Path(code.co_filename).name}:{traceback.tb_lineno} in {code.co_name}"
            )
        )
        traceback = traceback.tb_next
    return locations


def _message(record: logging.LogRecord) -> str:
    try:
        return redact_text(record.getMessage())
    except Exception:
        # Logging must not leak raw args through logging's default handleError.
        return "Diagnostic message could not be formatted."


class _TerminalHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.console = _console()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "progress_event", False):
            return
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        label = redact_text(record.levelname)
        text = Text(f"{timestamp}  ", style="dim")
        text.append(f"{label:<7} ", style=_STYLES.get(record.levelno, ""))
        text.append(_message(record), style="default")
        # A closed stderr should not trigger logging's unsanitized traceback.
        with suppress(OSError):
            self.console.print(text, soft_wrap=True)


class _JsonFileHandler(logging.Handler):
    def __init__(self, stream: TextIO) -> None:
        super().__init__()
        self.stream = stream
        self.failed = False

    def emit(self, record: logging.LogRecord) -> None:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": redact_text(record.levelname),
            "logger": redact_text(record.name),
            "message": _message(record),
        }
        event = getattr(record, "event", None)
        if isinstance(event, str):
            payload["event"] = redact_text(event)
        error = getattr(record, "diagnostic_error", None)
        if isinstance(error, dict):
            payload["error"] = {
                key: redact_text(value)
                for key, value in error.items()
                if key in {"code", "type", "message", "hint"} and isinstance(value, str)
            }
        frames = getattr(record, "diagnostic_frames", None)
        if isinstance(frames, list):
            payload["frames"] = [
                redact_text(frame) for frame in frames if isinstance(frame, str)
            ]
        try:
            self.stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.stream.flush()
        except (OSError, ValueError):
            if not self.failed:
                self.failed = True
                _console().print(
                    Text(
                        "WARNING Diagnostic log could not be written; "
                        "terminal diagnostics remain available.",
                        style="bold yellow",
                    )
                )

    def close(self) -> None:
        try:
            self.stream.close()
        except OSError:
            pass
        finally:
            super().close()


def _open_log_file(path: Path) -> TextIO:
    """Append to a private regular file, refusing symlinks and hard links."""
    destination = Path(path).expanduser()
    descriptor: int | None = None
    try:
        if destination.is_symlink():
            raise DiagnosticLogError(
                "Diagnostic log must be a regular file, not a symbolic link."
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(destination, flags, 0o600)
        opened = os.fstat(descriptor)
        current = destination.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            or opened.st_nlink != 1
        ):
            raise DiagnosticLogError(
                "Diagnostic log must be a regular file without links."
            )
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)
        descriptor = None
        return stream
    except DiagnosticLogError:
        raise
    except OSError as error:
        raise DiagnosticLogError(
            "Cannot open diagnostic log. Choose a writable file in an existing folder."
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def diagnostic_session(
    *,
    verbose: bool = False,
    debug: bool = False,
    quiet: bool = False,
    log_file: Path | None = None,
) -> Iterator[None]:
    """Install only Mindcap handlers and restore them after each invocation.

    Verbose enables detailed progress in the caller. Debug additionally enables
    DEBUG records. Quiet suppresses INFO progress but preserves warnings/errors.
    File diagnostics follow the same level and include reporter-owned events.
    """
    del verbose  # Progress detail belongs to the progress reporter.
    namespace = logging.getLogger("mindcap")
    previous = (
        list(namespace.handlers),
        namespace.level,
        namespace.propagate,
        namespace.disabled,
    )
    handlers: list[logging.Handler] = [_TerminalHandler()]
    try:
        if log_file is not None:
            handlers.append(_JsonFileHandler(_open_log_file(log_file)))
        namespace.handlers = handlers
        namespace.setLevel(
            logging.WARNING if quiet else logging.DEBUG if debug else logging.INFO
        )
        namespace.propagate = False
        namespace.disabled = False
        yield
    finally:
        namespace.handlers = previous[0]
        namespace.setLevel(previous[1])
        namespace.propagate = previous[2]
        namespace.disabled = previous[3]
        for handler in handlers:
            handler.close()


def _error_details(error: Exception) -> dict[str, str]:
    cases: tuple[tuple[type[Exception], str, str], ...] = (
        (
            Abort,
            "interrupted",
            "Saved archives remain available; retry when ready.",
        ),
        (
            click.ClickException,
            "invalid_usage",
            "Run this command with --help to check arguments and options.",
        ),
        (
            DiagnosticLogError,
            "diagnostic_log_unavailable",
            "Choose a writable regular file in an existing local folder.",
        ),
        (
            AuthenticationRequiredError,
            "authentication_required",
            "Run the matching mindcap auth command, then retry.",
        ),
        (
            ProfileLockedError,
            "browser_profile_locked",
            "Close the dedicated capture browser, then retry.",
        ),
        (
            StableChromeNotFoundError,
            "chrome_not_found",
            "Install Google Chrome, then run mindcap doctor for this source.",
        ),
        (
            VerificationError,
            "verification_failed",
            "Keep the archive and inspect it before retrying the capture.",
        ),
        (
            InvalidSourceError,
            "invalid_source",
            "Check the source identifier and selected plugin.",
        ),
        (
            CaptureFailedError,
            "capture_failed",
            "Check source access with mindcap doctor; use --verbose for phase details.",
        ),
        (
            NormalizationError,
            "normalization_failed",
            "Keep the original capture and check its supported input format.",
        ),
        (
            PermissionError,
            "permission_denied",
            "Check permissions or choose a writable local destination.",
        ),
        (FileNotFoundError, "path_not_found", "Check that the input path exists."),
        (
            FileExistsError,
            "path_already_exists",
            "Choose a new destination or use the command's documented resume option.",
        ),
        (ValueError, "invalid_input", "Check the command arguments and input format."),
        (OSError, "io_error", "Check local files, available space, and permissions."),
        (
            MindcapError,
            "mindcap_error",
            "Use --verbose for phase details and check the source with mindcap doctor.",
        ),
    )
    code = "unexpected_error"
    hint = "Retry with --debug for sanitized frame locations and share the diagnostics."
    for exception_type, candidate_code, candidate_hint in cases:
        if isinstance(error, exception_type):
            code, hint = candidate_code, candidate_hint
            break
    return {
        "code": code,
        "type": redact_text(type(error).__name__),
        "message": (
            "Interrupted. Saved archives remain available; "
            "no successful capture is implied."
            if isinstance(error, Abort)
            else redact_text(str(error)) or "The operation could not be completed."
        ),
        "hint": hint,
    }


def render_error(
    error: Exception, *, json_output: bool = False, debug: bool = False
) -> None:
    """Emit a safe error on stderr, with no raw traceback or private locals."""
    details = _error_details(error)
    frames = _frame_locations(error.__traceback__) if debug else []
    if any(
        isinstance(handler, (_TerminalHandler, _JsonFileHandler))
        for handler in logging.getLogger("mindcap").handlers
    ):
        logging.getLogger("mindcap.cli").error(
            "%s: %s",
            details["code"],
            details["message"],
            extra={
                "progress_event": True,
                "event": "command_failed",
                "diagnostic_error": details,
                "diagnostic_frames": frames,
            },
        )
    if json_output:
        envelope: dict[str, Any] = {"error": details}
        if frames:
            envelope["frames"] = frames
        print(json.dumps(envelope, ensure_ascii=False), file=sys.stderr)
        return
    body = Text(details["message"])
    body.append("\n\nNext step: ", style="bold")
    body.append(details["hint"])
    if frames:
        body.append(f"\n\n{details['type']}\n", style="dim")
        body.append("\n".join(frames), style="dim")
    _console().print(
        Panel(
            body,
            title=Text(f"Mindcap error · {details['code']}", style="bold bright_red"),
            border_style="bright_red",
            expand=False,
        )
    )
