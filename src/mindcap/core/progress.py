"""Provider-agnostic progress reporting for Mindcap capture operations.

This module provides a :class:`CaptureProgressReporter` that any plugin can
use to emit phase labels, progress bars, spinners, and a final statistics
summary.  The reporter is entirely passive when not attached to a console so
plugins do not need any conditional guards.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from mindcap.core.diagnostics import redact_text

_LOGGER = logging.getLogger("mindcap.progress")
_LOGGER.addHandler(logging.NullHandler())
_UUID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.IGNORECASE)


@dataclass
class CaptureStats:
    """Accumulated statistics emitted at the end of a capture."""

    clips_discovered: int = 0
    clips_archived: int = 0
    audio_downloaded: int = 0
    audio_expected: int | None = None
    artwork_downloaded: int = 0
    videos_downloaded: int = 0
    bytes_downloaded: int = 0
    files_written: int = 0
    bytes_written: int = 0
    elapsed_seconds: float = 0.0
    verification_passed: bool = True
    capture_complete: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def throughput_mbps(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return (self.bytes_downloaded / 1_048_576) / self.elapsed_seconds

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n //= 1024
        return f"{n:.1f} TB"

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        s = int(seconds)
        m, s = divmod(s, 60)
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"


class CaptureProgressReporter:
    """Rich-backed progress reporter for capture operations.

    All public methods are safe to call when *console* is ``None``; they
    become no-ops so plugins never need conditional guards.

    Parameters
    ----------
    console:
        Rich :class:`~rich.console.Console` to write to.  Pass ``None`` for
        a fully silent reporter.
    verbose:
        Emit additional per-asset detail messages.
    debug:
        Emit low-level diagnostic messages (never exposes secrets).
    quiet:
        Suppress informational output and the final summary, retaining warnings.
    summary_console:
        Optional destination for the final result table.  Diagnostic events
        always use *console*, allowing a CLI to keep stdout machine-readable.
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        verbose: bool = False,
        debug: bool = False,
        quiet: bool = False,
        summary_console: Console | None = None,
    ) -> None:
        self._console = console
        self._summary_console = summary_console or console
        self.verbose = verbose
        self.debug = debug
        self.quiet = quiet
        self._start: float = time.monotonic()
        self._warnings: dict[str, int] = {}
        self._observed_warnings: Counter[str] = Counter()
        self._reported_groups: dict[str, int] = {}
        self._warning_count = 0

    def _emit(self, message: str, level: int = logging.INFO) -> None:
        """Write a literal message once, with a matching event for file logging."""
        if self._console is None:
            return
        safe = redact_text(message)
        _LOGGER.log(level, "%s", safe, extra={"progress_event": True})
        if self.quiet and level < logging.WARNING:
            return
        style = {
            logging.DEBUG: "dim",
            logging.INFO: "bright_cyan",
            logging.WARNING: "bright_yellow",
            logging.ERROR: "bold bright_red",
        }.get(level, "bright_cyan")
        label = logging.getLevelName(level)
        line = Text()
        line.append(datetime.now().strftime("%H:%M:%S"), style="dim")
        line.append(f"  {label:<7} ", style=style)
        line.append(safe)
        self._console.print(line, highlight=False)

    def phase(self, message: str) -> None:
        """Emit a top-level phase label."""
        self._emit(message)

    def detail(self, message: str) -> None:
        """Emit a verbose-only detail line."""
        if self.verbose:
            self._emit(message)

    def debug_line(self, message: str) -> None:
        """Emit a debug-only line with credential redaction."""
        if self.debug:
            self._emit(message, logging.DEBUG)

    def warn(self, message: str) -> None:
        """Emit warnings, collapsing repeated asset failures in normal output.

        Every occurrence remains available to an attached file logger.  The
        summary reports grouped counts, while verbose output shows each item.
        Identifiers are normalized generically, without provider assumptions.
        """
        if self._console is None:
            return
        safe = redact_text(message)
        key = _UUID.sub("<item>", safe)
        self._warnings[key] = self._warnings.get(key, 0) + 1
        self._observed_warnings[safe] += 1
        self._warning_count += 1
        if self.verbose or self._warnings[key] == 1:
            self._emit(safe, logging.WARNING)
        else:
            _LOGGER.warning("%s", safe, extra={"progress_event": True})

    def begin_capture(self) -> None:
        """Start warning reconciliation for another capture on this reporter.

        Live warnings and returned envelope warnings deduplicate within one
        capture. Aggregate groups and occurrence counts span the whole run.
        """
        self._observed_warnings.clear()

    def report_warnings(self, messages: list[str]) -> None:
        """Report an envelope's warnings without repeating provider events.

        Providers may emit warnings as they occur or only return them at the
        end.  Reconciliation preserves occurrences and makes both paths behave
        identically, including quiet and structured-output CLI invocations.
        """
        expected = Counter(redact_text(message) for message in messages)
        for message, count in expected.items():
            missing = count - self._observed_warnings[message]
            for _ in range(max(0, missing)):
                self.warn(message)
        if not self.verbose:
            for warning, count in self._warnings.items():
                if count > 1 and self._reported_groups.get(warning) != count:
                    self._emit(f"{count}x {warning}", logging.WARNING)
                    self._reported_groups[warning] = count

    def _phase_finish(
        self,
        label: str,
        started: float,
        *,
        interrupted: bool,
        attempted: int | None = None,
        total: int | None = None,
    ) -> None:
        elapsed = time.monotonic() - started
        state = "interrupted" if interrupted else "finished"
        count = ""
        if attempted is not None:
            count = f"; attempted {attempted} / {total}"
        self._emit(
            f"{label}: {state}{count}; elapsed {elapsed:.1f}s",
            logging.ERROR if interrupted else logging.INFO,
        )

    @contextmanager
    def spinner(self, label: str) -> Iterator[None]:
        """Show transient TTY activity and bounded start/end diagnostic events."""
        started = time.monotonic()
        self.phase(label)
        try:
            if self._console and not self.quiet and self._console.is_terminal:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("{task.description}", markup=False),
                    console=self._console,
                    transient=True,
                ) as prog:
                    prog.add_task(redact_text(label))
                    yield
            else:
                yield
        except BaseException:
            self._phase_finish(label, started, interrupted=True)
            raise
        else:
            self._phase_finish(label, started, interrupted=False)

    @contextmanager
    def progress_bar(self, label: str, total: int) -> Iterator[_ProgressHandle]:
        """Show attempts, including a bounded completion event outside a TTY.

        An advance records an attempted item; asset download success is tracked
        by capture statistics.  Leaving this context never implies all assets
        succeeded, and exceptions always produce an interrupted event.
        """
        started = time.monotonic()
        self.phase(f"{label} (0 / {total})")
        handle = _ProgressHandle(None, None)
        try:
            if self._console and not self.quiet and self._console.is_terminal:
                with Progress(
                    TextColumn("{task.description}", markup=False),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TaskProgressColumn(),
                    TimeRemainingColumn(),
                    TimeElapsedColumn(),
                    console=self._console,
                    transient=True,
                ) as prog:
                    task_id = prog.add_task(redact_text(label), total=total)
                    handle = _ProgressHandle(prog, task_id)
                    yield handle
            else:
                yield handle
        except BaseException:
            self._phase_finish(
                label,
                started,
                interrupted=True,
                attempted=handle.attempted,
                total=total,
            )
            raise
        else:
            self._phase_finish(
                label,
                started,
                interrupted=False,
                attempted=handle.attempted,
                total=total,
            )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(
        self,
        stats: CaptureStats,
        *,
        project_name: str | None = None,
        archive_path: str | None = None,
    ) -> None:
        """Render the final capture summary table."""
        self.report_warnings(stats.warnings)
        if self.quiet or not self._console:
            return
        stats.elapsed_seconds = time.monotonic() - self._start
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(overflow="fold")
        if project_name:
            table.add_row("Project", Text(redact_text(project_name)))
        table.add_row("Clips", str(stats.clips_archived))
        audio_label = str(stats.audio_downloaded)
        if stats.audio_expected is not None:
            audio_label = f"{stats.audio_downloaded} / {stats.audio_expected}"
        table.add_row("Audio", audio_label)
        table.add_row("Artwork", str(stats.artwork_downloaded))
        table.add_row("Videos", str(stats.videos_downloaded))
        table.add_row("Elapsed", CaptureStats._fmt_elapsed(stats.elapsed_seconds))
        if stats.bytes_downloaded:
            table.add_row("Downloaded", CaptureStats._fmt_bytes(stats.bytes_downloaded))
        if stats.throughput_mbps > 0:
            table.add_row("Throughput", f"{stats.throughput_mbps:.1f} MB/s")
        verification_label = Text(
            "PASS" if stats.verification_passed else "FAIL",
            style="bright_green" if stats.verification_passed else "bright_red",
        )
        table.add_row(
            "Capture completeness",
            Text(
                "Complete" if stats.capture_complete else "Partial",
                style="bright_green" if stats.capture_complete else "bright_yellow",
            ),
        )
        table.add_row("Archive integrity", verification_label)
        if archive_path:
            table.add_row("Archive", Text(redact_text(archive_path), overflow="fold"))
        summary_console = self._summary_console
        assert summary_console is not None
        summary_console.print()
        capture_label = (
            "Capture Complete" if stats.capture_complete else "Capture Partial"
        )
        summary_console.rule(
            Text(capture_label, style="bold"),
            style="bright_green" if stats.capture_complete else "bright_yellow",
        )
        _LOGGER.info(
            "%s; clips=%s; audio=%s; archive_integrity=%s; archive=%s",
            capture_label,
            stats.clips_archived,
            audio_label,
            "PASS" if stats.verification_passed else "FAIL",
            redact_text(archive_path or "not recorded"),
            extra={"progress_event": True},
        )
        summary_console.print(table)
        warning_count = max(self._warning_count, len(stats.warnings))
        if warning_count:
            self._emit(f"{warning_count} warning(s) recorded.", logging.WARNING)


class _ProgressHandle:
    """Thin wrapper returned by :meth:`CaptureProgressReporter.progress_bar`."""

    def __init__(self, prog: Progress | None, task_id: object) -> None:
        self._prog = prog
        self._task_id = task_id
        self.attempted = 0

    def advance(self, n: int = 1) -> None:
        self.attempted += n
        if self._prog is not None and self._task_id is not None:
            self._prog.advance(self._task_id, n)  # type: ignore[arg-type]
