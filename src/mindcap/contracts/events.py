"""Typed event models for presentation-neutral progress reporting.

These events are emitted by application services and consumed by any
registered :class:`EventSink` implementation.  They contain no credentials,
raw provider payloads, or terminal-specific formatting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class OperationStarted:
    """Emitted when a top-level operation begins."""

    operation_id: str
    operation: str
    provider: str | None = None
    started_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class PhaseStarted:
    """Emitted when a named phase within an operation begins."""

    operation_id: str
    phase: str
    provider: str | None = None


@dataclass(frozen=True)
class PhaseCompleted:
    """Emitted when a named phase completes successfully."""

    operation_id: str
    phase: str
    provider: str | None = None


@dataclass(frozen=True)
class ItemStarted:
    """Emitted when processing of an individual item begins."""

    operation_id: str
    item_id: str
    provider: str | None = None
    total: int | None = None
    completed: int = 0


@dataclass(frozen=True)
class ItemProgress:
    """Emitted to report incremental progress on an item."""

    operation_id: str
    item_id: str
    bytes_processed: int = 0
    detail: str = ""


@dataclass(frozen=True)
class ItemCompleted:
    """Emitted when an individual item finishes processing."""

    operation_id: str
    item_id: str
    status: str
    completed: int = 0
    total: int | None = None


@dataclass(frozen=True)
class WarningEmitted:
    """Emitted when a non-fatal warning occurs."""

    operation_id: str
    message: str
    provider: str | None = None
    warning_code: str | None = None


@dataclass(frozen=True)
class RetryScheduled:
    """Emitted when an operation will be retried."""

    operation_id: str
    item_id: str
    attempt: int
    reason: str


@dataclass(frozen=True)
class OperationCompleted:
    """Emitted when a top-level operation completes successfully."""

    operation_id: str
    operation: str
    provider: str | None = None
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class OperationFailed:
    """Emitted when a top-level operation fails."""

    operation_id: str
    operation: str
    error: str
    provider: str | None = None


MindcapEvent = (
    OperationStarted
    | PhaseStarted
    | PhaseCompleted
    | ItemStarted
    | ItemProgress
    | ItemCompleted
    | WarningEmitted
    | RetryScheduled
    | OperationCompleted
    | OperationFailed
)
