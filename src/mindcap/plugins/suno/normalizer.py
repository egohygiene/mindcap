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
        if unit.endpoint_category in {"workspace", "clips-page"}:
            for clip in _object_list(payload.get("clips")):
                if isinstance(clip, dict) and clip.get("id"):
                    clips[str(clip["id"])] = {**clips.get(str(clip["id"]), {}), **clip}
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


def normalize_suno(
    envelope: CaptureEnvelope, requested_identifier: str
) -> dict[str, Any]:
    workspace = _workspace_payload(envelope)
    clips, lyric_payloads, aligned_payloads = _clip_payloads(envelope)
    normalized_clips: list[dict[str, Any]] = []
    for clip_id, clip in sorted(clips.items()):
        metadata = _object_dict(clip.get("metadata"))
        lyrics_payload = _object_dict(lyric_payloads.get(clip_id))
        aligned_payload = _object_dict(aligned_payloads.get(clip_id))
        normalized_clips.append(
            {
                "clip_id": clip_id,
                "workspace_id": requested_identifier,
                "title": clip.get("title"),
                "status": clip.get("status"),
                "created_at": clip.get("created_at") or metadata.get("created_at"),
                "updated_at": clip.get("updated_at") or metadata.get("updated_at"),
                "duration": metadata.get("duration") or clip.get("duration"),
                "bpm": metadata.get("bpm")
                or metadata.get("avg_bpm")
                or clip.get("bpm"),
                "model": clip.get("model_name")
                or metadata.get("model")
                or metadata.get("model_name"),
                "tags": _object_list(metadata.get("tags") or clip.get("tags")),
                "instrumental": metadata.get("make_instrumental")
                or clip.get("instrumental"),
                "lyrics": {
                    "plain": lyrics_payload.get("text")
                    or metadata.get("lyrics")
                    or clip.get("lyrics"),
                    "aligned_words": _object_list(aligned_payload.get("aligned_words")),
                },
                "prompts": {
                    "prompt": metadata.get("prompt") or clip.get("prompt"),
                    "lyrics_prompt": metadata.get("lyrics_prompt")
                    or clip.get("lyrics_prompt"),
                    "style_prompt": metadata.get("style_prompt")
                    or clip.get("style_prompt"),
                    "excluded_styles": _object_list(metadata.get("excluded_styles")),
                },
                "audio_url": clip.get("audio_url"),
                "video_url": clip.get("video_url"),
                "image_url": clip.get("image_url"),
                "image_large_url": clip.get("image_large_url")
                or metadata.get("image_large_url"),
                "parent_id": clip.get("parent_id") or metadata.get("parent_id"),
                "source_id": clip.get("source_id") or metadata.get("source_id"),
                "provider_metadata": clip,
                "archived_assets": [
                    {
                        "asset_id": asset.asset_id,
                        "asset_type": asset.asset_type,
                        "relative_path": asset.relative_path,
                        "capture_status": asset.capture_status,
                        "checksum": asset.checksum,
                    }
                    for asset in envelope.assets
                    if asset.clip_id == clip_id
                ],
            }
        )

    return {
        "schema": "mindcap.suno-workspace/v0.1",
        "provider": "suno",
        "source_id": f"suno-{requested_identifier}",
        "workspace_id": requested_identifier,
        "canonical_url": envelope.canonical_url,
        "title": workspace.get("title")
        or workspace.get("name")
        or f"Suno Workspace {requested_identifier}",
        "description": workspace.get("description"),
        "created_at": workspace.get("created_at"),
        "updated_at": workspace.get("updated_at"),
        "owner_id": workspace.get("owner_id") or workspace.get("user_id"),
        "capture_completeness": "partial" if envelope.warnings else "complete",
        "pagination_evidence": [
            unit.safe_metadata
            for unit in envelope.response_units
            if unit.endpoint_category == "clips-page"
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
