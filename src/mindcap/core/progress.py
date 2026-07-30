"""Provider-agnostic progress reporting for Mindcap capture operations.

This module provides a :class:`CaptureProgressReporter` that any plugin can
use to emit phase labels, progress bars, spinners, and a final statistics
summary.  The reporter is entirely passive when not attached to a console so
plugins do not need any conditional guards.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

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


@dataclass
class CaptureStats:
    """Accumulated statistics emitted at the end of a capture."""

    clips_discovered: int = 0
    clips_archived: int = 0
    audio_downloaded: int = 0
    artwork_downloaded: int = 0
    videos_downloaded: int = 0
    bytes_downloaded: int = 0
    files_written: int = 0
    bytes_written: int = 0
    elapsed_seconds: float = 0.0
    verification_passed: bool = True
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
        Suppress all non-error output, including the final summary.
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        verbose: bool = False,
        debug: bool = False,
        quiet: bool = False,
    ) -> None:
        self._console = console
        self.verbose = verbose
        self.debug = debug
        self.quiet = quiet
        self._start: float = time.monotonic()

    # ------------------------------------------------------------------
    # Phase / label helpers
    # ------------------------------------------------------------------

    def phase(self, message: str) -> None:
        """Emit a top-level phase label."""
        if self._console and not self.quiet:
            self._console.print(message)

    def detail(self, message: str) -> None:
        """Emit a verbose-only detail line."""
        if self._console and self.verbose and not self.quiet:
            self._console.print(f"  [dim]{message}[/dim]")

    def debug_line(self, message: str) -> None:
        """Emit a debug-only line (must never contain secrets)."""
        if self._console and self.debug and not self.quiet:
            self._console.print(f"  [dim yellow]{message}[/dim yellow]")

    def warn(self, message: str) -> None:
        """Always emit a warning, even in quiet mode."""
        if self._console:
            self._console.print(f"[bold yellow]Warning:[/bold yellow] {message}")

    # ------------------------------------------------------------------
    # Spinner context manager
    # ------------------------------------------------------------------

    @contextmanager
    def spinner(self, label: str) -> Iterator[None]:
        """Display a spinner while the block executes.

        Falls back to a plain phase line when the console is not available or
        is not a TTY (e.g., captured test output).
        """
        if self._console and not self.quiet and self._console.is_terminal:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self._console,
                transient=True,
            ) as prog:
                prog.add_task(label)
                yield
        else:
            self.phase(label)
            yield

    # ------------------------------------------------------------------
    # Progress-bar context manager
    # ------------------------------------------------------------------

    @contextmanager
    def progress_bar(self, label: str, total: int) -> Iterator[_ProgressHandle]:
        """Display a Rich progress bar for *total* items.

        Yields a :class:`_ProgressHandle` with an ``advance()`` method.
        """
        if self._console and not self.quiet and self._console.is_terminal:
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                TimeElapsedColumn(),
                console=self._console,
                transient=False,
            ) as prog:
                task_id = prog.add_task(label, total=total)
                handle = _ProgressHandle(prog, task_id)
                yield handle
        else:
            self.phase(f"{label} (0 / {total})")
            handle = _ProgressHandle(None, None)
            yield handle

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
        if self.quiet or not self._console:
            return
        stats.elapsed_seconds = time.monotonic() - self._start
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()
        if project_name:
            table.add_row("Project", project_name)
        table.add_row("Clips", str(stats.clips_archived))
        table.add_row("Audio", str(stats.audio_downloaded))
        table.add_row("Artwork", str(stats.artwork_downloaded))
        table.add_row("Videos", str(stats.videos_downloaded))
        table.add_row("Elapsed", CaptureStats._fmt_elapsed(stats.elapsed_seconds))
        if stats.bytes_downloaded:
            table.add_row("Downloaded", CaptureStats._fmt_bytes(stats.bytes_downloaded))
        if stats.throughput_mbps > 0:
            table.add_row("Throughput", f"{stats.throughput_mbps:.1f} MB/s")
        verification_label = (
            "[green]PASS[/green]" if stats.verification_passed else "[red]FAIL[/red]"
        )
        table.add_row("Verification", verification_label)
        if archive_path:
            table.add_row("Archive", archive_path)
        self._console.print()
        self._console.rule("[bold]Capture Complete[/bold]")
        self._console.print(table)
        if stats.warnings:
            self._console.print(
                f"[yellow]{len(stats.warnings)} warning(s) recorded.[/yellow]"
            )


class _ProgressHandle:
    """Thin wrapper returned by :meth:`CaptureProgressReporter.progress_bar`."""

    def __init__(self, prog: Progress | None, task_id: object) -> None:
        self._prog = prog
        self._task_id = task_id

    def advance(self, n: int = 1) -> None:
        if self._prog is not None and self._task_id is not None:
            self._prog.advance(self._task_id, n)  # type: ignore[arg-type]
