"""Tests for the provider-agnostic CaptureProgressReporter."""

from __future__ import annotations

import time
from io import StringIO

from rich.console import Console

from mindcap.core.progress import CaptureProgressReporter, CaptureStats


def _console() -> Console:
    """Return a non-interactive console backed by a StringIO buffer."""
    return Console(file=StringIO(), highlight=False, markup=False)


def _console_markup() -> Console:
    return Console(file=StringIO(), highlight=False, markup=True)


class TestCaptureProgressReporterNoOp:
    """A reporter with no console must never raise."""

    def test_phase_no_console(self) -> None:
        reporter = CaptureProgressReporter()
        reporter.phase("Testing phase")  # should not raise

    def test_detail_no_console(self) -> None:
        reporter = CaptureProgressReporter()
        reporter.detail("Some detail")

    def test_debug_line_no_console(self) -> None:
        reporter = CaptureProgressReporter()
        reporter.debug_line("Debug info")

    def test_warn_no_console(self) -> None:
        reporter = CaptureProgressReporter()
        reporter.warn("A warning")

    def test_spinner_no_console(self) -> None:
        reporter = CaptureProgressReporter()
        with reporter.spinner("Working..."):
            pass  # must not raise

    def test_progress_bar_no_console(self) -> None:
        reporter = CaptureProgressReporter()
        with reporter.progress_bar("Items", 10) as bar:
            bar.advance(5)

    def test_summary_no_console(self) -> None:
        reporter = CaptureProgressReporter()
        stats = CaptureStats(clips_archived=5, audio_downloaded=5)
        reporter.summary(stats)


class TestCaptureProgressReporterOutput:
    def test_phase_emits_to_console(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c)
        reporter.phase("Downloading audio...")
        assert "Downloading audio..." in buf.getvalue()

    def test_quiet_suppresses_phase(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c, quiet=True)
        reporter.phase("Should be hidden")
        assert "Should be hidden" not in buf.getvalue()

    def test_detail_hidden_when_not_verbose(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c, verbose=False)
        reporter.detail("Should be hidden")
        assert "Should be hidden" not in buf.getvalue()

    def test_detail_shown_when_verbose(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c, verbose=True)
        reporter.detail("Should appear")
        assert "Should appear" in buf.getvalue()

    def test_debug_line_hidden_when_not_debug(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c, debug=False)
        reporter.debug_line("Should be hidden")
        assert "Should be hidden" not in buf.getvalue()

    def test_debug_line_shown_when_debug(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c, debug=True)
        reporter.debug_line("Debug detail")
        assert "Debug detail" in buf.getvalue()

    def test_warn_always_shown(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c, quiet=True)
        reporter.warn("Important warning")
        assert "Important warning" in buf.getvalue()

    def test_spinner_context_manager_emits_phase_in_non_tty(self) -> None:
        """Spinner falls back to phase() in non-TTY (test) environments."""
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c)
        with reporter.spinner("Resolving project..."):
            pass
        assert "Resolving project..." in buf.getvalue()

    def test_progress_bar_yields_handle_in_non_tty(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c)
        with reporter.progress_bar("Items", 5) as bar:
            bar.advance(3)
        # In non-TTY mode the phase line is emitted
        assert "Items" in buf.getvalue()


class TestCaptureStats:
    def test_throughput_zero_elapsed(self) -> None:
        stats = CaptureStats(bytes_downloaded=1_048_576, elapsed_seconds=0.0)
        assert stats.throughput_mbps == 0.0

    def test_throughput_with_elapsed(self) -> None:
        stats = CaptureStats(bytes_downloaded=10 * 1_048_576, elapsed_seconds=10.0)
        assert stats.throughput_mbps == 1.0

    def test_fmt_bytes_bytes(self) -> None:
        assert CaptureStats._fmt_bytes(512) == "512.0 B"

    def test_fmt_bytes_mb(self) -> None:
        result = CaptureStats._fmt_bytes(2 * 1024 * 1024)
        assert "MB" in result

    def test_fmt_elapsed_seconds_only(self) -> None:
        assert CaptureStats._fmt_elapsed(45.0) == "45s"

    def test_fmt_elapsed_minutes(self) -> None:
        assert CaptureStats._fmt_elapsed(151.0) == "2m31s"

    def test_summary_records_elapsed(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c)
        # Wind back the start so elapsed is measurable
        reporter._start = time.monotonic() - 5.0
        stats = CaptureStats(clips_archived=10)
        reporter.summary(stats, project_name="my-project", archive_path="/tmp/arc")
        output = buf.getvalue()
        assert "Capture Complete" in output or "my-project" in output

    def test_summary_quiet_suppresses(self) -> None:
        buf = StringIO()
        c = Console(file=buf, highlight=False, markup=False)
        reporter = CaptureProgressReporter(console=c, quiet=True)
        stats = CaptureStats()
        reporter.summary(stats)
        assert buf.getvalue() == ""
