"""Tests for deterministic capture planning."""

from __future__ import annotations

import json
from pathlib import Path

from mindcap.sync.models import (
    ItemAction,
    ItemStatus,
    SourceDescriptor,
)
from mindcap.sync.plan import build_plan, format_dry_run_table, plan_summary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _descriptor(
    cid: str,
    position: int | None = None,
    remote_revision: str | None = None,
) -> SourceDescriptor:
    return SourceDescriptor(
        provider="suno",
        source_type="workspace",
        canonical_identifier=cid,
        collection_position=position,
        remote_revision=remote_revision,
    )


def _write_verified_archive(
    artifact_root: Path,
    cid: str,
    remote_revision: str | None = None,
) -> None:
    """Write a minimal latest.json + bundle directory."""
    source_root = artifact_root / "workspaces" / "suno" / cid
    source_root.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "source_id": cid,
        "version": 1,
        "bundle_path": "v1",
        "canonical_content_hash": "deadbeef",
    }
    if remote_revision is not None:
        data["remote_revision"] = remote_revision
    (source_root / "latest.json").write_text(json.dumps(data), encoding="utf-8")
    (source_root / "v1").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_ordered_by_collection_position(self, tmp_path: Path) -> None:
        descriptors = [
            _descriptor("ws-3", position=3),
            _descriptor("ws-1", position=1),
            _descriptor("ws-2", position=2),
        ]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        positions = [item.descriptor.collection_position for item in items]
        assert positions == [1, 2, 3]

    def test_none_positions_sort_last(self, tmp_path: Path) -> None:
        descriptors = [
            _descriptor("ws-b", position=None),
            _descriptor("ws-a", position=1),
        ]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert items[0].descriptor.canonical_identifier == "ws-a"
        assert items[1].descriptor.canonical_identifier == "ws-b"

    def test_identifier_as_tiebreaker(self, tmp_path: Path) -> None:
        descriptors = [
            _descriptor("ws-b", position=1),
            _descriptor("ws-a", position=1),
        ]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert items[0].descriptor.canonical_identifier == "ws-a"
        assert items[1].descriptor.canonical_identifier == "ws-b"

    def test_ordering_is_stable_across_multiple_calls(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i:03d}", position=i) for i in range(20)]
        items1 = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        items2 = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        ids1 = [i.descriptor.canonical_identifier for i in items1]
        ids2 = [i.descriptor.canonical_identifier for i in items2]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# Duplicate rejection
# ---------------------------------------------------------------------------


class TestDuplicateRejection:
    def test_duplicate_identifiers_are_deduplicated(self, tmp_path: Path) -> None:
        descriptors = [
            _descriptor("ws-1", position=1),
            _descriptor("ws-1", position=2),  # Duplicate.
        ]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert len(items) == 1


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassification:
    def test_capture_new_when_no_local_archive(self, tmp_path: Path) -> None:
        items = build_plan(
            descriptors=[_descriptor("ws-new")],
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert items[0].action == ItemAction.CAPTURE_NEW

    def test_skip_when_verified_and_revision_matches(self, tmp_path: Path) -> None:
        _write_verified_archive(tmp_path, "ws-cached", remote_revision="rev-v1")
        items = build_plan(
            descriptors=[_descriptor("ws-cached", remote_revision="rev-v1")],
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,
        )
        assert items[0].action == ItemAction.SKIP_VERIFIED_UNCHANGED

    def test_capture_changed_when_revision_differs(self, tmp_path: Path) -> None:
        _write_verified_archive(tmp_path, "ws-changed", remote_revision="rev-v1")
        items = build_plan(
            descriptors=[_descriptor("ws-changed", remote_revision="rev-v2")],
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,
        )
        assert items[0].action == ItemAction.CAPTURE_CHANGED

    def test_force_overrides_cache(self, tmp_path: Path) -> None:
        _write_verified_archive(tmp_path, "ws-cached", remote_revision="rev-v1")
        items = build_plan(
            descriptors=[_descriptor("ws-cached", remote_revision="rev-v1")],
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            force=True,
        )
        assert items[0].action == ItemAction.CAPTURE_NEW

    def test_all_items_have_planned_status(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i}") for i in range(5)]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        for item in items:
            assert item.status == ItemStatus.PLANNED


# ---------------------------------------------------------------------------
# max_items cap
# ---------------------------------------------------------------------------


class TestMaxItems:
    def test_max_items_caps_plan(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i:03d}", position=i) for i in range(10)]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            max_items=3,
        )
        assert len(items) == 3

    def test_max_items_respects_ordering(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i:03d}", position=i) for i in range(10)]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            max_items=3,
        )
        # Should be the first 3 by collection_position.
        assert items[0].descriptor.collection_position == 0
        assert items[2].descriptor.collection_position == 2


# ---------------------------------------------------------------------------
# 500-item synthetic scale test
# ---------------------------------------------------------------------------


class TestSyntheticScale:
    def test_500_items_stable_ordering(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i:04d}", position=i) for i in range(500)]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        assert len(items) == 500
        # Verify stable ordering.
        positions = [item.descriptor.collection_position for item in items]
        assert positions == list(range(500))

    def test_500_items_no_duplicates(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i:04d}", position=i) for i in range(500)]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        ids = [item.descriptor.canonical_identifier for item in items]
        assert len(ids) == len(set(ids))

    def test_500_items_all_new_classification(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i:04d}", position=i) for i in range(500)]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        actions = {item.action for item in items}
        assert actions == {ItemAction.CAPTURE_NEW}

    def test_500_items_all_cached(self, tmp_path: Path) -> None:
        """500 items all with verified archives and matching revisions are skipped."""
        descriptors = [
            _descriptor(f"ws-{i:04d}", position=i, remote_revision=f"rev-{i}")
            for i in range(500)
        ]
        for i in range(500):
            _write_verified_archive(tmp_path, f"ws-{i:04d}", remote_revision=f"rev-{i}")
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
            verify_fn=lambda _: None,
        )
        assert all(item.action == ItemAction.SKIP_VERIFIED_UNCHANGED for item in items)


# ---------------------------------------------------------------------------
# Dry-run output
# ---------------------------------------------------------------------------


class TestDryRunOutput:
    def test_format_dry_run_table(self, tmp_path: Path) -> None:
        descriptors = [
            _descriptor("ws-new-1"),
            _descriptor("ws-new-2"),
        ]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        output = format_dry_run_table(len(descriptors), items)
        assert "New:" in output
        assert "2" in output

    def test_plan_summary_counts(self, tmp_path: Path) -> None:
        descriptors = [_descriptor(f"ws-{i}") for i in range(4)]
        items = build_plan(
            descriptors=descriptors,
            artifact_root=tmp_path,
            archive_subdir="workspaces/suno",
        )
        summary = plan_summary(items)
        assert summary.get("capture-new") == 4
