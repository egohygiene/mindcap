"""Tests for run storage, run ID generation, and state persistence."""

from __future__ import annotations

import json
from pathlib import Path

from mindcap.sync.models import (
    BatchRunConfig,
    CaptureItemRecord,
    ItemStatus,
    RunStatus,
    SourceDescriptor,
)
from mindcap.sync.run_storage import (
    RunStorage,
    create_initial_state,
    generate_run_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(provider: str = "suno") -> BatchRunConfig:
    return BatchRunConfig(
        provider=provider,
        collection_identifier=f"{provider}-account",
    )


def _descriptor(cid: str) -> SourceDescriptor:
    return SourceDescriptor(
        provider="suno",
        source_type="workspace",
        canonical_identifier=cid,
    )


# ---------------------------------------------------------------------------
# Run ID generation
# ---------------------------------------------------------------------------


class TestGenerateRunId:
    def test_format(self) -> None:
        run_id = generate_run_id("suno")
        assert run_id.startswith("suno-")
        parts = run_id.split("-")
        assert len(parts) >= 3
        # provider-YYYYMMDDTHHMMSSZ-suffix (16 chars: 4+2+2+T+2+2+2+Z)
        assert len(parts[1]) == 16  # timestamp: 20260730T164500Z

    def test_unique_ids(self) -> None:
        ids = {generate_run_id("suno") for _ in range(20)}
        # Very unlikely to collide; 6-char random suffix.
        assert len(ids) >= 15

    def test_different_providers(self) -> None:
        suno_id = generate_run_id("suno")
        dk_id = generate_run_id("distrokid")
        assert suno_id.startswith("suno-")
        assert dk_id.startswith("distrokid-")


# ---------------------------------------------------------------------------
# create_initial_state
# ---------------------------------------------------------------------------


class TestCreateInitialState:
    def test_creates_state_with_correct_fields(self, tmp_path: Path) -> None:
        config = _config()
        run_id = generate_run_id("suno")
        state = create_initial_state(run_id, config, tmp_path)
        assert state.run_id == run_id
        assert state.provider == "suno"
        assert state.status == RunStatus.PLANNING
        assert state.config_fingerprint == config.fingerprint()
        assert state.artifact_root == str(tmp_path)


# ---------------------------------------------------------------------------
# RunStorage: directory management
# ---------------------------------------------------------------------------


class TestRunStorageDirectory:
    def test_ensure_dir_creates_directory(self, tmp_path: Path) -> None:
        storage = RunStorage(tmp_path, "suno", "test-run-001")
        run_dir = storage.ensure_dir()
        assert run_dir.is_dir()

    def test_run_dir_path_correct(self, tmp_path: Path) -> None:
        storage = RunStorage(tmp_path, "suno", "suno-test-123")
        assert storage.run_dir == tmp_path / "runs" / "suno" / "suno-test-123"


# ---------------------------------------------------------------------------
# RunStorage: state save/load round trip
# ---------------------------------------------------------------------------


class TestRunStoragePersistence:
    def test_save_and_load_state_round_trip(self, tmp_path: Path) -> None:
        config = _config()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        state = create_initial_state(run_id, config, tmp_path)
        state.status = RunStatus.RUNNING
        storage.save_state(state)

        loaded = storage.load_state()
        assert loaded is not None
        assert loaded.run_id == run_id
        assert loaded.status == RunStatus.RUNNING

    def test_load_state_returns_none_when_missing(self, tmp_path: Path) -> None:
        storage = RunStorage(tmp_path, "suno", "nonexistent-run")
        assert storage.load_state() is None

    def test_checkpoint_written_on_save_state(self, tmp_path: Path) -> None:
        from mindcap.sync.checkpoint import checkpoint_exists

        config = _config()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        state = create_initial_state(run_id, config, tmp_path)
        storage.save_state(state)
        assert checkpoint_exists(storage.run_dir)

    def test_save_plan_creates_plan_json(self, tmp_path: Path) -> None:
        _config()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        items = [
            CaptureItemRecord(
                descriptor=_descriptor("ws-001"),
                status=ItemStatus.PLANNED,
            )
        ]
        storage.save_plan(items)
        plan_path = storage.run_dir / "plan.json"
        assert plan_path.is_file()
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        assert plan_data["run_id"] == run_id
        assert len(plan_data["items"]) == 1

    def test_append_item_event_creates_jsonl(self, tmp_path: Path) -> None:
        _config()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        storage.append_item_event({"event": "item-started", "id": "ws-001"})
        log_path = storage.run_dir / "items.jsonl"
        assert log_path.is_file()
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "item-started"
        assert "ts" in entry

    def test_save_report_creates_report_files(self, tmp_path: Path) -> None:
        config = _config()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        state = create_initial_state(run_id, config, tmp_path)
        state.status = RunStatus.COMPLETE
        storage.save_report(state)
        assert (storage.run_dir / "report.json").is_file()
        assert (storage.run_dir / "report.md").is_file()


# ---------------------------------------------------------------------------
# RunStorage: run discovery
# ---------------------------------------------------------------------------


class TestRunDiscovery:
    def test_list_runs_returns_all_runs(self, tmp_path: Path) -> None:
        for i in range(3):
            run_id = f"suno-20260730T00000{i}Z-abc"
            storage = RunStorage(tmp_path, "suno", run_id)
            state = create_initial_state(run_id, _config(), tmp_path)
            storage.save_state(state)

        runs = RunStorage.list_runs(tmp_path, "suno")
        assert len(runs) == 3

    def test_list_runs_empty_when_no_runs(self, tmp_path: Path) -> None:
        runs = RunStorage.list_runs(tmp_path, "suno")
        assert runs == []

    def test_find_resumable_returns_interrupted_runs(self, tmp_path: Path) -> None:
        config = _config()
        fp = config.fingerprint()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        state = create_initial_state(run_id, config, tmp_path)
        state.status = RunStatus.INTERRUPTED
        storage.save_state(state)

        candidates = RunStorage.find_resumable(tmp_path, "suno", fp)
        assert len(candidates) == 1

    def test_find_resumable_ignores_completed_runs(self, tmp_path: Path) -> None:
        config = _config()
        fp = config.fingerprint()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        state = create_initial_state(run_id, config, tmp_path)
        state.status = RunStatus.COMPLETE
        storage.save_state(state)

        candidates = RunStorage.find_resumable(tmp_path, "suno", fp)
        assert candidates == []

    def test_find_resumable_ignores_wrong_fingerprint(self, tmp_path: Path) -> None:
        config = _config()
        run_id = generate_run_id("suno")
        storage = RunStorage(tmp_path, "suno", run_id)
        state = create_initial_state(run_id, config, tmp_path)
        state.status = RunStatus.INTERRUPTED
        storage.save_state(state)

        wrong_fp = "deadbeefdeadbeef"
        candidates = RunStorage.find_resumable(tmp_path, "suno", wrong_fp)
        assert candidates == []
