"""Tests for safe cache evaluation."""

from __future__ import annotations

import json
from pathlib import Path

from mindcap.sync.cache import evaluate_cache
from mindcap.sync.models import SourceDescriptor


def _descriptor(
    canonical_identifier: str = "ws-abc123",
    remote_revision: str | None = None,
) -> SourceDescriptor:
    return SourceDescriptor(
        provider="suno",
        source_type="workspace",
        canonical_identifier=canonical_identifier,
        canonical_url=f"https://suno.com/create?wid={canonical_identifier}",
        remote_revision=remote_revision,
    )


def _write_latest(
    source_root: Path,
    *,
    version: int = 1,
    content_hash: str = "abc123",
    remote_revision: str | None = None,
) -> None:
    """Write a minimal latest.json pointer."""
    source_root.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "source_id": "ws-abc123",
        "version": version,
        "bundle_path": f"v{version}",
        "canonical_content_hash": content_hash,
    }
    if remote_revision is not None:
        data["remote_revision"] = remote_revision
    (source_root / "latest.json").write_text(json.dumps(data), encoding="utf-8")


def _write_bundle_dir(source_root: Path, version: int = 1) -> Path:
    """Create a minimal bundle directory."""
    bundle = source_root / f"v{version}"
    bundle.mkdir(parents=True, exist_ok=True)
    return bundle


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoLocalArchive:
    def test_returns_capture_when_no_archive(self, tmp_path: Path) -> None:
        decision = evaluate_cache(
            _descriptor(),
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert decision.decision == "capture"
        assert decision.reason == "no-local-archive"


class TestCorruptedLatestPointer:
    def test_returns_capture_when_latest_unreadable(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        source_root.mkdir(parents=True)
        (source_root / "latest.json").write_text("CORRUPT", encoding="utf-8")
        decision = evaluate_cache(
            _descriptor(),
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert decision.decision == "capture"
        assert decision.reason == "unreadable-latest-pointer"


class TestBundleDirMissing:
    def test_returns_capture_when_bundle_dir_missing(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        _write_latest(source_root)
        # Do NOT create the v1 directory.
        decision = evaluate_cache(
            _descriptor(),
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert decision.decision == "capture"
        assert decision.reason == "latest-bundle-directory-missing"


class TestNoRemoteRevision:
    def test_returns_capture_without_verification(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        _write_latest(source_root)
        _write_bundle_dir(source_root)
        descriptor = _descriptor(remote_revision=None)
        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=None,
        )
        assert decision.decision == "capture"
        assert "no-remote-revision" in decision.reason

    def test_returns_probe_with_passing_verification(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        _write_latest(source_root)
        _write_bundle_dir(source_root)
        descriptor = _descriptor(remote_revision=None)
        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,  # Always passes.
        )
        assert decision.decision == "probe"


class TestRevisionMatch:
    def test_skip_when_verified_and_revision_matches(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        _write_latest(source_root, remote_revision="rev-v2")
        _write_bundle_dir(source_root)
        descriptor = _descriptor(remote_revision="rev-v2")
        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,
        )
        assert decision.decision == "skip"
        assert decision.reason == "verified-archive-with-matching-revision"
        assert decision.verification_result == "pass"

    def test_probe_when_not_verified_but_revision_matches(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        _write_latest(source_root, remote_revision="rev-v2")
        _write_bundle_dir(source_root)
        descriptor = _descriptor(remote_revision="rev-v2")
        # No verify_fn supplied.
        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=None,
        )
        assert decision.decision == "probe"

    def test_capture_when_revision_changed(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        _write_latest(source_root, remote_revision="rev-v1")
        _write_bundle_dir(source_root)
        descriptor = _descriptor(remote_revision="rev-v2")
        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,
        )
        assert decision.decision == "capture"
        assert decision.reason == "remote-revision-changed"

    def test_capture_when_local_has_no_revision(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        # No remote_revision stored in latest.json.
        _write_latest(source_root, remote_revision=None)
        _write_bundle_dir(source_root)
        descriptor = _descriptor(remote_revision="rev-v1")
        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,
        )
        assert decision.decision == "probe"
        assert "no-recorded-revision" in decision.reason


class TestVerificationFailed:
    def test_capture_when_verification_fails(self, tmp_path: Path) -> None:
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        _write_latest(source_root, remote_revision="rev-v1")
        _write_bundle_dir(source_root)
        descriptor = _descriptor(remote_revision="rev-v1")

        def _fail(path: Path) -> None:
            raise ValueError("Checksum mismatch")

        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=_fail,
        )
        assert decision.decision == "capture"
        assert decision.verification_result == "fail"


class TestFolderExistenceAloneNeverCausesSkip:
    def test_directory_only_results_in_capture(self, tmp_path: Path) -> None:
        """An existing directory with no latest.json must never trigger a skip."""
        source_root = tmp_path / "workspaces" / "suno" / "ws-abc123"
        source_root.mkdir(parents=True)
        # Create the bundle dir but NOT latest.json.
        (source_root / "v1").mkdir()
        descriptor = _descriptor(remote_revision="rev-v1")
        decision = evaluate_cache(
            descriptor,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,
        )
        assert decision.decision == "capture"
        assert decision.reason == "no-local-archive"
