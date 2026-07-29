from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SunoProjectPayload(BaseModel):
    """Top-level response from GET /api/project/{id}."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    description: str | None = None
    clip_count: int | None = None
    current_page: int | None = None
    shared: bool | None = None
    is_owned: bool | None = None
    is_public: bool | None = None
    is_trashed: bool | None = None
    project_clips: list[dict[str, Any]] = Field(default_factory=list)


class SunoWorkspacePayload(BaseModel):
    """Legacy workspace response schema retained for backward compatibility."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    description: str | None = None
    clips: list[dict[str, Any]] = Field(default_factory=list)


class SunoClipPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    entity_type: str | None = None
    title: str | None = None
    status: str | None = None
    created_at: str | None = None
    duration: float | None = None
    model_name: str | None = None
    major_model_version: str | None = None
    uses_latest_model: bool | None = None
    audio_url: str | None = None
    video_url: str | None = None
    image_url: str | None = None
    image_large_url: str | None = None
    media_urls: list[dict[str, Any]] = Field(default_factory=list)
    display_tags: list[Any] = Field(default_factory=list)
    badges: list[Any] = Field(default_factory=list)
    secondary_badges: list[Any] = Field(default_factory=list)
    edited_clip_id: str | None = None
    cover_clip_id: str | None = None
    user_id: str | None = None
    display_name: str | None = None
    handle: str | None = None
    is_public: bool | None = None
    explicit: bool | None = None
    comment_count: int | None = None
    flag_count: int | None = None
    ownership: dict[str, Any] = Field(default_factory=dict)
    reactions: dict[str, Any] = Field(default_factory=dict)
    action_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SunoFeedPagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    clips: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
