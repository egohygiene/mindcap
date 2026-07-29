from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mindcap.core.hashing import sha256_bytes
from mindcap.core.models import CapturedAsset, CaptureEnvelope, CaptureRequest
from mindcap.plugins.suno.archive.downloader import SunoAssetDownloader
from mindcap.plugins.suno.client import SunoClient


def _object_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _object_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_clips_from_project_response(
    payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Extract clips from a project response.

    Handles both the production ``project_clips[].clip`` structure and the
    legacy flat ``clips[]`` structure.  Returns a mapping of clip ID to a dict
    that merges the project-clip entry metadata (relative_index, pinned,
    batch_index) into the clip body so nothing is discarded.
    """
    clips: dict[str, dict[str, Any]] = {}

    # Production structure: project_clips[].clip
    for entry in _object_list(payload.get("project_clips")):
        if not isinstance(entry, dict):
            continue
        clip = _object_dict(entry.get("clip"))
        clip_id = clip.get("id")
        if not clip_id:
            continue
        clip_id = str(clip_id)
        entry_meta = {
            k: v
            for k, v in entry.items()
            if k != "clip"
        }
        merged = {**clips.get(clip_id, {}), **clip, **{"_project_clip": entry_meta}}
        clips[clip_id] = merged

    # Legacy flat structure: clips[]
    for clip in _object_list(payload.get("clips")):
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("id")
        if not clip_id:
            continue
        clip_id = str(clip_id)
        if clip_id not in clips:
            clips[clip_id] = dict(clip)

    return clips


class SunoWorkspaceCaptureService:
    def __init__(
        self,
        client: SunoClient | None = None,
        downloader: SunoAssetDownloader | None = None,
    ) -> None:
        self._client = client
        self._downloader = downloader

    def _asset_candidates(
        self, clip: dict[str, Any], options: dict[str, Any]
    ) -> list[tuple[str, str, str, str]]:
        metadata = _object_dict(clip.get("metadata"))
        clip_id = str(clip.get("id") or "")
        candidates: list[tuple[str, str, str, str]] = []
        audio_url = clip.get("audio_url") or metadata.get("audio_url")
        if isinstance(audio_url, str) and audio_url:
            candidates.append(
                (
                    "audio",
                    audio_url,
                    "audio/mpeg",
                    "clips/"
                    f"{clip_id}/audio/original."
                    f"{options.get('audio_format', 'mp3')}",
                )
            )
        image_url = clip.get("image_url") or metadata.get("image_url")
        if isinstance(image_url, str) and image_url:
            candidates.append(
                (
                    "artwork",
                    image_url,
                    "image/jpeg",
                    f"clips/{clip_id}/artwork/cover.jpg",
                )
            )
        large_image_url = clip.get("image_large_url") or metadata.get("image_large_url")
        if isinstance(large_image_url, str) and large_image_url:
            candidates.append(
                (
                    "artwork-large",
                    large_image_url,
                    "image/jpeg",
                    f"clips/{clip_id}/artwork/cover-large.jpg",
                )
            )
        if options.get("include_video", True):
            video_url = clip.get("video_url") or metadata.get("video_url")
            if isinstance(video_url, str) and video_url:
                candidates.append(
                    (
                        "video",
                        video_url,
                        "video/mp4",
                        f"clips/{clip_id}/video/video.mp4",
                    )
                )
        # Additional media_urls variants beyond primary audio/video
        for media_entry in _object_list(clip.get("media_urls")):
            if not isinstance(media_entry, dict):
                continue
            url = media_entry.get("url")
            encoding = media_entry.get("encoding") or ""
            content_type = media_entry.get("content_type") or "application/octet-stream"
            if not isinstance(url, str) or not url:
                continue
            if url in (audio_url, video_url):
                continue
            asset_type = f"media-{encoding}" if encoding else "media-variant"
            ext = encoding.split("-")[0] if encoding else "bin"
            candidates.append(
                (
                    asset_type,
                    url,
                    content_type,
                    f"clips/{clip_id}/media/{encoding or 'variant'}.{ext}",
                )
            )
        return candidates

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        client = self._client or SunoClient()
        downloader = self._downloader or SunoAssetDownloader(client)
        warnings: list[str] = []
        response_units = []
        assets: list[CapturedAsset] = []

        # Fetch workspace/project metadata (first page).
        workspace, workspace_record = client.get_workspace(request.canonical_identifier)
        response_units.append(workspace_record.to_raw_response_unit("workspace-000", 0))

        # Collect clips from the first workspace response.
        clips_by_id: dict[str, dict[str, Any]] = _extract_clips_from_project_response(
            workspace
        )
        expected_clip_count: int | None = workspace.get("clip_count") if isinstance(
            workspace.get("clip_count"), int
        ) else None

        # Fetch additional pages via the production project pagination endpoint.
        # Skip page 1 if its content is already present in the workspace response.
        additional_pages = client.get_project_pages(request.canonical_identifier)
        for index, (page, record) in enumerate(additional_pages, start=1):
            page_clips = _extract_clips_from_project_response(page)
            # Only record as a separate response unit if it brought new data.
            if page_clips.keys() - clips_by_id.keys() or index == 1:
                response_units.append(
                    record.to_raw_response_unit(
                        f"project-page-{index:03d}", len(response_units)
                    )
                )
            for clip_id, clip in page_clips.items():
                if clip_id not in clips_by_id:
                    clips_by_id[clip_id] = clip
                else:
                    clips_by_id[clip_id] = {**clips_by_id[clip_id], **clip}

        # Supplement with legacy clips-page endpoint when available.
        for index, (page, record) in enumerate(
            client.list_workspace_clips(request.canonical_identifier), start=1
        ):
            response_units.append(
                record.to_raw_response_unit(
                    f"clips-page-{index:03d}", len(response_units)
                )
            )
            for clip in _object_list(page.get("clips")):
                if isinstance(clip, dict) and clip.get("id"):
                    clip_id = str(clip["id"])
                    clips_by_id[clip_id] = {
                        **clips_by_id.get(clip_id, {}),
                        **clip,
                    }

        # Fetch per-clip detail, lyrics, and aligned lyrics; then download assets.
        for clip_id, clip in sorted(clips_by_id.items()):
            detail = client.get_clip_detail(clip_id)
            if detail is not None:
                payload, record = detail
                clip.update(payload)
                response_units.append(
                    record.to_raw_response_unit(f"clip-{clip_id}", len(response_units))
                )
            lyrics = client.get_lyrics(clip_id)
            if lyrics is not None:
                _, record = lyrics
                response_units.append(
                    record.to_raw_response_unit(
                        f"lyrics-{clip_id}", len(response_units)
                    )
                )
            aligned = client.get_aligned_lyrics(clip_id)
            if aligned is not None:
                _, record = aligned
                response_units.append(
                    record.to_raw_response_unit(
                        f"aligned-lyrics-{clip_id}", len(response_units)
                    )
                )
            for asset_type, url, media_type, relative_path in self._asset_candidates(
                clip, request.options
            ):
                try:
                    local_path, metadata = downloader.download(url)
                    assets.append(
                        CapturedAsset(
                            asset_id=f"{clip_id}-{asset_type}",
                            workspace_id=request.canonical_identifier,
                            clip_id=clip_id,
                            asset_type=asset_type,
                            media_type=metadata.get("media_type", media_type),
                            source_url=metadata.get("source_url", url),
                            relative_path=relative_path,
                            temporary_path=local_path,
                            byte_size=int(
                                metadata.get("byte_size") or local_path.stat().st_size
                            ),
                            checksum=sha256_bytes(local_path.read_bytes()),
                            downloaded_at=datetime.now(UTC),
                            http_metadata={
                                "source_url": metadata.get("source_url", url),
                            },
                        )
                    )
                except Exception as error:
                    warnings.append(
                        f"Asset download failed for {clip_id} ({asset_type}): {error}"
                    )

        # Completeness validation.
        actual_count = len(clips_by_id)
        capture_complete: bool
        if expected_clip_count is not None and actual_count < expected_clip_count:
            warnings.append(
                f"Incomplete capture: expected {expected_clip_count} clips, "
                f"archived {actual_count}."
            )
            capture_complete = False
        else:
            capture_complete = True

        return CaptureEnvelope(
            provider="suno",
            source_type="workspace",
            canonical_identifier=request.canonical_identifier,
            canonical_url=request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy=request.strategy,
            response_units=response_units,
            assets=assets,
            safe_metadata={
                "input_kind": "api",
                "clip_count": actual_count,
                "expected_clip_count": expected_clip_count,
                "capture_complete": capture_complete,
                "api_origin": client.api_origin,
            },
            warnings=warnings,
        )
