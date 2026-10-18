"""Offline, allowlisted diagnostics for audio links in an existing archive."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from mindcap.core.errors import VerificationError
from mindcap.plugins.suno.archive.verifier import verify_workspace_bundle

_API_HOSTS = {"studio-api-prod.suno.com", "studio-api.prod.suno.com"}
_CDN_HOSTS = {"cdn1.suno.ai", "cdn2.suno.ai", "cdn.suno.ai", "cdn-o.suno.com"}
_SIGNATURE_KEYS = {"signature", "key-pair-id", "policy", "x-amz-signature"}


def _audio_url(clip: dict[str, Any]) -> object:
    provider = clip.get("provider_metadata")
    if not isinstance(provider, dict):
        provider = {}
    metadata = provider.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return (
        clip.get("audio_url") or provider.get("audio_url") or metadata.get("audio_url")
    )


def suno_asset_diagnostics(bundle_path: Path) -> dict[str, Any]:
    """Inspect saved URL shapes without network calls or exposing URL values.

    This deliberately returns counts and fixed labels only: no raw metadata,
    titles, identifiers, hosts, signed links, cookies, or exception messages.
    It works with archives created before asset-count reporting was added.
    """
    bundle_path = bundle_path.expanduser().resolve()
    try:
        manifest = json.loads(
            (bundle_path / "manifest.json").read_text(encoding="utf-8")
        )
        checksums = json.loads(
            (bundle_path / "checksums.json").read_text(encoding="utf-8")
        )
        if manifest.get("workspace_metadata_path") != "workspace/metadata.json":
            raise ValueError
        if (
            sum(
                isinstance(entry, dict)
                and entry.get("path") == "workspace/metadata.json"
                for entry in checksums["files"]
            )
            != 1
        ):
            raise ValueError
        verify_workspace_bundle(bundle_path)
        metadata = json.loads(
            (bundle_path / "workspace" / "metadata.json").read_text(encoding="utf-8")
        )
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("clips"), list
        ):
            raise ValueError
        clips = metadata["clips"]
        if any(not isinstance(clip, dict) for clip in clips):
            raise ValueError
    except (
        VerificationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        raise VerificationError(
            "Suno archive audio metadata could not be verified."
        ) from error

    counts = {
        "missing": 0,
        "invalid": 0,
        "forbidden_endpoint": 0,
        "suno_api": 0,
        "suno_cdn": 0,
        "other_http": 0,
    }
    query_urls = 0
    signed_query_urls = 0
    archived_audio = 0
    for clip in clips:
        assets = clip.get("archived_assets")
        if isinstance(assets, list) and any(
            isinstance(asset, dict)
            and asset.get("asset_type") == "audio"
            and asset.get("capture_status", "downloaded") in ("downloaded", "verified")
            for asset in assets
        ):
            archived_audio += 1
        url = _audio_url(clip)
        if not url:
            counts["missing"] += 1
            continue
        try:
            if not isinstance(url, str):
                raise ValueError
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError
            _ = (
                parsed.port
            )  # Validate malformed/out-of-range ports without emitting them.
            host = parsed.hostname.lower()
            query_keys = {
                key.lower() for key, _ in parse_qsl(parsed.query, max_num_fields=200)
            }
        except ValueError:
            counts["invalid"] += 1
            continue
        query_urls += bool(parsed.query)
        signed_query_urls += bool(query_keys & _SIGNATURE_KEYS)
        if host in _API_HOSTS and parsed.path.rstrip("/") == "/api/forbidden":
            counts["forbidden_endpoint"] += 1
        elif host in _API_HOSTS:
            counts["suno_api"] += 1
        elif host in _CDN_HOSTS:
            counts["suno_cdn"] += 1
        else:
            counts["other_http"] += 1

    return {
        "schema": "mindcap.suno-asset-diagnostics/v0.1",
        "offline": True,
        "clip_count": len(clips),
        "clips_with_archived_primary_audio": archived_audio,
        "audio_url_kinds": counts,
        "audio_urls_with_query": query_urls,
        "audio_urls_with_signature_keys": signed_query_urls,
    }
