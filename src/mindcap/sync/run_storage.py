"""Batch run state persistence for the Mindcap synchronization subsystem.

Run state is stored outside finalized archives so that interrupted runs can be
resumed without touching immutable bundles.

Directory layout::

    {artifact_root}/runs/{provider}/{run-id}/
        run.json         — full BatchRunState
        checkpoint.json  — latest atomic checkpoint
        plan.json        — serialized capture plan (written after planning)
        items.jsonl      — append-only item event log
        events.jsonl     — append-only run event log
        report.json      — final run report (written on completion)
        report.md        — human-readable final report
        run.lock         — process lock (not archived)
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import string
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mindcap.sync.checkpoint import write_checkpoint
from mindcap.sync.models import (
    BatchRunConfig,
    BatchRunState,
    CaptureItemRecord,
    RunStatus,
    run_root,
)

# ---------------------------------------------------------------------------
# Run ID generation
# ---------------------------------------------------------------------------

_RUN_ID_ALPHABET = string.ascii_lowercase + string.digits


def generate_run_id(provider: str) -> str:
    """Return a stable, human-readable run ID.

    Format: ``{provider}-{YYYYMMDDTHHMMSSZ}-{6-char-random}``

    Examples::

        suno-20260730T164500Z-abc123
        distrokid-20260730T164500Z-xyz789
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(6))
    return f"{provider}-{timestamp}-{suffix}"


# ---------------------------------------------------------------------------
# Run storage
# ---------------------------------------------------------------------------


class RunStorage:
    """Read/write helper for one batch run's persisted state.

    Parameters
    ----------
    artifact_root:
        Private artifact root directory.
    provider:
        Registered plugin name.
    run_id:
        Unique run identifier.
    """

    def __init__(self, artifact_root: Path, provider: str, run_id: str) -> None:
        self.artifact_root = artifact_root
        self.provider = provider
        self.run_id = run_id
        self.run_dir = run_root(artifact_root, provider, run_id)

    # ------------------------------------------------------------------
    # Directory management
    # ------------------------------------------------------------------

    def ensure_dir(self) -> Path:
        """Create the run directory and return it."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # Private permissions — best-effort on non-POSIX filesystems.
        with contextlib.suppress(OSError):
            self.run_dir.chmod(0o700)
        return self.run_dir

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save_state(self, state: BatchRunState) -> None:
        """Persist the full run state and update the checkpoint atomically."""
        self.ensure_dir()
        state.updated_at = datetime.now(UTC)
        state_path = self.run_dir / "run.json"
        _atomic_json(state_path, state.model_dump(mode="json"))
        write_checkpoint(
            self.run_dir,
            {
                "run_id": self.run_id,
                "status": state.status.value,
                "updated_at": state.updated_at.isoformat(),
                "item_statuses": {
                    item.descriptor.canonical_identifier: item.status.value
                    for item in state.items
                },
            },
        )

    def load_state(self) -> BatchRunState | None:
        """Load and return the persisted run state, or ``None`` if absent."""
        state_path = self.run_dir / "run.json"
        if not state_path.is_file():
            return None
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            return BatchRunState.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def save_plan(self, items: list[CaptureItemRecord]) -> None:
        """Persist the deterministic capture plan."""
        self.ensure_dir()
        plan: list[dict[str, Any]] = [
            {
                "canonical_identifier": item.descriptor.canonical_identifier,
                "display_title": item.descriptor.display_title,
                "action": item.action.value if item.action else None,
                "status": item.status.value,
                "collection_position": item.descriptor.collection_position,
            }
            for item in items
        ]
        _atomic_json(
            self.run_dir / "plan.json",
            {"run_id": self.run_id, "items": plan},
        )

    def append_item_event(self, event: dict[str, Any]) -> None:
        """Append one item event to the append-only ``items.jsonl`` log."""
        self.ensure_dir()
        event["ts"] = datetime.now(UTC).isoformat()
        with (self.run_dir / "items.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_run_event(self, event: dict[str, Any]) -> None:
        """Append one run-level event to the ``events.jsonl`` log."""
        self.ensure_dir()
        event["ts"] = datetime.now(UTC).isoformat()
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def save_report(self, state: BatchRunState) -> None:
        """Write the final ``report.json`` and ``report.md``."""
        self.ensure_dir()
        counts = state.counts()
        report: dict[str, Any] = {
            "schema": "mindcap.sync-report/v0.1",
            "run_id": state.run_id,
            "provider": state.provider,
            "collection_identifier": state.collection_identifier,
            "collection_url": state.collection_url,
            "status": state.status.value,
            "started_at": state.started_at.isoformat(),
            "completed_at": state.completed_at.isoformat()
            if state.completed_at
            else None,
            "counts": counts,
            "warnings": state.warnings,
            "errors": state.errors,
        }
        _atomic_json(self.run_dir / "report.json", report)
        md = _render_report_markdown(state, counts)
        _atomic_text(self.run_dir / "report.md", md)

    # ------------------------------------------------------------------
    # Run discovery helpers
    # ------------------------------------------------------------------

    @classmethod
    def list_runs(cls, artifact_root: Path, provider: str) -> list[RunStorage]:
        """Return all run storage objects for *provider*, newest first."""
        runs_dir = artifact_root / "runs" / provider
        if not runs_dir.is_dir():
            return []
        entries = sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True)
        return [
            cls(artifact_root, provider, entry.name)
            for entry in entries
            if entry.is_dir()
        ]

    @classmethod
    def find_resumable(
        cls,
        artifact_root: Path,
        provider: str,
        config_fingerprint: str,
    ) -> list[RunStorage]:
        """Return runs that are resumable and share the configuration fingerprint.

        Parameters
        ----------
        artifact_root:
            Private artifact root.
        provider:
            Registered plugin name.
        config_fingerprint:
            Fingerprint of the desired run configuration.
        """
        candidates = []
        for storage in cls.list_runs(artifact_root, provider):
            state = storage.load_state()
            if state is None:
                continue
            if state.config_fingerprint != config_fingerprint:
                continue
            if state.status in {
                RunStatus.INTERRUPTED,
                RunStatus.RUNNING,
                RunStatus.PLANNING,
                RunStatus.DISCOVERING,
            }:
                candidates.append(storage)
        return candidates


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _atomic_json(path: Path, data: object) -> None:
    """Write *data* as JSON to *path* atomically via a sibling temp file."""
    temp = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        with temp.open("w", encoding="utf-8") as fh:
            fh.write(encoded)
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(temp, path)
    except Exception:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise


def _atomic_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically via a sibling temp file."""
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    except Exception:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise


def _render_report_markdown(state: BatchRunState, counts: dict[str, int]) -> str:
    """Return a human-readable Markdown sync report."""
    lines = [
        f"# Sync Report — {state.provider.title()} Account",
        "",
        f"- Run ID: `{state.run_id}`",
        f"- Status: {state.status.value}",
        f"- Provider: {state.provider}",
        f"- Collection: {state.collection_identifier}",
        f"- Started: {state.started_at.isoformat()}",
    ]
    if state.completed_at:
        lines.append(f"- Completed: {state.completed_at.isoformat()}")
    lines += [
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Discovered | {counts['discovered']} |",
        f"| Completed | {counts['completed']} |",
        f"| Unchanged | {counts['unchanged']} |",
        f"| Skipped | {counts['skipped']} |",
        f"| Failed | {counts['failed']} |",
        f"| Interrupted | {counts['interrupted']} |",
        "",
    ]
    if state.warnings:
        lines += ["## Warnings", ""]
        for w in state.warnings:
            lines.append(f"- ⚠ {w}")
        lines.append("")
    if state.errors:
        lines += ["## Errors", ""]
        for e in state.errors:
            lines.append(f"- ✗ {e}")
        lines.append("")
    return "\n".join(lines)


def create_initial_state(
    run_id: str,
    config: BatchRunConfig,
    artifact_root: Path,
) -> BatchRunState:
    """Build and return the initial :class:`~mindcap.sync.models.BatchRunState`."""
    now = datetime.now(UTC)
    return BatchRunState(
        run_id=run_id,
        provider=config.provider,
        collection_identifier=config.collection_identifier,
        collection_url=config.collection_url,
        config=config,
        config_fingerprint=config.fingerprint(),
        started_at=now,
        updated_at=now,
        status=RunStatus.PLANNING,
        artifact_root=str(artifact_root),
    )
