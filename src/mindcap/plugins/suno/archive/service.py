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
        return candidates

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        client = self._client or SunoClient()
        downloader = self._downloader or SunoAssetDownloader(client)
        warnings: list[str] = []
        response_units = []
        assets: list[CapturedAsset] = []
        workspace, workspace_record = client.get_workspace(request.canonical_identifier)
        response_units.append(workspace_record.to_raw_response_unit("workspace-000", 0))

        clips_by_id: dict[str, dict[str, Any]] = {}
        for clip in _object_list(workspace.get("clips")):
            if isinstance(clip, dict) and clip.get("id"):
                clips_by_id[str(clip["id"])] = dict(clip)

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
                    clips_by_id[str(clip["id"])] = {
                        **clips_by_id.get(str(clip["id"]), {}),
                        **clip,
                    }

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
                "clip_count": len(clips_by_id),
                "api_origin": client.api_origin,
            },
            warnings=warnings,
        )
