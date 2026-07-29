from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SunoWorkspacePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    description: str | None = None
    clips: list[dict[str, Any]] = Field(default_factory=list)


class SunoClipPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SunoFeedPagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    clips: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
