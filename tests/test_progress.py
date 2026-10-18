"""Tests for the provider-agnostic CaptureProgressReporter."""

from __future__ import annotations

import time
from io import StringIO

import pytest
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

    def test_partial_capture_distinguishes_integrity_from_completeness(self) -> None:
        buf = StringIO()
        reporter = CaptureProgressReporter(
            console=Console(file=buf, highlight=False, markup=True, width=100)
        )
        reporter.summary(
            CaptureStats(
                clips_archived=36,
                audio_downloaded=0,
                audio_expected=36,
                capture_complete=False,
                verification_passed=True,
            )
        )

        output = buf.getvalue()
        assert "Capture Partial" in output
        assert "Capture Complete" not in output
        assert "0 / 36" in output
        assert "Capture completeness Partial" in " ".join(output.split())
        assert "Archive integrity PASS" in " ".join(output.split())

    def test_complete_capture_summary_keeps_default_stats_compatible(self) -> None:
        buf = StringIO()
        reporter = CaptureProgressReporter(
            console=Console(file=buf, highlight=False, markup=True, width=100)
        )
        reporter.summary(CaptureStats(audio_downloaded=1))

        output = buf.getvalue()
        assert "Capture Complete" in output
        assert "Capture completeness Complete" in " ".join(output.split())
        assert "Archive integrity PASS" in " ".join(output.split())
        assert " / " not in output


def test_non_tty_progress_reports_attempted_count_and_elapsed() -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(console=Console(file=output, width=140))
    with reporter.progress_bar("Audio", 36) as bar:
        bar.advance(5)
    lines = output.getvalue().splitlines()
    assert len(lines) == 2
    assert "Audio (0 / 36)" in lines[0]
    assert "Audio: finished; attempted 5 / 36; elapsed" in lines[1]
    assert "completed" not in output.getvalue().lower()


@pytest.mark.parametrize("error", [RuntimeError("failed"), KeyboardInterrupt()])
def test_interrupted_progress_does_not_report_success(error: BaseException) -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(console=Console(file=output, width=140))
    with pytest.raises(type(error)), reporter.progress_bar("Audio", 36) as bar:
        bar.advance(2)
        raise error
    assert "interrupted; attempted 2 / 36; elapsed" in output.getvalue()
    assert "finished" not in output.getvalue()
    assert "ERROR" in output.getvalue()


def test_interrupted_spinner_does_not_report_success() -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(console=Console(file=output, width=140))
    with pytest.raises(RuntimeError), reporter.spinner("Authenticating"):
        raise RuntimeError("failed")
    assert "Authenticating: interrupted; elapsed" in output.getvalue()
    assert "finished" not in output.getvalue()


def test_repeated_asset_warnings_group_and_preserve_every_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(console=Console(file=output, width=180))
    warnings = [
        f"Asset download failed for {index:08x}-1234-5678-9012-123456789012 "
        "(audio): Failed to download asset (403)."
        for index in range(36)
    ]
    with caplog.at_level("INFO", logger="mindcap.progress"):
        for warning in warnings:
            reporter.warn(warning)
        reporter.report_warnings(warnings)
        reporter.summary(CaptureStats(capture_complete=False, warnings=warnings))
    text = output.getvalue()
    assert "36x Asset download failed for <item> (audio)" in text
    assert "36 warning(s) recorded." in text
    assert text.count("Asset download failed") == 2
    warning_records = [
        record
        for record in caplog.records
        if record.message.startswith("Asset download failed")
    ]
    assert len(warning_records) == 36
    assert all(getattr(record, "progress_event", False) for record in caplog.records)


def test_warning_groups_keep_different_statuses_and_verbose_details() -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(
        console=Console(file=output, width=160), verbose=True
    )
    reporter.warn("Asset failure 00000001-1234-5678-9012-123456789012 HTTP 403")
    reporter.warn("Asset failure 00000002-1234-5678-9012-123456789012 HTTP 403")
    reporter.warn("Asset failure 00000003-1234-5678-9012-123456789012 HTTP 429")
    reporter.summary(CaptureStats())
    assert output.getvalue().count("Asset failure") == 3
    assert "3 warning(s) recorded." in output.getvalue()
    assert "x Asset failure" not in output.getvalue()


def test_warning_reconciliation_supports_providers_returning_only_warnings() -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(
        console=Console(file=output, width=160), quiet=True
    )
    reporter.warn("Same warning")
    reporter.report_warnings(["Same warning", "Same warning", "Another warning"])
    reporter.report_warnings(["Same warning", "Same warning", "Another warning"])
    assert reporter._warning_count == 3
    assert output.getvalue().count("WARNING Same warning") == 1
    assert output.getvalue().count("2x Same warning") == 1
    assert "Another warning" in output.getvalue()


def test_warnings_with_no_console_remain_completely_silent(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    reporter = CaptureProgressReporter()
    with caplog.at_level("DEBUG", logger="mindcap.progress"):
        reporter.warn("A warning")
        reporter.phase("A phase")
        reporter.report_warnings(["A warning", "A warning"])
        reporter.summary(CaptureStats(warnings=["A warning"]))
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


def test_summary_keeps_long_archive_path_at_narrow_width() -> None:
    output = StringIO()
    archive = "/Users/example/mindcap/.cache/workspaces/suno/synthetic-workspace/v2"
    reporter = CaptureProgressReporter(console=Console(file=output, width=60))
    reporter.summary(CaptureStats(), archive_path=archive)
    assert archive in "".join(output.getvalue().split())
    assert "…" not in output.getvalue()


def test_warning_reconciliation_is_per_capture_but_groups_span_run() -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(console=Console(file=output, width=100))
    for _ in range(2):
        reporter.begin_capture()
        reporter.report_warnings(["Required attachment unavailable."])
        reporter.report_warnings(["Required attachment unavailable."])
    assert reporter._warning_count == 2
    assert "2x Required attachment unavailable." in output.getvalue()


def test_external_markup_and_credentials_are_literal_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = StringIO()
    reporter = CaptureProgressReporter(console=Console(file=output, width=160))
    malicious = (
        "[bold red]literal[/bold red] https://user:secret@cdn.example/audio?t=token"
    )
    with caplog.at_level("INFO", logger="mindcap.progress"):
        reporter.phase(malicious)
        reporter.summary(CaptureStats(), project_name=malicious, archive_path=malicious)
    assert "[bold red]literal[/bold red]" in output.getvalue()
    assert "cdn.example/audio" in output.getvalue()
    assert "user:secret" not in output.getvalue()
    assert "t=token" not in output.getvalue()
    assert all("user:secret" not in record.message for record in caplog.records)
    assert all("t=token" not in record.message for record in caplog.records)


def test_summary_can_be_routed_separately_from_progress() -> None:
    diagnostics, result = StringIO(), StringIO()
    reporter = CaptureProgressReporter(
        console=Console(file=diagnostics, width=140),
        summary_console=Console(file=result, width=140),
    )
    reporter.phase("Downloading audio")
    reporter.summary(CaptureStats(), project_name="Test project")
    assert "Downloading audio" in diagnostics.getvalue()
    assert "Capture Complete" not in diagnostics.getvalue()
    assert "Capture Complete" in result.getvalue()
    assert "Downloading audio" not in result.getvalue()
