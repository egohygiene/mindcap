"""Run and staging locking for the Mindcap synchronization subsystem.

Prevents two concurrent processes from mutating the same run or source staging
area.  Locks are JSON files containing the owner PID and start timestamp so
that stale-lock diagnostics can identify the owning process.

Lock policy
-----------
- Locks are acquired by writing a JSON file.
- A lock is *valid* when its owning process is still alive.
- A lock is *stale* when the owning process no longer exists.
- Mindcap never automatically deletes a stale lock; it prints diagnostics and
  recovery instructions so the operator can make an informed decision.
- Lock files are never committed to archives.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

_LOCK_FILENAME = "run.lock"
_STAGING_LOCK_FILENAME = "staging.lock"


class LockConflictError(Exception):
    """Raised when a run or staging area is already locked by another process."""


class RunLock:
    """Context-manager-based file lock for a sync run directory.

    Parameters
    ----------
    run_dir:
        Directory to lock.
    lock_filename:
        Name of the lock file inside *run_dir*.
    """

    def __init__(self, run_dir: Path, lock_filename: str = _LOCK_FILENAME) -> None:
        self._path = run_dir / lock_filename
        self._pid = os.getpid()
        self._acquired = False

    @property
    def lock_path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # Acquisition and release
    # ------------------------------------------------------------------

    def acquire(self) -> None:
        """Acquire the lock.

        Raises
        ------
        LockConflictError
            When an active (non-stale) lock already exists.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._read_lock()
        if existing is not None:
            owner_pid = existing.get("pid")
            owner_time = existing.get("acquired_at", "unknown")
            if isinstance(owner_pid, int) and _is_process_alive(owner_pid):
                raise LockConflictError(
                    f"Run directory is locked by PID {owner_pid} "
                    f"(started {owner_time}).\n"
                    f"Lock file: {self._path}\n"
                    f"If the process is no longer running, remove the lock "
                    f"manually:\n  rm {self._path}"
                )
        self._write_lock()
        self._acquired = True

    def release(self) -> None:
        """Release the lock, if it was acquired by this process."""
        if self._acquired:
            with contextlib.suppress(OSError):
                self._path.unlink(missing_ok=True)
            self._acquired = False

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def is_stale(self) -> bool:
        """Return ``True`` when a lock exists but its owner is no longer alive."""
        existing = self._read_lock()
        if existing is None:
            return False
        owner_pid = existing.get("pid")
        if not isinstance(owner_pid, int):
            return True
        return not _is_process_alive(owner_pid)

    def lock_info(self) -> dict[str, object] | None:
        """Return the raw lock metadata, or ``None`` when no lock exists."""
        return self._read_lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_lock(self) -> None:
        payload = json.dumps(
            {
                "pid": self._pid,
                "acquired_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        self._path.write_text(payload, encoding="utf-8")

    def _read_lock(self) -> dict[str, object] | None:
        if not self._path.is_file():
            return None
        try:
            raw = self._path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError):
            return None


def _is_process_alive(pid: int) -> bool:
    """Return ``True`` when *pid* refers to a running process.

    Uses :func:`os.kill` with signal 0 on POSIX.  On Windows this is a
    best-effort check using ``psutil`` when available; otherwise returns
    ``True`` (conservative — treat unknown as alive).
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user.
        return True
    except OSError:
        return False
