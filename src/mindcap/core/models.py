from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CaptureRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_type: str
    source: str
    provider: str
    canonical_identifier: str
    canonical_url: str | None = None
    strategy: str
    artifact_root: Path
    wait_seconds: float = Field(default=10.0, ge=1.0, le=120.0)
    sensitivity: Literal["public", "normal", "sensitive", "restricted"] = "sensitive"
    options: dict[str, Any] = Field(default_factory=dict)


class RawResponseUnit(BaseModel):
    unit_id: str
    sequence: int
    media_type: str
    body: bytes
    source_url: str | None = None
    endpoint_category: str | None = None
    retrieved_at: datetime | None = None
    safe_metadata: dict[str, Any] = Field(default_factory=dict)


class CapturedAsset(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    asset_id: str
    workspace_id: str
    clip_id: str | None = None
    asset_type: str
    media_type: str
    source_url: str | None = None
    relative_path: str
    temporary_path: Path
    byte_size: int | None = None
    checksum_algorithm: str = "sha256"
    checksum: str | None = None
    capture_status: Literal[
        "downloaded",
        "verified",
        "skipped",
        "failed",
        "missing",
    ] = "downloaded"
    downloaded_at: datetime | None = None
    http_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CaptureEnvelope(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    source_type: str
    canonical_identifier: str
    canonical_url: str | None
    captured_at: datetime
    strategy: str
    response_units: list[RawResponseUnit]
    assets: list[CapturedAsset] = Field(default_factory=list)
    safe_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class StoredBundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_id: str
    version: int
    path: Path
    status: Literal["complete", "unchanged"]
    canonical_content_hash: str
