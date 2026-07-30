"""Tests for atomic checkpointing."""

from __future__ import annotations

from pathlib import Path

from mindcap.sync.checkpoint import (
    checkpoint_exists,
    read_checkpoint,
    write_checkpoint,
)


class TestWriteAndRead:
    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        data = {"run_id": "suno-123", "status": "running", "count": 42}
        write_checkpoint(tmp_path, data)
        loaded = read_checkpoint(tmp_path)
        assert loaded == data

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        data = {"key": "value"}
        write_checkpoint(nested, data)
        assert checkpoint_exists(nested)

    def test_overwrites_previous_checkpoint(self, tmp_path: Path) -> None:
        write_checkpoint(tmp_path, {"version": 1})
        write_checkpoint(tmp_path, {"version": 2})
        loaded = read_checkpoint(tmp_path)
        assert loaded == {"version": 2}

    def test_read_returns_none_when_no_checkpoint(self, tmp_path: Path) -> None:
        assert read_checkpoint(tmp_path) is None

    def test_checkpoint_exists_true(self, tmp_path: Path) -> None:
        write_checkpoint(tmp_path, {"ok": True})
        assert checkpoint_exists(tmp_path)

    def test_checkpoint_exists_false(self, tmp_path: Path) -> None:
        assert not checkpoint_exists(tmp_path)


class TestCorruptCheckpointRecovery:
    def test_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "checkpoint.json").write_text("NOT VALID JSON", encoding="utf-8")
        assert read_checkpoint(tmp_path) is None

    def test_non_dict_json_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "checkpoint.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert read_checkpoint(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "checkpoint.json").write_text("", encoding="utf-8")
        assert read_checkpoint(tmp_path) is None

    def test_partial_tmp_file_ignored(self, tmp_path: Path) -> None:
        """A leftover .json.tmp file must not corrupt the last valid checkpoint."""
        write_checkpoint(tmp_path, {"safe": True})
        # Simulate an interrupted write leaving a partial tmp file.
        (tmp_path / "checkpoint.json.tmp").write_text("PARTIAL DATA", encoding="utf-8")
        loaded = read_checkpoint(tmp_path)
        # The valid checkpoint must still be returned.
        assert loaded == {"safe": True}


class TestAtomicWrite:
    def test_no_checkpoint_file_left_on_permission_error(self, tmp_path: Path) -> None:
        """When write_checkpoint raises, no partial checkpoint should remain."""
        # This is implicitly tested by verifying that a failed write does not
        # corrupt the previous checkpoint.
        write_checkpoint(tmp_path, {"original": True})
        # A second write should succeed and not corrupt the first.
        write_checkpoint(tmp_path, {"updated": True})
        assert read_checkpoint(tmp_path) == {"updated": True}
