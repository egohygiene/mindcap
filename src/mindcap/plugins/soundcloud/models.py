"""Pydantic models for SoundCloud provider responses.

All models use ``extra="allow"`` so that unknown future fields survive
normalization without being silently discarded.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SoundCloudUserPayload(BaseModel):
    """Top-level response from GET /users/<id> or GET /me."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    kind: str | None = None
    permalink: str | None = None
    username: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    full_name: str | None = None
    display_name: str | None = None
    description: str | None = None
    city: str | None = None
    country_code: str | None = None
    country: str | None = None
    permalink_url: str | None = None
    avatar_url: str | None = None
    website: str | None = None
    website_title: str | None = None
    verified: bool | None = None
    followers_count: int | None = None
    followings_count: int | None = None
    track_count: int | None = None
    public_favorites_count: int | None = None
    playlist_count: int | None = None
    reposts_count: int | None = None
    likes_count: int | None = None
    comments_count: int | None = None
    plan: str | None = None
    subscriptions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str | None = None
    last_modified: str | None = None
    urn: str | None = None


class SoundCloudTrackPayload(BaseModel):
    """Top-level response from GET /tracks/<id>."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    kind: str | None = None
    permalink: str | None = None
    permalink_url: str | None = None
    urn: str | None = None
    title: str | None = None
    description: str | None = None
    caption: str | None = None
    label_name: str | None = None
    genre: str | None = None
    tag_list: str | None = None
    license: str | None = None
    track_type: str | None = None
    bpm: float | None = None
    key_signature: str | None = None
    isrc: str | None = None
    purchase_url: str | None = None
    purchase_title: str | None = None
    release: str | None = None
    release_date: str | None = None
    original_release_date: str | None = None
    created_at: str | None = None
    last_modified: str | None = None
    display_date: str | None = None
    duration: int | None = None
    full_duration: int | None = None
    sharing: str | None = None
    embeddable_by: str | None = None
    access: str | None = None
    streamable: bool | None = None
    downloadable: bool | None = None
    has_downloads_left: bool | None = None
    download_url: str | None = None
    stream_url: str | None = None
    artwork_url: str | None = None
    waveform_url: str | None = None
    comment_count: int | None = None
    download_count: int | None = None
    playback_count: int | None = None
    likes_count: int | None = None
    reposts_count: int | None = None
    commentable: bool | None = None
    state: str | None = None
    policy: str | None = None
    monetization_model: str | None = None
    user_id: int | None = None
    user: dict[str, Any] = Field(default_factory=dict)
    publisher_metadata: dict[str, Any] = Field(default_factory=dict)
    media: dict[str, Any] = Field(default_factory=dict)
    secret_token: str | None = None
    secret_uri: str | None = None


class SoundCloudPlaylistPayload(BaseModel):
    """Top-level response from GET /playlists/<id>."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    kind: str | None = None
    permalink: str | None = None
    permalink_url: str | None = None
    urn: str | None = None
    title: str | None = None
    description: str | None = None
    label_name: str | None = None
    genre: str | None = None
    tag_list: str | None = None
    license: str | None = None
    release_date: str | None = None
    created_at: str | None = None
    last_modified: str | None = None
    sharing: str | None = None
    access: str | None = None
    track_count: int | None = None
    likes_count: int | None = None
    reposts_count: int | None = None
    commentable: bool | None = None
    is_album: bool | None = None
    set_type: str | None = None
    artwork_url: str | None = None
    user: dict[str, Any] = Field(default_factory=dict)
    tracks: list[dict[str, Any]] = Field(default_factory=list)
    secret_token: str | None = None


class SoundCloudCollectionPagePayload(BaseModel):
    """Paginated collection response (tracks, playlists, etc.)."""

    model_config = ConfigDict(extra="allow")

    collection: list[dict[str, Any]] = Field(default_factory=list)
    next_href: str | None = None
    query_urn: str | None = None
