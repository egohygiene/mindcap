from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from mindcap.core.hashing import sha256_bytes
from mindcap.core.models import CapturedAsset, CaptureEnvelope, CaptureRequest
from mindcap.core.progress import CaptureProgressReporter, CaptureStats
from mindcap.plugins.suno.archive.downloader import SunoAssetDownloader
from mindcap.plugins.suno.client import SunoClient

# Matches version-like encoding identifiers such as "1.0.0.1.0.0" that come
# from the Suno media_urls API.  We normalise these to descriptive names.
_VERSION_LIKE = re.compile(r"^\d+(?:\.\d+){2,}$")


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
        entry_meta = {k: v for k, v in entry.items() if k != "clip"}
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


def _descriptive_media_filename(encoding: str, content_type: str) -> str:
    """Return a human-readable filename for a media_urls variant.

    The Suno API occasionally exposes version-like encoding identifiers such
    as ``"1.0.0.1.0.0"`` which are opaque and hard to inspect in a file
    browser.  This function maps those to a descriptive name based on the
    content-type or encoding key.
    """
    if encoding and not _VERSION_LIKE.match(encoding):
        # Already a meaningful label (e.g. "opus", "hls", "mp4")
        base = encoding.lower().replace(" ", "-")
    elif "audio" in content_type:
        fmt = content_type.split("/")[-1].split(";")[0].strip()
        base = f"audio-{fmt}" if fmt and fmt != "octet-stream" else "audio-variant"
    elif "video" in content_type:
        fmt = content_type.split("/")[-1].split(";")[0].strip()
        base = f"video-{fmt}" if fmt and fmt != "octet-stream" else "video-variant"
    else:
        base = "variant"
    # Extension from content-type (best-effort)
    ct_ext = content_type.split("/")[-1].split(";")[0].strip()
    ext = ct_ext if ct_ext and ct_ext != "octet-stream" else "bin"
    return f"{base}.{ext}"


class SunoWorkspaceCaptureService:
    def __init__(
        self,
        client: SunoClient | None = None,
        downloader: SunoAssetDownloader | None = None,
        reporter: CaptureProgressReporter | None = None,
    ) -> None:
        self._client = client
        self._downloader = downloader
        self._reporter = reporter or CaptureProgressReporter()

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
                        f"clips/{clip_id}/video/original.mp4",
                    )
                )
        # Additional media_urls variants beyond primary audio/video
        seen_urls = {audio_url, video_url}
        for media_entry in _object_list(clip.get("media_urls")):
            if not isinstance(media_entry, dict):
                continue
            url = media_entry.get("url")
            encoding = media_entry.get("encoding") or ""
            content_type = media_entry.get("content_type") or "application/octet-stream"
            if not isinstance(url, str) or not url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            asset_type = f"media-{encoding}" if encoding else "media-variant"
            filename = _descriptive_media_filename(encoding, content_type)
            candidates.append(
                (
                    asset_type,
                    url,
                    content_type,
                    f"clips/{clip_id}/media/{filename}",
                )
            )
        return candidates

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        reporter = self._reporter
        client = self._client or SunoClient()
        downloader = self._downloader or SunoAssetDownloader(client)
        warnings: list[str] = []
        response_units = []
        assets: list[CapturedAsset] = []
        stats = CaptureStats()

        # ── Phase 1: Authentication / project resolution ─────────────────
        with reporter.spinner("Connecting to Studio API..."):
            workspace, workspace_record = client.get_workspace(
                request.canonical_identifier
            )
        response_units.append(workspace_record.to_raw_response_unit("workspace-000", 0))

        # Collect clips from the first workspace response.
        clips_by_id: dict[str, dict[str, Any]] = _extract_clips_from_project_response(
            workspace
        )
        expected_clip_count: int | None = (
            workspace.get("clip_count")
            if isinstance(workspace.get("clip_count"), int)
            else None
        )
        project_title: str | None = workspace.get("name") or workspace.get("title")
        if project_title:
            reporter.phase(f"Project: [bold]{project_title}[/bold]")
        if expected_clip_count is not None:
            reporter.phase(f"Expected clips: [bold]{expected_clip_count}[/bold]")

        # ── Phase 2: Pagination ───────────────────────────────────────────
        reporter.phase("Downloading project metadata...")
        additional_pages = client.get_project_pages(request.canonical_identifier)
        for index, (page, record) in enumerate(additional_pages, start=1):
            page_clips = _extract_clips_from_project_response(page)
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
            reporter.detail(f"Fetched project page {index}")

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
            reporter.detail(f"Fetched legacy clips page {index}")

        total_clips = len(clips_by_id)
        stats.clips_discovered = total_clips

        # ── Phase 3: Per-clip metadata (detail + lyrics) ──────────────────
        reporter.phase("Downloading clip metadata...")
        sorted_clips = sorted(clips_by_id.items())
        with reporter.progress_bar("Metadata", total_clips) as bar:
            for clip_id, clip in sorted_clips:
                detail = client.get_clip_detail(clip_id)
                if detail is not None:
                    payload, record = detail
                    clip.update(payload)
                    response_units.append(
                        record.to_raw_response_unit(
                            f"clip-{clip_id}", len(response_units)
                        )
                    )
                    reporter.detail(f"Fetched detail for {clip_id}")
                bar.advance()

        reporter.phase("Downloading lyrics...")
        with reporter.progress_bar("Lyrics", total_clips) as bar:
            for clip_id, _clip in sorted_clips:
                lyrics = client.get_lyrics(clip_id)
                if lyrics is not None:
                    _, record = lyrics
                    response_units.append(
                        record.to_raw_response_unit(
                            f"lyrics-{clip_id}", len(response_units)
                        )
                    )
                    reporter.detail(f"Fetched lyrics for {clip_id}")
                aligned = client.get_aligned_lyrics(clip_id)
                if aligned is not None:
                    _, record = aligned
                    response_units.append(
                        record.to_raw_response_unit(
                            f"aligned-lyrics-{clip_id}", len(response_units)
                        )
                    )
                bar.advance()

        # ── Phase 4: Asset downloads ──────────────────────────────────────
        audio_count = 0
        artwork_count = 0
        video_count = 0

        # Pre-collect all assets to enable accurate progress bars.
        all_asset_jobs: list[tuple[str, tuple[str, str, str, str]]] = []
        for clip_id, clip in sorted_clips:
            for job in self._asset_candidates(clip, request.options):
                all_asset_jobs.append((clip_id, job))

        reporter.phase("Downloading artwork...")
        artwork_jobs = [
            (cid, job) for cid, job in all_asset_jobs if job[0].startswith("artwork")
        ]
        with reporter.progress_bar("Artwork", len(artwork_jobs)) as bar:
            for clip_id, (asset_type, url, media_type, relative_path) in artwork_jobs:
                downloaded = self._download_asset(
                    clip_id,
                    asset_type,
                    url,
                    media_type,
                    relative_path,
                    request,
                    downloader,
                    assets,
                    warnings,
                    stats,
                )
                if downloaded:
                    artwork_count += 1
                bar.advance()

        reporter.phase("Downloading audio...")
        audio_jobs = [(cid, job) for cid, job in all_asset_jobs if job[0] == "audio"]
        with reporter.progress_bar("Audio", len(audio_jobs)) as bar:
            for clip_id, (asset_type, url, media_type, relative_path) in audio_jobs:
                downloaded = self._download_asset(
                    clip_id,
                    asset_type,
                    url,
                    media_type,
                    relative_path,
                    request,
                    downloader,
                    assets,
                    warnings,
                    stats,
                )
                if downloaded:
                    audio_count += 1
                bar.advance()

        reporter.phase("Downloading videos...")
        video_jobs = [(cid, job) for cid, job in all_asset_jobs if job[0] == "video"]
        with reporter.progress_bar("Videos", len(video_jobs)) as bar:
            for clip_id, (asset_type, url, media_type, relative_path) in video_jobs:
                downloaded = self._download_asset(
                    clip_id,
                    asset_type,
                    url,
                    media_type,
                    relative_path,
                    request,
                    downloader,
                    assets,
                    warnings,
                    stats,
                )
                if downloaded:
                    video_count += 1
                bar.advance()

        # Remaining media variants (not audio/artwork/video)
        other_jobs = [
            (cid, job)
            for cid, job in all_asset_jobs
            if not job[0].startswith("artwork") and job[0] not in ("audio", "video")
        ]
        for clip_id, (asset_type, url, media_type, relative_path) in other_jobs:
            self._download_asset(
                clip_id,
                asset_type,
                url,
                media_type,
                relative_path,
                request,
                downloader,
                assets,
                warnings,
                stats,
            )

        # ── Completeness validation ───────────────────────────────────────
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

        stats.clips_archived = actual_count
        stats.audio_downloaded = audio_count
        stats.artwork_downloaded = artwork_count
        stats.videos_downloaded = video_count

        for w in warnings:
            reporter.warn(w)

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
                "project_title": project_title,
                "audio_count": audio_count,
                "artwork_count": artwork_count,
                "video_count": video_count,
            },
            warnings=warnings,
        )

    def _download_asset(
        self,
        clip_id: str,
        asset_type: str,
        url: str,
        media_type: str,
        relative_path: str,
        request: CaptureRequest,
        downloader: SunoAssetDownloader,
        assets: list[CapturedAsset],
        warnings: list[str],
        stats: CaptureStats,
    ) -> bool:
        """Download a single asset and append to *assets*.  Returns True on success."""
        try:
            local_path, metadata = downloader.download(url)
            byte_size = int(metadata.get("byte_size") or local_path.stat().st_size)
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
                    byte_size=byte_size,
                    checksum=sha256_bytes(local_path.read_bytes()),
                    downloaded_at=datetime.now(UTC),
                    http_metadata={
                        "source_url": metadata.get("source_url", url),
                    },
                )
            )
            stats.bytes_downloaded += byte_size
            self._reporter.detail(
                f"Downloaded {asset_type} for {clip_id} ({byte_size} bytes)"
            )
            return True
        except Exception as error:
            warnings.append(
                f"Asset download failed for {clip_id} ({asset_type}): {error}"
            )
            self._reporter.detail(f"Skipped {asset_type} for {clip_id}: {error}")
            return False
