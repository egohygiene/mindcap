from __future__ import annotations

import json
from typing import Any

from mindcap.core.models import CaptureEnvelope


def _load_json(unit_body: bytes) -> dict[str, Any] | None:
    payload = json.loads(unit_body)
    return payload if isinstance(payload, dict) else None


def _workspace_payload(envelope: CaptureEnvelope) -> dict[str, Any]:
    for unit in envelope.response_units:
        if unit.endpoint_category == "workspace":
            payload = _load_json(unit.body)
            if payload is not None:
                return payload
    return {}


def _object_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _object_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _coerce_tags(value: object) -> list[str]:
    """Tags may be a string (space/comma separated) or a list."""
    if isinstance(value, list):
        return [str(t) for t in value if t is not None]
    if isinstance(value, str) and value.strip():
        return [t.strip() for t in value.replace(",", " ").split() if t.strip()]
    return []


def _clip_payloads(
    envelope: CaptureEnvelope,
) -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]
]:
    clips: dict[str, dict[str, Any]] = {}
    lyrics: dict[str, dict[str, Any]] = {}
    aligned: dict[str, dict[str, Any]] = {}
    for unit in envelope.response_units:
        payload = _load_json(unit.body)
        if payload is None:
            continue
        if unit.endpoint_category in {"workspace", "project-page"}:
            # Production: project_clips[].clip
            for entry in _object_list(payload.get("project_clips")):
                if not isinstance(entry, dict):
                    continue
                clip = _object_dict(entry.get("clip"))
                clip_id = clip.get("id")
                if not clip_id:
                    continue
                clip_id = str(clip_id)
                entry_meta = {k: v for k, v in entry.items() if k != "clip"}
                merged = {
                    **clips.get(clip_id, {}),
                    **clip,
                    **{"_project_clip": entry_meta},
                }
                clips[clip_id] = merged
            # Legacy: clips[]
            for clip in _object_list(payload.get("clips")):
                if isinstance(clip, dict) and clip.get("id"):
                    clip_id = str(clip["id"])
                    if clip_id not in clips:
                        clips[clip_id] = {**clips.get(clip_id, {}), **clip}
        elif unit.endpoint_category == "clips-page":
            for clip in _object_list(payload.get("clips")):
                if isinstance(clip, dict) and clip.get("id"):
                    clip_id = str(clip["id"])
                    clips[clip_id] = {**clips.get(clip_id, {}), **clip}
        elif unit.endpoint_category == "clip" and payload.get("id"):
            clip_id = str(payload["id"])
            clips[clip_id] = {**clips.get(clip_id, {}), **payload}
        elif unit.endpoint_category == "lyrics":
            clip_id = unit.unit_id.removeprefix("lyrics-")
            lyrics[clip_id] = payload
        elif unit.endpoint_category == "aligned-lyrics":
            clip_id = unit.unit_id.removeprefix("aligned-lyrics-")
            aligned[clip_id] = payload
    return clips, lyrics, aligned


def _normalize_clip(
    clip_id: str,
    clip: dict[str, Any],
    *,
    workspace_id: str,
    lyrics_payload: dict[str, Any],
    aligned_payload: dict[str, Any],
    assets: list[Any],
) -> dict[str, Any]:
    metadata = _object_dict(clip.get("metadata"))
    project_clip_meta = _object_dict(clip.get("_project_clip"))

    # Prompt fields: preserve both prompt and tags; tags may carry the real prompt.
    prompt = (
        metadata.get("prompt")
        if metadata.get("prompt") is not None
        else clip.get("prompt")
    )
    raw_style_prompt = (
        metadata.get("tags") if metadata.get("tags") is not None else clip.get("tags")
    )
    display_tags_raw = (
        metadata.get("display_tags")
        if metadata.get("display_tags") is not None
        else clip.get("display_tags")
    )

    # Control sliders: extensible dictionary, never hardcode names.
    sliders = _object_dict(
        metadata.get("control_sliders")
        or metadata.get("sliders")
        or clip.get("control_sliders")
        or clip.get("sliders")
    )

    # Remix lineage
    remix_lineage: dict[str, Any] = {}
    for key in ("edited_clip_id", "cover_clip_id"):
        val = clip.get(key) or metadata.get(key)
        if val is not None:
            remix_lineage[key] = val

    # Task metadata
    task_metadata: dict[str, Any] = {}
    for key in ("task", "type", "stream", "refund_credits"):
        val = metadata.get(key)
        if val is not None:
            task_metadata[key] = val

    # Generation flags
    generation_flags: dict[str, Any] = {}
    for key in (
        "make_instrumental",
        "has_vocal",
        "can_publish_with_vocal",
        "has_stem",
        "is_mumble",
    ):
        val = metadata.get(key) if metadata.get(key) is not None else clip.get(key)
        if val is not None:
            generation_flags[key] = val

    return {
        "clip_id": clip_id,
        "workspace_id": workspace_id,
        "title": clip.get("title"),
        "entity_type": clip.get("entity_type"),
        "status": clip.get("status"),
        "created_at": clip.get("created_at") or metadata.get("created_at"),
        "updated_at": clip.get("updated_at") or metadata.get("updated_at"),
        "duration": clip.get("duration") or metadata.get("duration"),
        "bpm": metadata.get("bpm") or metadata.get("avg_bpm") or clip.get("bpm"),
        "model": {
            "name": (
                clip.get("model_name")
                or metadata.get("model_name")
                or metadata.get("model")
            ),
            "major_version": clip.get("major_model_version"),
            "uses_latest": (
                clip.get("uses_latest_model")
                if clip.get("uses_latest_model") is not None
                else metadata.get("uses_latest_model")
            ),
            "badges": _object_list(
                metadata.get("model_badges")
                if metadata.get("model_badges") is not None
                else clip.get("badges")
            ),
            "secondary_badges": _object_list(
                metadata.get("secondary_badges")
                if metadata.get("secondary_badges") is not None
                else clip.get("secondary_badges")
            ),
        },
        "prompts": {
            "prompt": prompt,
            "raw_style_prompt": raw_style_prompt,
            "display_tags": _object_list(display_tags_raw),
            "lyrics_prompt": metadata.get("lyrics_prompt") or clip.get("lyrics_prompt"),
            "style_prompt": metadata.get("style_prompt") or clip.get("style_prompt"),
            "excluded_styles": _object_list(metadata.get("excluded_styles")),
        },
        "lyrics": {
            "plain": lyrics_payload.get("text")
            or metadata.get("lyrics")
            or clip.get("lyrics"),
            "aligned_words": _object_list(aligned_payload.get("aligned_words")),
        },
        "media": {
            "audio_url": clip.get("audio_url"),
            "video_url": clip.get("video_url"),
            "image_url": clip.get("image_url"),
            "image_large_url": (
                clip.get("image_large_url") or metadata.get("image_large_url")
            ),
            "media_urls": _object_list(clip.get("media_urls")),
        },
        "generation_flags": generation_flags,
        "sliders": sliders,
        "task_metadata": task_metadata,
        "remix_lineage": remix_lineage,
        "user": {
            "user_id": clip.get("user_id"),
            "display_name": clip.get("display_name"),
            "handle": clip.get("handle"),
        },
        "publishing": {
            "is_public": clip.get("is_public"),
            "explicit": clip.get("explicit"),
            "comment_count": clip.get("comment_count"),
            "flag_count": clip.get("flag_count"),
        },
        "ownership": _object_dict(clip.get("ownership")),
        "reactions": _object_dict(clip.get("reactions")),
        "action_config": _object_dict(clip.get("action_config")),
        "display": {
            "badges": _object_list(clip.get("badges")),
            "secondary_badges": _object_list(clip.get("secondary_badges")),
        },
        "project_clip_meta": project_clip_meta,
        # Backward-compat fields kept for existing consumers.
        "audio_url": clip.get("audio_url"),
        "video_url": clip.get("video_url"),
        "image_url": clip.get("image_url"),
        "image_large_url": (
            clip.get("image_large_url") or metadata.get("image_large_url")
        ),
        "parent_id": clip.get("parent_id") or metadata.get("parent_id"),
        "source_id": clip.get("source_id") or metadata.get("source_id"),
        "tags": raw_style_prompt,
        "instrumental": generation_flags.get("make_instrumental"),
        "provider_metadata": clip,
        "archived_assets": [
            {
                "asset_id": asset.asset_id,
                "asset_type": asset.asset_type,
                "relative_path": asset.relative_path,
                "capture_status": asset.capture_status,
                "checksum": asset.checksum,
            }
            for asset in assets
            if asset.clip_id == clip_id
        ],
    }


def normalize_suno(
    envelope: CaptureEnvelope, requested_identifier: str
) -> dict[str, Any]:
    workspace = _workspace_payload(envelope)
    clips, lyric_payloads, aligned_payloads = _clip_payloads(envelope)
    normalized_clips: list[dict[str, Any]] = []
    for clip_id, clip in sorted(clips.items()):
        normalized_clips.append(
            _normalize_clip(
                clip_id,
                clip,
                workspace_id=requested_identifier,
                lyrics_payload=_object_dict(lyric_payloads.get(clip_id)),
                aligned_payload=_object_dict(aligned_payloads.get(clip_id)),
                assets=envelope.assets,
            )
        )

    return {
        "schema": "mindcap.suno-workspace/v0.3",
        "provider": "suno",
        "source_id": f"suno-{requested_identifier}",
        "workspace_id": requested_identifier,
        "canonical_url": envelope.canonical_url,
        "title": workspace.get("name")
        or workspace.get("title")
        or f"Suno Workspace {requested_identifier}",
        "description": workspace.get("description"),
        "created_at": workspace.get("created_at"),
        "updated_at": workspace.get("updated_at"),
        "owner_id": workspace.get("owner_id") or workspace.get("user_id"),
        "project_metadata": {
            "clip_count": workspace.get("clip_count"),
            "current_page": workspace.get("current_page"),
            "shared": workspace.get("shared"),
            "is_owned": workspace.get("is_owned"),
            "is_public": workspace.get("is_public"),
            "is_trashed": workspace.get("is_trashed"),
        },
        "capture_completeness": "partial" if envelope.warnings else "complete",
        "pagination_evidence": [
            unit.safe_metadata
            for unit in envelope.response_units
            if unit.endpoint_category in {"clips-page", "project-page"}
        ],
        "warnings": envelope.warnings,
        "raw_response_units": [
            {
                "unit_id": unit.unit_id,
                "endpoint_category": unit.endpoint_category,
                "source_url": unit.source_url,
                "clip_id": unit.unit_id.split("-", 1)[1]
                if unit.unit_id.startswith(("clip-", "lyrics-", "aligned-lyrics-"))
                else None,
            }
            for unit in envelope.response_units
        ],
        "clips": normalized_clips,
        "assets": [
            {
                "asset_id": asset.asset_id,
                "clip_id": asset.clip_id,
                "asset_type": asset.asset_type,
                "relative_path": asset.relative_path,
                "checksum": asset.checksum,
                "capture_status": asset.capture_status,
            }
            for asset in envelope.assets
        ],
        "provider_metadata": workspace,
    }
