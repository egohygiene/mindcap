"""Tests for the sync batch runner with synthetic collection discovery."""

from __future__ import annotations

import json
import logging
import signal
from collections.abc import Iterable
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from mindcap.core.errors import AuthenticationRequiredError, CaptureFailedError
from mindcap.core.progress import CaptureProgressReporter
from mindcap.sync.models import (
    BatchRunConfig,
    CollectionRequest,
    DiscoveryResult,
    ItemStatus,
    RunStatus,
    SourceDescriptor,
)
from mindcap.sync.runner import SyncRunner, _is_retryable_error

# ---------------------------------------------------------------------------
# Synthetic discovery strategy
# ---------------------------------------------------------------------------


class FakeDiscovery:
    """Yields a fixed list of SourceDescriptors for testing."""

    def __init__(self, descriptors: list[SourceDescriptor]) -> None:
        self._descriptors = descriptors
        self.discovery_result: DiscoveryResult | None = None

    def discover(
        self,
        request: CollectionRequest,
        reporter: Any,
    ) -> Iterable[SourceDescriptor]:
        items = list(self._descriptors)
        if request.max_items is not None:
            items = items[: request.max_items]
        self.discovery_result = DiscoveryResult(
            provider=request.provider,
            collection_identifier=request.collection_identifier,
            unique_items_discovered=len(items),
            discovery_complete=True,
            discovered_at=datetime.now(UTC),
        )
        return iter(items)


def _descriptor(cid: str, position: int = 0) -> SourceDescriptor:
    return SourceDescriptor(
        provider="suno",
        source_type="workspace",
        canonical_identifier=cid,
        canonical_url=f"https://suno.com/create?wid={cid}",
        collection_position=position,
    )


def _config(
    provider: str = "suno",
    dry_run: bool = False,
    force: bool = False,
    max_items: int | None = None,
) -> BatchRunConfig:
    return BatchRunConfig(
        provider=provider,
        collection_identifier=f"{provider}-account",
        dry_run=dry_run,
        force=force,
        max_items=max_items,
    )


# ---------------------------------------------------------------------------
# Helper: build a mock plugin that tracks captures
# ---------------------------------------------------------------------------


def _mock_plugin(capture_raises: Exception | None = None) -> Any:
    """Return a mock plugin that records captured items."""
    from mindcap.core.models import CaptureEnvelope, StoredBundle

    captured_ids: list[str] = []

    def _capture(request: Any) -> CaptureEnvelope:
        if capture_raises is not None:
            raise capture_raises
        captured_ids.append(request.canonical_identifier)
        return CaptureEnvelope(
            provider="suno",
            source_type="workspace",
            canonical_identifier=request.canonical_identifier,
            canonical_url=request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy="api",
            response_units=[],
        )

    mock_storage = MagicMock()
    mock_storage.persist.return_value = StoredBundle(
        source_id="test",
        version=1,
        path=Path("/tmp/bundle"),
        status="complete",
        canonical_content_hash="abc",
    )
    mock_storage.verify = lambda path: None

    mock_strategy = MagicMock()
    mock_strategy.capture.side_effect = _capture

    mock_plugin = MagicMock()
    mock_plugin.default_strategy.return_value = "api"
    mock_plugin.strategy.return_value = mock_strategy
    mock_plugin.normalize.return_value = {}
    mock_plugin.render.return_value = ""
    mock_plugin.storage.return_value = mock_storage
    mock_plugin._captured_ids = captured_ids
    return mock_plugin


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_capture(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i}") for i in range(5)]
        discovery = FakeDiscovery(descriptors)
        mock_plugin = _mock_plugin()

        with patch("mindcap.sync.runner.build_registry") as mock_registry:
            mock_registry.return_value.get.return_value = mock_plugin
            runner = SyncRunner(discovery, archive_subdir="workspaces/suno")
            state = runner.run(
                config=_config(dry_run=True),
                artifact_root=tmp_path,
            )

        assert state.status == RunStatus.COMPLETE
        # No captures should have been triggered.
        assert mock_plugin._captured_ids == []

    def test_dry_run_items_have_actions_assigned(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i}") for i in range(3)]
        discovery = FakeDiscovery(descriptors)

        with patch("mindcap.sync.runner.build_registry") as mock_registry:
            mock_registry.return_value.get.return_value = _mock_plugin()
            runner = SyncRunner(discovery, archive_subdir="workspaces/suno")
            state = runner.run(
                config=_config(dry_run=True),
                artifact_root=tmp_path,
            )

        assert len(state.items) == 3
        for item in state.items:
            assert item.action is not None


# ---------------------------------------------------------------------------
# Successful capture
# ---------------------------------------------------------------------------


class TestSuccessfulCapture:
    def test_all_items_captured_successfully(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i}", i) for i in range(5)]
        discovery = FakeDiscovery(descriptors)
        mock_plugin = _mock_plugin()

        with patch("mindcap.sync.runner.build_registry") as mock_registry:
            mock_registry.return_value.get.return_value = mock_plugin
            runner = SyncRunner(discovery, archive_subdir="workspaces/suno")
            state = runner.run(
                config=_config(force=True),
                artifact_root=tmp_path,
            )

        assert state.status == RunStatus.COMPLETE
        counts = state.counts()
        assert counts["failed"] == 0

    def test_run_state_persisted(self, tmp_path: Path) -> None:
        descriptors = [_descriptor("ws-001")]
        discovery = FakeDiscovery(descriptors)

        with patch("mindcap.sync.runner.build_registry") as mock_registry:
            mock_registry.return_value.get.return_value = _mock_plugin()
            runner = SyncRunner(discovery, archive_subdir="workspaces/suno")
            state = runner.run(config=_config(force=True), artifact_root=tmp_path)

        run_dir = tmp_path / "runs" / "suno" / state.run_id
        assert run_dir.is_dir()
        assert (run_dir / "run.json").is_file()


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_single_item_failure_does_not_abort_other_items(
        self, tmp_path: Path
    ) -> None:
        """A fatal error on one item must not prevent other items from completing."""
        from mindcap.core.errors import CaptureFailedError
        from mindcap.core.models import CaptureEnvelope, StoredBundle

        call_count = [0]

        def _capture(request: Any) -> CaptureEnvelope:
            call_count[0] += 1
            if request.canonical_identifier == "ws-001":
                raise CaptureFailedError("Source not found — terminal error")
            return CaptureEnvelope(
                provider="suno",
                source_type="workspace",
                canonical_identifier=request.canonical_identifier,
                canonical_url=request.canonical_url,
                captured_at=datetime.now(UTC),
                strategy="api",
                response_units=[],
            )

        mock_strategy = MagicMock()
        mock_strategy.capture.side_effect = _capture
        mock_storage = MagicMock()
        mock_storage.persist.return_value = StoredBundle(
            source_id="test",
            version=1,
            path=Path("/tmp/bundle"),
            status="complete",
            canonical_content_hash="abc",
        )
        mock_plugin = MagicMock()
        mock_plugin.default_strategy.return_value = "api"
        mock_plugin.strategy.return_value = mock_strategy
        mock_plugin.normalize.return_value = {}
        mock_plugin.render.return_value = ""
        mock_plugin.storage.return_value = mock_storage

        # Three items: ws-001 fails, ws-002 and ws-003 succeed.
        descriptors = [
            _descriptor("ws-001", 1),
            _descriptor("ws-002", 2),
            _descriptor("ws-003", 3),
        ]
        discovery = FakeDiscovery(descriptors)

        with patch("mindcap.sync.runner.build_registry") as mock_registry:
            mock_registry.return_value.get.return_value = mock_plugin
            runner = SyncRunner(discovery, archive_subdir="workspaces/suno")
            state = runner.run(
                config=_config(force=True),
                artifact_root=tmp_path,
            )

        assert state.status == RunStatus.COMPLETE_WITH_FAILURES
        counts = state.counts()
        assert counts["failed"] >= 1
        # All three items must have been attempted.
        assert call_count[0] >= 3

    def test_partial_failure_returns_exit_code_2(self, tmp_path: Path) -> None:
        from mindcap.core.errors import CaptureFailedError
        from mindcap.core.models import CaptureEnvelope, StoredBundle
        from mindcap.sync.models import EXIT_PARTIAL_FAILURE, run_exit_code

        def _capture(request: Any) -> CaptureEnvelope:
            if request.canonical_identifier == "ws-fail":
                raise CaptureFailedError("terminal")
            return CaptureEnvelope(
                provider="suno",
                source_type="workspace",
                canonical_identifier=request.canonical_identifier,
                canonical_url=request.canonical_url,
                captured_at=datetime.now(UTC),
                strategy="api",
                response_units=[],
            )

        mock_strategy = MagicMock()
        mock_strategy.capture.side_effect = _capture
        mock_storage = MagicMock()
        mock_storage.persist.return_value = StoredBundle(
            source_id="t",
            version=1,
            path=Path("/tmp/b"),
            status="complete",
            canonical_content_hash="x",
        )
        mock_plugin = MagicMock()
        mock_plugin.default_strategy.return_value = "api"
        mock_plugin.strategy.return_value = mock_strategy
        mock_plugin.normalize.return_value = {}
        mock_plugin.render.return_value = ""
        mock_plugin.storage.return_value = mock_storage

        discovery = FakeDiscovery(
            [
                _descriptor("ws-ok"),
                _descriptor("ws-fail"),
            ]
        )
        with patch("mindcap.sync.runner.build_registry") as mock_registry:
            mock_registry.return_value.get.return_value = mock_plugin
            runner = SyncRunner(discovery, archive_subdir="workspaces/suno")
            state = runner.run(config=_config(force=True), artifact_root=tmp_path)

        assert run_exit_code(state) == EXIT_PARTIAL_FAILURE


class TestSafeDiagnostics:
    def test_transient_failures_report_backoff_and_preserve_attempt_history(
        self, tmp_path: Path
    ) -> None:
        plugin = _mock_plugin()
        capture = plugin.strategy.return_value.capture.side_effect
        calls = 0
        observations: list[str] = []
        reporter = MagicMock(spec=CaptureProgressReporter)
        reporter.warn.side_effect = lambda message: observations.append(message)

        def fail_twice(request: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise CaptureFailedError(
                    "request timed out for https://media.example/audio?token=hidden"
                )
            return capture(request)

        plugin.strategy.return_value.capture.side_effect = fail_twice

        def record_sleep(seconds: int) -> None:
            observations.append(f"sleep {seconds}")

        with (
            patch("mindcap.sync.runner.build_registry") as registry,
            patch("mindcap.sync.runner.time.sleep", side_effect=record_sleep) as sleep,
        ):
            registry.return_value.get.return_value = plugin
            state = SyncRunner(
                FakeDiscovery([_descriptor("ws-retry")]),
                "workspaces/suno",
                reporter=reporter,
            ).run(config=_config(force=True), artifact_root=tmp_path)

        assert state.status == RunStatus.COMPLETE
        assert calls == 3
        assert [call.args[0] for call in sleep.call_args_list] == [2, 4]
        assert "attempt 1 failed" in observations[0]
        assert "Retrying in 2s (retry 1 of 3)" in observations[0]
        assert observations[1] == "sleep 2"
        assert "attempt 2 failed" in observations[2]
        assert observations[3] == "sleep 4"
        assert any(
            "attempt 3 of 4" in call.args[0] for call in reporter.detail.call_args_list
        )
        attempts = state.items[0].attempts
        assert [attempt.status for attempt in attempts] == [
            ItemStatus.FAILED_RETRYABLE,
            ItemStatus.FAILED_RETRYABLE,
            ItemStatus.COMPLETE,
        ]
        assert all(attempt.error for attempt in attempts[:2])
        run_dir = tmp_path / "runs" / "suno" / state.run_id
        saved = (run_dir / "run.json").read_text()
        events = (run_dir / "items.jsonl").read_text()
        assert "hidden" not in saved + events + " ".join(observations)
        assert "media.example/audio" in saved
        assert "[REDACTED]" in events

    @pytest.mark.parametrize(
        "error_type, message, expected_attempts",
        [
            (CaptureFailedError, "permission denied", 1),
            (AuthenticationRequiredError, "session expired", 1),
            (CaptureFailedError, "request timed out", 4),
        ],
    )
    def test_final_failures_are_visible_and_redacted(
        self,
        tmp_path: Path,
        error_type: type[Exception],
        message: str,
        expected_attempts: int,
    ) -> None:
        output = StringIO()
        reporter = CaptureProgressReporter(
            Console(file=output, width=160, color_system=None), verbose=True
        )
        error = error_type(
            f"{message}: https://media.example/audio?signature=signed-secret\n"
            "Authorization: Bearer bearer-secret\n"
            "Cookie: session=cookie-secret"
        )
        plugin = _mock_plugin(capture_raises=error)
        with (
            patch("mindcap.sync.runner.build_registry") as registry,
            patch("mindcap.sync.runner.time.sleep"),
        ):
            registry.return_value.get.return_value = plugin
            state = SyncRunner(
                FakeDiscovery([_descriptor("ws-failed")]),
                "workspaces/suno",
                reporter=reporter,
            ).run(config=_config(force=True), artifact_root=tmp_path)

        assert state.status == RunStatus.COMPLETE_WITH_FAILURES
        item = state.items[0]
        assert len(item.attempts) == expected_attempts
        assert item.status == ItemStatus.FAILED_TERMINAL
        assert all(attempt.error for attempt in item.attempts)
        assert "ws-failed" in output.getvalue()
        assert "failed" in output.getvalue()
        if error_type is AuthenticationRequiredError:
            assert "Authenticate again" in " ".join(output.getvalue().split())
        else:
            assert f"after {expected_attempts} attempt(s)" in output.getvalue()
        run_dir = tmp_path / "runs" / "suno" / state.run_id
        diagnostics = output.getvalue() + "".join(
            path.read_text()
            for path in run_dir.iterdir()
            if path.suffix in {".json", ".jsonl", ".md"}
        )
        for secret in ("signed-secret", "bearer-secret", "cookie-secret"):
            assert secret not in diagnostics
        assert "[REDACTED]" in diagnostics
        events = [
            json.loads(line)
            for line in (run_dir / "items.jsonl").read_text().splitlines()
        ]
        assert events[-1]["event"] == "item-failed"
        assert events[-1]["terminal"] is True

    def test_run_failure_writes_safe_final_report_and_displays_reason(
        self, tmp_path: Path
    ) -> None:
        discovery = FakeDiscovery([])
        discovery.discover = MagicMock(  # type: ignore[method-assign]
            side_effect=CaptureFailedError(
                "discovery failed: https://api.example/items?token=query-secret\n"
                "Authorization: Bearer header-secret"
            )
        )
        output = StringIO()
        reporter = CaptureProgressReporter(
            Console(file=output, width=160, color_system=None)
        )
        state = SyncRunner(discovery, "workspaces/suno", reporter=reporter).run(
            config=_config(), artifact_root=tmp_path
        )

        assert state.status == RunStatus.FAILED
        assert state.completed_at is not None
        assert "Sync failed" in output.getvalue()
        run_dir = tmp_path / "runs" / "suno" / state.run_id
        report = json.loads((run_dir / "report.json").read_text())
        assert report["status"] == "failed"
        assert report["completed_at"] is not None
        assert report["errors"] == state.errors
        diagnostics = output.getvalue() + "".join(
            path.read_text()
            for path in run_dir.iterdir()
            if path.suffix in {".json", ".jsonl", ".md"}
        )
        assert "query-secret" not in diagnostics
        assert "header-secret" not in diagnostics
        assert "api.example/items" in diagnostics

    @pytest.mark.parametrize("capture_complete", [None, True, 0, "false"])
    def test_advisory_envelope_warnings_are_visible_and_preserved(
        self, tmp_path: Path, capture_complete: object
    ) -> None:
        from mindcap.core.models import CaptureEnvelope

        plugin = _mock_plugin()

        def capture_with_warning(request: Any) -> CaptureEnvelope:
            return CaptureEnvelope(
                provider=request.provider,
                source_type=request.source_type,
                canonical_identifier=request.canonical_identifier,
                canonical_url=request.canonical_url,
                captured_at=datetime.now(UTC),
                strategy="api",
                response_units=[],
                warnings=[
                    "Audio download failed (403): "
                    "https://media.example/audio?signature=warning-secret"
                ],
                safe_metadata={"capture_complete": capture_complete},
            )

        plugin.strategy.return_value.capture.side_effect = capture_with_warning
        output = StringIO()
        reporter = CaptureProgressReporter(
            Console(file=output, width=160, color_system=None)
        )
        with patch("mindcap.sync.runner.build_registry") as registry:
            registry.return_value.get.return_value = plugin
            state = SyncRunner(
                FakeDiscovery([_descriptor("ws-advisory")]),
                "workspaces/suno",
                reporter=reporter,
            ).run(config=_config(force=True), artifact_root=tmp_path)

        item = state.items[0]
        assert state.status == RunStatus.COMPLETE
        assert item.status == ItemStatus.COMPLETE_WITH_WARNINGS
        assert item.attempts[0].status == ItemStatus.COMPLETE
        assert len(item.warnings) == 1
        assert item.attempts[0].warnings == item.warnings
        assert "Audio download failed (403)" in item.warnings[0]
        assert "warning-secret" not in item.warnings[0]
        assert "Audio download failed (403)" in output.getvalue()
        assert "warning-secret" not in output.getvalue()
        run_dir = tmp_path / "runs" / "suno" / state.run_id
        saved_item = json.loads((run_dir / "run.json").read_text())["items"][0]
        assert saved_item["warnings"] == item.warnings
        assert saved_item["attempts"][0]["warnings"] == item.warnings

    def test_matching_envelope_warnings_from_two_sources_count_both_occurrences(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from mindcap.core.models import CaptureEnvelope

        warning = "Optional cover artwork was unavailable."
        plugin = _mock_plugin()
        capture = plugin.strategy.return_value.capture.side_effect

        def capture_with_advisory(request: Any) -> CaptureEnvelope:
            envelope = capture(request)
            assert isinstance(envelope, CaptureEnvelope)
            envelope.warnings.append(warning)
            return envelope

        plugin.strategy.return_value.capture.side_effect = capture_with_advisory
        output = StringIO()
        reporter = CaptureProgressReporter(
            Console(file=output, width=160, color_system=None)
        )
        with (
            caplog.at_level(logging.WARNING, logger="mindcap.progress"),
            patch("mindcap.sync.runner.build_registry") as registry,
        ):
            registry.return_value.get.return_value = plugin
            state = SyncRunner(
                FakeDiscovery([_descriptor("ws-first"), _descriptor("ws-second")]),
                "workspaces/suno",
                reporter=reporter,
            ).run(config=_config(force=True), artifact_root=tmp_path)

        assert state.status == RunStatus.COMPLETE
        assert all(item.warnings == [warning] for item in state.items)
        assert reporter._warning_count == 2
        assert f"2x {warning}" in output.getvalue()
        warning_events = [
            record
            for record in caplog.records
            if record.name == "mindcap.progress" and record.getMessage() == warning
        ]
        assert len(warning_events) == 2

    @pytest.mark.parametrize(
        "stored_status, with_console", [("complete", True), ("unchanged", False)]
    )
    def test_explicitly_partial_archive_fails_without_retry_and_retains_archive(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        stored_status: str,
        with_console: bool,
    ) -> None:
        from mindcap.core.models import CaptureEnvelope
        from mindcap.sync.models import EXIT_PARTIAL_FAILURE, run_exit_code

        plugin = _mock_plugin()
        capture = plugin.strategy.return_value.capture.side_effect

        def capture_partial(request: Any) -> CaptureEnvelope:
            envelope = capture(request)
            assert isinstance(envelope, CaptureEnvelope)
            envelope.safe_metadata["capture_complete"] = False
            envelope.warnings.append("Required audio download failed (403).")
            return envelope

        plugin.strategy.return_value.capture.side_effect = capture_partial
        plugin.storage.return_value.persist.return_value.status = stored_status
        output = StringIO()
        reporter = CaptureProgressReporter(
            Console(file=output, width=160, color_system=None) if with_console else None
        )
        with (
            patch("mindcap.sync.runner.build_registry") as registry,
            patch("mindcap.sync.runner.time.sleep") as sleep,
        ):
            registry.return_value.get.return_value = plugin
            state = SyncRunner(
                FakeDiscovery([_descriptor("ws-partial")]),
                "workspaces/suno",
                reporter=reporter,
            ).run(config=_config(force=True), artifact_root=tmp_path)

        assert state.status == RunStatus.COMPLETE_WITH_FAILURES
        assert run_exit_code(state) == EXIT_PARTIAL_FAILURE
        item = state.items[0]
        assert item.status == ItemStatus.FAILED_TERMINAL
        assert item.archive_path == "/tmp/bundle"
        assert item.archive_version == 1
        assert len(item.attempts) == 1
        attempt = item.attempts[0]
        assert attempt.status == ItemStatus.FAILED_TERMINAL
        assert attempt.archive_path == item.archive_path
        assert (
            attempt.warnings
            == item.warnings
            == ["Required audio download failed (403)."]
        )
        assert attempt.error == item.errors[0]
        assert "Capture is partial" in item.errors[0]
        assert "saved archive retained" in item.errors[0]
        assert plugin.strategy.return_value.capture.call_count == 1
        sleep.assert_not_called()
        run_dir = tmp_path / "runs" / "suno" / state.run_id
        report = json.loads((run_dir / "report.json").read_text())
        assert report["status"] == "complete-with-failures"
        assert report["counts"]["failed"] == 1
        assert report["counts"]["completed"] == 0
        saved_item = json.loads((run_dir / "run.json").read_text())["items"][0]
        assert saved_item["status"] == "failed-terminal"
        assert saved_item["attempts"][0]["status"] == "failed-terminal"
        events = [
            json.loads(line)
            for line in (run_dir / "items.jsonl").read_text().splitlines()
        ]
        assert events[-1]["event"] == "item-failed"
        assert events[-1]["archive_path"] == item.archive_path
        assert events[-1]["error"] == attempt.error
        if with_console:
            assert "Required audio download failed (403)" in output.getvalue()
            assert "Capture is partial" in output.getvalue()
        else:
            assert output.getvalue() == ""
        assert capsys.readouterr() == ("", "")


class TestSignalLifecycle:
    @pytest.mark.parametrize("item_count", [1, 2])
    def test_signal_during_capture_preserves_interrupted_run_status(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        item_count: int,
    ) -> None:
        from mindcap.core.models import CaptureEnvelope
        from mindcap.sync.models import EXIT_INTERRUPTED, run_exit_code

        previous: dict[int, Any] = {
            signal.SIGINT: object(),
            signal.SIGTERM: object(),
        }
        active = previous.copy()
        monkeypatch.setattr(signal, "getsignal", lambda signum: active[signum])
        monkeypatch.setattr(
            signal,
            "signal",
            lambda signum, handler: active.__setitem__(signum, handler),
        )
        plugin = _mock_plugin()
        capture = plugin.strategy.return_value.capture.side_effect

        def interrupt_during_capture(request: Any) -> CaptureEnvelope:
            active[signal.SIGINT](signal.SIGINT, None)
            envelope = capture(request)
            assert isinstance(envelope, CaptureEnvelope)
            return envelope

        plugin.strategy.return_value.capture.side_effect = interrupt_during_capture
        with patch("mindcap.sync.runner.build_registry") as registry:
            registry.return_value.get.return_value = plugin
            state = SyncRunner(
                FakeDiscovery([_descriptor(f"ws-{i}") for i in range(item_count)]),
                "workspaces/suno",
            ).run(config=_config(force=True), artifact_root=tmp_path)

        assert state.status == RunStatus.INTERRUPTED
        assert state.interruption_reason == f"signal-{signal.SIGINT}"
        assert run_exit_code(state) == EXIT_INTERRUPTED
        assert plugin.strategy.return_value.capture.call_count == 1
        assert state.items[0].status == ItemStatus.COMPLETE
        if item_count > 1:
            assert state.items[1].status == ItemStatus.PLANNED
            assert state.items[1].attempts == []
        run_dir = tmp_path / "runs" / "suno" / state.run_id
        report = json.loads((run_dir / "report.json").read_text())
        assert report["status"] == "interrupted"
        assert json.loads((run_dir / "run.json").read_text())["status"] == "interrupted"
        assert active == previous

    @pytest.mark.parametrize("fail_discovery", [False, True])
    def test_previous_signal_handlers_are_restored(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fail_discovery: bool,
    ) -> None:
        previous = {signal.SIGINT: object(), signal.SIGTERM: object()}
        active = previous.copy()
        monkeypatch.setattr(signal, "getsignal", lambda signum: active[signum])
        monkeypatch.setattr(
            signal,
            "signal",
            lambda signum, handler: active.__setitem__(signum, handler),
        )
        discovery = FakeDiscovery([])
        if fail_discovery:
            discovery.discover = MagicMock(  # type: ignore[method-assign]
                side_effect=CaptureFailedError("discovery rejected")
            )
        with patch("mindcap.sync.runner.build_registry") as registry:
            registry.return_value.get.return_value = _mock_plugin()
            state = SyncRunner(discovery, "workspaces/suno").run(
                config=_config(), artifact_root=tmp_path
            )

        assert state.status == (
            RunStatus.FAILED if fail_discovery else RunStatus.COMPLETE
        )
        assert active == previous

    def test_runner_can_be_reused_after_interruption(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        previous: dict[int, Any] = {
            signal.SIGINT: object(),
            signal.SIGTERM: object(),
        }
        active = previous.copy()
        monkeypatch.setattr(signal, "getsignal", lambda signum: active[signum])
        monkeypatch.setattr(
            signal,
            "signal",
            lambda signum, handler: active.__setitem__(signum, handler),
        )
        discovery = FakeDiscovery([_descriptor("ws-001")])
        discover = discovery.discover
        interrupted_once = False

        def interrupt_first_discovery(
            request: CollectionRequest, reporter: Any
        ) -> Iterable[SourceDescriptor]:
            nonlocal interrupted_once
            descriptors = discover(request, reporter)
            if not interrupted_once:
                interrupted_once = True
                active[signal.SIGINT](signal.SIGINT, None)
            return descriptors

        monkeypatch.setattr(discovery, "discover", interrupt_first_discovery)
        runner = SyncRunner(discovery, "workspaces/suno")
        with patch("mindcap.sync.runner.build_registry") as registry:
            registry.return_value.get.return_value = _mock_plugin()
            interrupted = runner.run(
                config=_config(force=True), artifact_root=tmp_path, run_id="first"
            )
            assert interrupted.status == RunStatus.INTERRUPTED
            assert active == previous
            completed = runner.run(
                config=_config(force=True), artifact_root=tmp_path, run_id="second"
            )

        assert completed.status == RunStatus.COMPLETE
        assert completed.counts()["complete"] == 1
        assert active == previous


# ---------------------------------------------------------------------------
# Retryable error classification
# ---------------------------------------------------------------------------


class TestRetryableClassification:
    @pytest.mark.parametrize(
        "message",
        [
            "request timed out",
            "connection refused",
            "temporary unavailability",
            "HTTP 429 Too Many Requests",
            "HTTP 503 Service Unavailable",
            "network error",
        ],
    )
    def test_retryable_errors(self, message: str) -> None:
        assert _is_retryable_error(Exception(message))

    @pytest.mark.parametrize(
        "message",
        [
            "invalid identifier",
            "permission denied",
            "source has been deleted",
            "malformed request",
        ],
    )
    def test_non_retryable_errors(self, message: str) -> None:
        assert not _is_retryable_error(Exception(message))


# ---------------------------------------------------------------------------
# Zero items
# ---------------------------------------------------------------------------


class TestZeroItems:
    def test_empty_discovery_completes_successfully(self, tmp_path: Path) -> None:
        discovery = FakeDiscovery([])

        with patch("mindcap.sync.runner.build_registry") as mock_registry:
            mock_registry.return_value.get.return_value = _mock_plugin()
            runner = SyncRunner(discovery, archive_subdir="workspaces/suno")
            state = runner.run(config=_config(), artifact_root=tmp_path)

        assert state.status == RunStatus.COMPLETE
        assert len(state.items) == 0
