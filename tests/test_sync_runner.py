"""Tests for the sync batch runner with synthetic collection discovery."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mindcap.sync.models import (
    BatchRunConfig,
    CollectionRequest,
    DiscoveryResult,
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
