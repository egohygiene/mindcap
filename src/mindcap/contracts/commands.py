"""Typed command models for the Mindcap application layer.

These models are the input side of the public Mindcap library API.
They are independent of Typer and Rich.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class CaptureCommand(BaseModel):
    """Request to capture a single source through a registered plugin."""

    provider: str
    source: str | None = None
    strategy: str | None = None
    output_root: Path | None = None
    wait_seconds: float = Field(default=10.0, ge=1.0, le=120.0)
    options: dict[str, Any] = Field(default_factory=dict)
    identifier_override: str | None = None
    force: bool = False


class ImportCommand(BaseModel):
    """Request to batch-import a previously exported source (e.g. ChatGPT ZIP)."""

    provider: str
    source: str
    output_root: Path | None = None
    conversation_id_filter: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class SyncCommand(BaseModel):
    """Request to synchronize an entire provider account collection."""

    provider: str
    collection_url: str | None = None
    output_root: Path | None = None
    run_id: str | None = None
    resume: bool = False
    retry_failed: bool = False
    force: bool = False
    dry_run: bool = False
    max_items: int | None = None
    concurrency: int = Field(default=1, ge=1, le=16)
    wait_seconds: float = Field(default=10.0, ge=1.0, le=120.0)


class VerifyCommand(BaseModel):
    """Request to verify the integrity of a captured bundle."""

    bundle_path: Path


class InspectCommand(BaseModel):
    """Request to inspect a captured archive."""

    provider: str
    archive_path: Path
    verbose: bool = False


class AuthenticationCommand(BaseModel):
    """Request to authenticate against a provider."""

    provider: str
    options: dict[str, Any] = Field(default_factory=dict)


class DoctorCommand(BaseModel):
    """Request to run diagnostic checks for a provider."""

    provider: str
    verbose: bool = False


class PluginListCommand(BaseModel):
    """Request to list all registered plugins."""


class PathsCommand(BaseModel):
    """Request to resolve key filesystem paths."""
