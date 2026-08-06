"""Typed result models for the Mindcap application layer.

These models are the output side of the public Mindcap library API.
They are serializable without terminal parsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class CaptureResult(BaseModel):
    """Result returned by a single source capture operation."""

    status: Literal["complete", "unchanged"]
    provider: str
    source_id: str
    canonical_identifier: str
    archive_version: int
    archive_path: Path
    canonical_content_hash: str
    safe_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    verification_passed: bool = True


class ImportConversationResult(BaseModel):
    """Result for a single conversation within a batch import."""

    conversation_id: str
    status: Literal["imported", "unchanged", "failed"]
    version: int | None = None
    bundle_path: str | None = None
    source_file: str | None = None
    raw_sha256: str | None = None
    error: str | None = None


class ImportResult(BaseModel):
    """Result returned by a batch import operation."""

    import_id: str
    source: str
    source_sha256: str | None = None
    import_timestamp: str
    conversations_discovered: int = 0
    conversations_imported: int = 0
    conversations_unchanged: int = 0
    conversations_failed: int = 0
    warnings: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    import_path: Path | None = None


class SyncItemResult(BaseModel):
    """Result for a single item within a sync run."""

    source_id: str
    status: str
    archive_path: str | None = None
    error: str | None = None


class SyncResult(BaseModel):
    """Result returned by a provider-wide sync operation."""

    run_id: str
    provider: str
    collection_identifier: str
    status: str
    discovered: int = 0
    completed: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    run_path: Path | None = None
    warnings: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    """Result returned by an archive verification operation."""

    status: Literal["pass", "fail"]
    bundle_path: Path
    checks: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None


class InspectionResult(BaseModel):
    """Result returned by an archive inspection operation."""

    provider: str
    archive_path: Path
    fields: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class DoctorCheckResult(BaseModel):
    """A single diagnostic check result."""

    name: str
    status: str
    detail: str = ""
    sensitive: bool = False


class DoctorResult(BaseModel):
    """Result returned by a provider doctor operation."""

    provider: str
    checks: list[DoctorCheckResult] = Field(default_factory=list)


class PluginDescriptor(BaseModel):
    """Metadata describing a registered source plugin."""

    source_type: str
    strategies: tuple[str, ...] = ()
    capabilities: set[str] = Field(default_factory=set)


class PluginListResult(BaseModel):
    """Result returned by listing registered plugins."""

    plugins: list[PluginDescriptor] = Field(default_factory=list)


class PathEntry(BaseModel):
    """A resolved filesystem path with its purpose."""

    purpose: str
    path: Path
    archive_this: str


class PathResult(BaseModel):
    """Result returned by resolving key filesystem paths."""

    entries: list[PathEntry] = Field(default_factory=list)
