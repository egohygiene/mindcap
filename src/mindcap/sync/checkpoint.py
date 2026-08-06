"""Atomic checkpointing for the Mindcap synchronization subsystem.

Checkpoints are written with a write-to-temp-then-atomic-replace strategy so
that an interrupted write never leaves a partially serialized checkpoint on
disk.  The previous valid checkpoint is always preserved until the new one is
fully written.

Checkpoint writes occur after meaningful transitions:

- discovery page completed
- plan finalized
- item started
- item phase completed
- item finalized
- item failed
- run interrupted
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

_CHECKPOINT_FILENAME = "checkpoint.json"


def _temp_path(target: Path) -> Path:
    """Return a sibling temporary path for atomic replacement."""
    return target.with_suffix(".json.tmp")


def write_checkpoint(directory: Path, data: dict[str, Any]) -> None:
    """Write *data* as an atomic JSON checkpoint in *directory*.

    The write is performed as:

    1. Serialize *data* to JSON.
    2. Write to a sibling ``.json.tmp`` file.
    3. Flush to the OS.
    4. Atomically replace the target path via :func:`os.replace`.

    An interrupted write between steps 2 and 4 leaves the previous checkpoint
    intact.  A partial ``.tmp`` file is harmless on recovery.

    Parameters
    ----------
    directory:
        Directory that will contain ``checkpoint.json``.
    data:
        Serializable dict to persist.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / _CHECKPOINT_FILENAME
    temp = _temp_path(target)
    encoded = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            try:
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                # fsync is best-effort; not all filesystems support it.
                pass
        os.replace(temp, target)
    except Exception:
        # Best-effort cleanup of the temp file on failure.
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise


def read_checkpoint(directory: Path) -> dict[str, Any] | None:
    """Load and return the latest valid checkpoint from *directory*.

    Returns ``None`` when no checkpoint exists.

    A partial ``.json.tmp`` sibling is silently discarded; the caller always
    receives the last fully written checkpoint.

    Parameters
    ----------
    directory:
        Directory containing ``checkpoint.json``.
    """
    target = directory / _CHECKPOINT_FILENAME
    if not target.is_file():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            return None
        return loaded
    except (OSError, json.JSONDecodeError):
        return None


def checkpoint_exists(directory: Path) -> bool:
    """Return ``True`` when a readable checkpoint exists in *directory*."""
    return (directory / _CHECKPOINT_FILENAME).is_file()
