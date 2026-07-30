"""Core data models for the Mindcap synchronization subsystem."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Item-level state machine
# ---------------------------------------------------------------------------


class ItemAction(StrEnum):
    """The planned action for one source item in a capture plan."""

    CAPTURE_NEW = "capture-new"
    CAPTURE_CHANGED = "capture-changed"
    RESUME_INCOMPLETE = "resume-incomplete"
    RETRY_FAILED = "retry-failed"
    SKIP_VERIFIED_UNCHANGED = "skip-verified-unchanged"
    PROBE_REQUIRED = "probe-required"
    BLOCKED = "blocked"


class ItemStatus(StrEnum):
    """Runtime execution status of one source item."""

    DISCOVERED = "discovered"
    PLANNED = "planned"
    SKIPPED_VERIFIED_UNCHANGED = "skipped-verified-unchanged"
    PROBING = "probing"
    CAPTURING = "capturing"
    RESUMING = "resuming"
    COMPLETE = "complete"
    COMPLETE_WITH_WARNINGS = "complete-with-warnings"
    UNCHANGED = "unchanged"
    FAILED_RETRYABLE = "failed-retryable"
    FAILED_TERMINAL = "failed-terminal"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


#: Validated state machine transitions.  Terminal states map to an empty set.
VALID_TRANSITIONS: dict[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.DISCOVERED: frozenset({ItemStatus.PLANNED, ItemStatus.BLOCKED}),
    ItemStatus.PLANNED: frozenset(
        {
            ItemStatus.SKIPPED_VERIFIED_UNCHANGED,
            ItemStatus.PROBING,
            ItemStatus.CAPTURING,
            ItemStatus.RESUMING,
            ItemStatus.BLOCKED,
        }
    ),
    ItemStatus.PROBING: frozenset(
        {
            ItemStatus.CAPTURING,
            ItemStatus.SKIPPED_VERIFIED_UNCHANGED,
            ItemStatus.FAILED_RETRYABLE,
            ItemStatus.FAILED_TERMINAL,
            ItemStatus.INTERRUPTED,
        }
    ),
    ItemStatus.CAPTURING: frozenset(
        {
            ItemStatus.COMPLETE,
            ItemStatus.COMPLETE_WITH_WARNINGS,
            ItemStatus.UNCHANGED,
            ItemStatus.FAILED_RETRYABLE,
            ItemStatus.FAILED_TERMINAL,
            ItemStatus.INTERRUPTED,
        }
    ),
    ItemStatus.RESUMING: frozenset(
        {
            ItemStatus.COMPLETE,
            ItemStatus.COMPLETE_WITH_WARNINGS,
            ItemStatus.UNCHANGED,
            ItemStatus.FAILED_RETRYABLE,
            ItemStatus.FAILED_TERMINAL,
            ItemStatus.INTERRUPTED,
        }
    ),
    ItemStatus.FAILED_RETRYABLE: frozenset({ItemStatus.CAPTURING, ItemStatus.RESUMING}),
    ItemStatus.INTERRUPTED: frozenset({ItemStatus.CAPTURING, ItemStatus.RESUMING}),
    # Terminal states — no further transitions without a new attempt record.
    ItemStatus.COMPLETE: frozenset(),
    ItemStatus.COMPLETE_WITH_WARNINGS: frozenset(),
    ItemStatus.UNCHANGED: frozenset(),
    ItemStatus.SKIPPED_VERIFIED_UNCHANGED: frozenset(),
    ItemStatus.FAILED_TERMINAL: frozenset(),
    ItemStatus.BLOCKED: frozenset(),
}


def validate_transition(current: ItemStatus, next_status: ItemStatus) -> None:
    """Raise :exc:`ValueError` for an invalid item state transition.

    Parameters
    ----------
    current:
        The current item status.
    next_status:
        The requested next status.
    """
    allowed = VALID_TRANSITIONS.get(current, frozenset())
    if next_status not in allowed:
        raise ValueError(
            f"Invalid item state transition: "
            f"{current.value!r} → {next_status.value!r}. "
            f"Allowed: {sorted(s.value for s in allowed) or 'none (terminal)'}"
        )


# ---------------------------------------------------------------------------
# Run-level state
# ---------------------------------------------------------------------------


class RunStatus(StrEnum):
    """Overall status of a batch sync run."""

    PLANNING = "planning"
    DISCOVERING = "discovering"
    RUNNING = "running"
    COMPLETE = "complete"
    COMPLETE_WITH_FAILURES = "complete-with-failures"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


# ---------------------------------------------------------------------------
# Source descriptors
# ---------------------------------------------------------------------------


class SourceDescriptor(BaseModel):
    """Canonical description of one independently capturable provider source item.

    Fields
    ------
    provider:
        Registered plugin name, e.g. ``"suno"`` or ``"distrokid"``.
    source_type:
        Source type within the provider, e.g. ``"workspace"`` or
        ``"release"``.
    canonical_identifier:
        Stable, provider-unique identifier.
    canonical_url:
        Canonical URL for the source, if known.
    display_title:
        Human-readable title for progress output.
    collection_position:
        Provider-reported ordering position (used as the primary sort key).
    remote_revision:
        Provider-supplied revision token (update timestamp, revision ID,
        version token, or stable metadata fingerprint).  ``None`` means the
        provider did not supply trustworthy revision evidence.
    remote_updated_at:
        Provider-supplied last-update timestamp, when available.
    remote_status:
        Provider-reported status string (e.g. ``"active"``, ``"trashed"``).
    safe_metadata:
        Additional provider metadata that is safe to persist (no auth tokens,
        no signed URLs, no cookies).
    """

    model_config = ConfigDict(extra="allow")

    provider: str
    source_type: str
    canonical_identifier: str
    canonical_url: str | None = None
    display_title: str | None = None
    collection_position: int | None = None
    remote_revision: str | None = None
    remote_updated_at: datetime | None = None
    remote_status: str | None = None
    safe_metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cache evaluation
# ---------------------------------------------------------------------------


class CacheDecision(BaseModel):
    """Evidence-backed decision about whether a source capture can be skipped.

    A directory existing on disk is never sufficient evidence on its own.
    An item can only be skipped when all five conditions are met:

    1. A finalized, latest archive exists.
    2. The archive passes offline verification.
    3. Provider and canonical identifier match.
    4. The collection adapter supplies trustworthy remote revision evidence.
    5. The remote revision matches the revision recorded in the local archive.
    """

    decision: Literal["skip", "capture", "probe"]
    reason: str
    local_archive_version: int | None = None
    local_content_hash: str | None = None
    remote_revision: str | None = None
    metadata_probe_hash: str | None = None
    verification_result: Literal["pass", "fail", "not-checked"] = "not-checked"


# ---------------------------------------------------------------------------
# Attempt and item records
# ---------------------------------------------------------------------------


class AttemptRecord(BaseModel):
    """One capture attempt for a source item.  Preserved; never overwritten."""

    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    status: ItemStatus
    error: str | None = None
    archive_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class CaptureItemRecord(BaseModel):
    """Full tracking record for one source item across a batch run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    descriptor: SourceDescriptor
    action: ItemAction | None = None
    status: ItemStatus = ItemStatus.DISCOVERED
    cache_decision: CacheDecision | None = None
    retry_count: int = 0
    max_retries: int = 3
    attempts: list[AttemptRecord] = Field(default_factory=list)
    archive_path: str | None = None
    archive_version: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def transition(self, next_status: ItemStatus) -> None:
        """Apply a validated state transition.

        Raises
        ------
        ValueError
            If the transition is not allowed by the state machine.
        """
        validate_transition(self.status, next_status)
        self.status = next_status

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` when the item is in a terminal state."""
        return not VALID_TRANSITIONS.get(self.status, frozenset())

    @property
    def is_retryable(self) -> bool:
        """Return ``True`` when the item can be retried."""
        return (
            self.status == ItemStatus.FAILED_RETRYABLE
            and self.retry_count < self.max_retries
        )


# ---------------------------------------------------------------------------
# Discovery result
# ---------------------------------------------------------------------------


class DiscoveryResult(BaseModel):
    """Evidence record for one collection discovery run.

    *discovery_complete* must only be ``True`` when there is a documented
    terminal signal or explicit best-effort evidence.  Disagreement between
    *expected_item_count* and *unique_items_discovered* forces
    *discovery_complete* to ``False``.
    """

    provider: str
    collection_identifier: str
    collection_url: str | None = None
    expected_item_count: int | None = None
    unique_items_discovered: int = 0
    pages_observed: int = 0
    duplicate_identifiers_observed: int = 0
    terminal_signal: str | None = None
    repeated_page_protection_triggered: bool = False
    repeated_cursor_protection_triggered: bool = False
    discovery_complete: bool = False
    warnings: list[str] = Field(default_factory=list)
    discovered_at: datetime | None = None


# ---------------------------------------------------------------------------
# Run configuration and state
# ---------------------------------------------------------------------------


class BatchRunConfig(BaseModel):
    """Stable configuration fingerprint that identifies an unambiguous run.

    Two runs are compatible for resume only when their configuration
    fingerprints match.
    """

    provider: str
    collection_identifier: str
    collection_url: str | None = None
    concurrency: int = 1
    max_items: int | None = None
    force: bool = False
    dry_run: bool = False
    wait_seconds: float = 10.0
    options: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        """Return a stable SHA-256 fingerprint of this configuration."""
        stable = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(stable.encode()).hexdigest()[:16]


class BatchRunState(BaseModel):
    """Persisted batch run state stored outside finalized archives.

    Stored at::

        {artifact_root}/runs/{provider}/{run_id}/run.json

    Never store credentials, cookies, tokens, or signed URLs here.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    schema_version: str = "mindcap.sync-run/v0.1"
    run_id: str
    provider: str
    collection_identifier: str
    collection_url: str | None = None
    config: BatchRunConfig
    config_fingerprint: str
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    discovery_result: DiscoveryResult | None = None
    items: list[CaptureItemRecord] = Field(default_factory=list)
    status: RunStatus = RunStatus.PLANNING
    interruption_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    artifact_root: str | None = None

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    def item_by_id(self, canonical_identifier: str) -> CaptureItemRecord | None:
        """Return the item record for *canonical_identifier*, if present."""
        for item in self.items:
            if item.descriptor.canonical_identifier == canonical_identifier:
                return item
        return None

    def counts(self) -> dict[str, int]:
        """Return a summary count dict suitable for progress display."""
        result: dict[str, int] = {
            "discovered": len(self.items),
            "planned": 0,
            "skipped": 0,
            "capturing": 0,
            "resuming": 0,
            "complete": 0,
            "complete_with_warnings": 0,
            "unchanged": 0,
            "failed_retryable": 0,
            "failed_terminal": 0,
            "interrupted": 0,
            "blocked": 0,
        }
        for item in self.items:
            key = item.status.value.replace("-", "_")
            if key in result:
                result[key] += 1
        result["completed"] = (
            result["complete"]
            + result["complete_with_warnings"]
            + result["unchanged"]
            + result["skipped"]
        )
        result["failed"] = result["failed_retryable"] + result["failed_terminal"]
        return result


# ---------------------------------------------------------------------------
# Collection-level output
# ---------------------------------------------------------------------------


class CollectionRequest(BaseModel):
    """Parameters passed to a CollectionDiscoveryStrategy.

    Fields
    ------
    provider:
        Registered plugin name.
    collection_identifier:
        Stable, provider-unique identifier for the collection (e.g. account
        user ID, library slug, or collection URL path).
    collection_url:
        Entry-point URL for browser-backed discovery, when required.
    artifact_root:
        Private artifact root for reading existing archives.
    max_items:
        Optional cap on the number of items to discover.
    options:
        Provider-specific options forwarded from the CLI.
    """

    provider: str
    collection_identifier: str
    collection_url: str | None = None
    artifact_root: Path
    max_items: int | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CollectionArchiveManifest(BaseModel):
    """Manifest for a collection-level archive version."""

    schema_version: str = "mindcap.collection-manifest/v0.1"
    provider: str
    collection_id: str
    run_id: str
    archive_version: int
    discovery_time: datetime | None = None
    completion_time: datetime | None = None
    discovery_complete: bool = False
    source_count: int = 0
    complete_count: int = 0
    unchanged_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    not_observed_count: int = 0
    source_archive_refs: list[dict[str, Any]] = Field(default_factory=list)
    not_observed_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    config_fingerprint: str | None = None


# ---------------------------------------------------------------------------
# Sync result (machine-readable output)
# ---------------------------------------------------------------------------


class SyncResult(BaseModel):
    """Machine-readable result emitted with ``--json``."""

    schema_version: str = "mindcap.sync-result/v1"
    provider: str
    run_id: str
    status: RunStatus
    discovered: int = 0
    completed: int = 0
    unchanged: int = 0
    resumed: int = 0
    failed: int = 0
    skipped: int = 0
    run_path: str | None = None
    collection_archive: str | None = None


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_FATAL = 1
EXIT_PARTIAL_FAILURE = 2
EXIT_INTERRUPTED = 130
EXIT_TERMINATED = 143


def run_exit_code(state: BatchRunState) -> int:
    """Return the appropriate process exit code for a completed run."""
    if state.status == RunStatus.INTERRUPTED:
        return EXIT_INTERRUPTED
    if state.status == RunStatus.FAILED:
        return EXIT_FATAL
    if state.status == RunStatus.COMPLETE_WITH_FAILURES:
        return EXIT_PARTIAL_FAILURE
    return EXIT_SUCCESS


def collection_root(artifact_root: Path, provider: str) -> Path:
    """Return the canonical collection archive directory for a provider."""
    return artifact_root / "collections" / provider


def run_root(artifact_root: Path, provider: str, run_id: str) -> Path:
    """Return the run state directory for *run_id*."""
    return artifact_root / "runs" / provider / run_id
