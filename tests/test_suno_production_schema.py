"""Tests covering the production Studio API schema for the Suno provider.

These tests exercise the updated normalizer, client pagination, completeness
validation, and regression scenarios called out in issue #6.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from mindcap.config import suno_api_origin
from mindcap.core.models import CaptureEnvelope, RawResponseUnit
from mindcap.plugins.suno.archive.service import (
    SunoWorkspaceCaptureService,
    _extract_clips_from_project_response,
)
from mindcap.plugins.suno.auth import SunoAuthState
from mindcap.plugins.suno.client import SunoClient
from mindcap.plugins.suno.normalizer import _coerce_tags, normalize_suno

PROJECT_FIXTURE = Path(__file__).parent / "fixtures" / "suno" / "project.json"
PROJECT_ID = "proj-a1b2c3d4-0000-0000-0000-000000000001"
_CAPTURED_AT = datetime(2025, 3, 15, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state() -> SunoAuthState:
    import base64
    from datetime import timedelta

    def _encode_json(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    payload = {
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    header = _encode_json({"alg": "HS256", "typ": "JWT"})
    encoded_payload = _encode_json(payload)
    jwt = f"{header}.{encoded_payload}.signature"
    return SunoAuthState(
        clerk_client_cookie="clerk-cookie",
        cookie_header="__client=clerk-cookie",
        jwt=jwt,
        device_id="device-test",
    )


def _envelope_from_project_fixture() -> CaptureEnvelope:
    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))
    return CaptureEnvelope(
        provider="suno",
        source_type="workspace",
        canonical_identifier=PROJECT_ID,
        canonical_url=f"https://studio-api-prod.suno.com/project/{PROJECT_ID}",
        captured_at=_CAPTURED_AT,
        strategy="api",
        response_units=[
            RawResponseUnit(
                unit_id="workspace-000",
                sequence=0,
                media_type="application/json",
                body=json.dumps(project).encode("utf-8"),
                source_url=f"https://studio-api-prod.suno.com/api/project/{PROJECT_ID}",
                endpoint_category="workspace",
                retrieved_at=_CAPTURED_AT,
            )
        ],
        assets=[],
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_default_api_origin_is_production_studio() -> None:
    import os

    os.environ.pop("MINDCAP_SUNO_API_ORIGIN", None)
    assert suno_api_origin() == "https://studio-api-prod.suno.com"


# ---------------------------------------------------------------------------
# _coerce_tags
# ---------------------------------------------------------------------------


def test_coerce_tags_from_string() -> None:
    assert _coerce_tags("dream pop shimmering") == ["dream", "pop", "shimmering"]


def test_coerce_tags_from_comma_string() -> None:
    assert _coerce_tags("dream pop, shimmering") == ["dream", "pop", "shimmering"]


def test_coerce_tags_from_list() -> None:
    assert _coerce_tags(["dream pop", "shimmering"]) == ["dream pop", "shimmering"]


def test_coerce_tags_empty_string() -> None:
    assert _coerce_tags("") == []


def test_coerce_tags_none() -> None:
    assert _coerce_tags(None) == []


# ---------------------------------------------------------------------------
# Production project response structure
# ---------------------------------------------------------------------------


def test_extract_clips_from_project_clips_structure() -> None:
    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))
    clips = _extract_clips_from_project_response(project)

    assert len(clips) == 3
    assert "clip-prod-001" in clips
    assert "clip-prod-002" in clips
    assert "clip-prod-003" in clips


def test_project_clip_meta_preserved() -> None:
    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))
    clips = _extract_clips_from_project_response(project)

    meta_001 = clips["clip-prod-001"]["_project_clip"]
    assert meta_001["relative_index"] == 0
    assert meta_001["pinned"] is False
    assert meta_001["batch_index"] == 0

    meta_003 = clips["clip-prod-003"]["_project_clip"]
    assert meta_003["pinned"] is True
    assert meta_003["batch_index"] == 1


def test_legacy_flat_clips_also_extracted() -> None:
    payload = {
        "clips": [
            {"id": "legacy-001", "title": "Old Clip"},
        ]
    }
    clips = _extract_clips_from_project_response(payload)
    assert "legacy-001" in clips
    assert clips["legacy-001"]["title"] == "Old Clip"


def test_production_response_normalized_correctly() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    assert result["schema"] == "mindcap.suno-workspace/v0.3"
    assert result["workspace_id"] == PROJECT_ID
    assert result["title"] == "Synthetic Production Project"
    assert len(result["clips"]) == 3


def test_project_metadata_block_present() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    meta = result["project_metadata"]
    assert meta["clip_count"] == 3
    assert meta["is_owned"] is True
    assert meta["is_trashed"] is False


# ---------------------------------------------------------------------------
# Empty prompt with populated tags
# ---------------------------------------------------------------------------


def test_empty_prompt_preserved_when_tags_populated() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    # Prompt is explicitly empty string in the fixture
    assert clip["prompts"]["prompt"] == ""
    # Tags carry the real creative intent
    assert (
        clip["prompts"]["raw_style_prompt"] == "dream pop shimmering guitars hopeful"
    )
    assert "tags_list" not in clip["prompts"]


# ---------------------------------------------------------------------------
# Multiple media formats
# ---------------------------------------------------------------------------


def test_multiple_media_url_variants_preserved() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    media_urls = clip["media"]["media_urls"]
    assert len(media_urls) == 2
    encodings = {m["encoding"] for m in media_urls}
    assert "mp3" in encodings
    assert "m4a-opus" in encodings


# ---------------------------------------------------------------------------
# Unknown slider names
# ---------------------------------------------------------------------------


def test_unknown_slider_names_preserved() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-002")
    sliders = clip["sliders"]
    assert "audio_weight" in sliders
    # Unknown slider from fixture should be preserved
    assert "new_experimental_slider" in sliders
    assert sliders["new_experimental_slider"] == 0.9


def test_control_sliders_key_is_supported() -> None:
    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))
    project["project_clips"][0]["clip"]["metadata"]["control_sliders"] = {
        "depth": 0.7,
        "brightness": 0.1,
    }
    project["project_clips"][0]["clip"]["metadata"]["sliders"] = {}
    envelope = CaptureEnvelope(
        provider="suno",
        source_type="workspace",
        canonical_identifier=PROJECT_ID,
        canonical_url=None,
        captured_at=_CAPTURED_AT,
        strategy="api",
        response_units=[
            RawResponseUnit(
                unit_id="workspace-000",
                sequence=0,
                media_type="application/json",
                body=json.dumps(project).encode("utf-8"),
                endpoint_category="workspace",
                retrieved_at=_CAPTURED_AT,
            )
        ],
    )
    result = normalize_suno(envelope, PROJECT_ID)
    clip = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    assert clip["sliders"]["depth"] == 0.7


# ---------------------------------------------------------------------------
# Unknown metadata keys
# ---------------------------------------------------------------------------


def test_unknown_metadata_keys_preserved_via_provider_metadata() -> None:
    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))
    # Inject an unknown key into the first clip's metadata
    project["project_clips"][0]["clip"]["metadata"]["future_field"] = "mystery_value"
    project["project_clips"][0]["clip"]["unknown_top_level_key"] = "top_value"

    body = json.dumps(project).encode("utf-8")
    envelope = CaptureEnvelope(
        provider="suno",
        source_type="workspace",
        canonical_identifier=PROJECT_ID,
        canonical_url=None,
        captured_at=_CAPTURED_AT,
        strategy="api",
        response_units=[
            RawResponseUnit(
                unit_id="workspace-000",
                sequence=0,
                media_type="application/json",
                body=body,
                endpoint_category="workspace",
                retrieved_at=_CAPTURED_AT,
            )
        ],
    )
    result = normalize_suno(envelope, PROJECT_ID)
    clip = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    # provider_metadata is the raw clip dict, must retain unknown keys
    assert clip["provider_metadata"]["metadata"]["future_field"] == "mystery_value"
    assert clip["provider_metadata"]["unknown_top_level_key"] == "top_value"


# ---------------------------------------------------------------------------
# Action configuration preservation
# ---------------------------------------------------------------------------


def test_action_config_preserved() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip001 = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    assert clip001["action_config"]["can_remix"] is True
    assert "share" in clip001["action_config"]["actions"]

    clip002 = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-002")
    # future_action is an unknown key that must be retained
    assert clip002["action_config"]["future_action"] == "pending_review"


def test_empty_action_config_preserved() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip003 = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-003")
    assert clip003["action_config"] == {}


# ---------------------------------------------------------------------------
# Remix lineage
# ---------------------------------------------------------------------------


def test_remix_lineage_preserved() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip002 = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-002")
    assert clip002["remix_lineage"]["edited_clip_id"] == "clip-prod-001"

    clip003 = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-003")
    assert clip003["remix_lineage"]["cover_clip_id"] == "clip-prod-001"


def test_no_lineage_clip_has_empty_dict() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip001 = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    assert clip001["remix_lineage"] == {}


# ---------------------------------------------------------------------------
# Reactions and ownership
# ---------------------------------------------------------------------------


def test_reactions_not_flattened() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    reactions = clip["reactions"]
    assert reactions["play_count"] == 42
    assert reactions["skip_count"] == 3
    assert reactions["flagged"] is False


def test_ownership_not_flattened() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    ownership = clip["ownership"]
    assert ownership["is_owner"] is True
    assert ownership["can_edit"] is True


# ---------------------------------------------------------------------------
# Generation flags
# ---------------------------------------------------------------------------


def test_instrumental_flag_preserved() -> None:
    envelope = _envelope_from_project_fixture()
    result = normalize_suno(envelope, PROJECT_ID)

    clip003 = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-003")
    assert clip003["generation_flags"]["make_instrumental"] is True
    assert clip003["generation_flags"]["has_vocal"] is False
    assert clip003["generation_flags"]["has_stem"] is True


def test_model_uses_latest_falls_back_to_metadata() -> None:
    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))
    clip = project["project_clips"][0]["clip"]
    clip["uses_latest_model"] = None
    clip["metadata"]["uses_latest_model"] = True
    envelope = CaptureEnvelope(
        provider="suno",
        source_type="workspace",
        canonical_identifier=PROJECT_ID,
        canonical_url=None,
        captured_at=_CAPTURED_AT,
        strategy="api",
        response_units=[
            RawResponseUnit(
                unit_id="workspace-000",
                sequence=0,
                media_type="application/json",
                body=json.dumps(project).encode("utf-8"),
                endpoint_category="workspace",
                retrieved_at=_CAPTURED_AT,
            )
        ],
    )
    result = normalize_suno(envelope, PROJECT_ID)
    normalized = next(c for c in result["clips"] if c["clip_id"] == "clip-prod-001")
    assert normalized["model"]["uses_latest"] is True


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def _make_page_response(
    project_id: str,
    page_num: int,
    clips: list[dict[str, Any]],
    clip_count: int,
) -> dict[str, Any]:
    return {
        "id": project_id,
        "name": "Paginated Project",
        "clip_count": clip_count,
        "current_page": page_num,
        "project_clips": [
            {"relative_index": i, "pinned": False, "batch_index": 0, "clip": clip}
            for i, clip in enumerate(clips)
        ],
    }


def _make_clip(clip_id: str) -> dict[str, Any]:
    return {
        "id": clip_id,
        "status": "complete",
        "metadata": {"prompt": "test", "tags": "test"},
    }


def test_client_fetches_all_pages_until_clip_count_reached() -> None:
    page1_clips = [_make_clip("c1"), _make_clip("c2")]
    page2_clips = [_make_clip("c3")]
    page1 = _make_page_response("proj-page", 1, page1_clips, clip_count=3)
    page2 = _make_page_response("proj-page", 2, page2_clips, clip_count=3)

    call_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append(str(request.url))
        path = request.url.path
        page = request.url.params.get("page", "1")
        if path == "/api/project/proj-page":
            if page == "1" or page is None:
                return httpx.Response(
                    200,
                    request=request,
                    headers={"content-type": "application/json"},
                    content=json.dumps(page1).encode(),
                )
            if page == "2":
                return httpx.Response(
                    200,
                    request=request,
                    headers={"content-type": "application/json"},
                    content=json.dumps(page2).encode(),
                )
        return httpx.Response(404, request=request, content=b"{}")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    pages = client.get_project_pages("proj-page")

    assert len(pages) == 2
    all_clip_ids: set[str] = set()
    for payload, _ in pages:
        for entry in payload.get("project_clips", []):
            all_clip_ids.add(entry["clip"]["id"])
    assert all_clip_ids == {"c1", "c2", "c3"}


def test_pagination_stops_on_repeated_page_number() -> None:
    # If the API keeps returning the same current_page, stop early.
    page = _make_page_response("proj-repeat", 1, [_make_clip("c1")], clip_count=99)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/project/proj-repeat" in request.url.path:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                content=json.dumps(page).encode(),
            )
        return httpx.Response(404, request=request, content=b"{}")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    pages = client.get_project_pages("proj-repeat")
    # Should stop after the first page because current_page repeats on page 2 request.
    assert len(pages) == 1


def test_pagination_stops_on_empty_project_clips() -> None:
    page = {
        "id": "proj-empty",
        "clip_count": 0,
        "current_page": 1,
        "project_clips": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/project/proj-empty" in request.url.path:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                content=json.dumps(page).encode(),
            )
        return httpx.Response(404, request=request, content=b"{}")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    pages = client.get_project_pages("proj-empty")
    assert len(pages) == 1


# ---------------------------------------------------------------------------
# Duplicate clip IDs
# ---------------------------------------------------------------------------


def test_duplicate_clip_ids_are_deduplicated() -> None:
    clip = {
        "id": "dup-clip",
        "title": "Version A",
        "metadata": {"prompt": "original"},
    }
    clip_v2 = {
        "id": "dup-clip",
        "title": "Version B",
        "metadata": {"prompt": "updated"},
    }
    payload_with_duplicates = {
        "project_clips": [
            {"relative_index": 0, "pinned": False, "batch_index": 0, "clip": clip},
            {"relative_index": 1, "pinned": False, "batch_index": 0, "clip": clip_v2},
        ]
    }
    clips = _extract_clips_from_project_response(payload_with_duplicates)
    # Only one entry for the duplicated ID; last write wins.
    assert len(clips) == 1
    assert clips["dup-clip"]["title"] == "Version B"


def test_duplicate_clip_ids_across_pages_are_deduplicated_in_normalizer() -> None:
    clip_page1 = {
        "id": "shared-clip",
        "title": "From page 1",
        "metadata": {"prompt": "p1"},
    }
    clip_page2 = {
        "id": "shared-clip",
        "title": "From page 2",
        "metadata": {"prompt": "p2"},
    }
    page1 = {
        "id": "proj-dedup",
        "clip_count": 1,
        "current_page": 1,
        "project_clips": [
            {"relative_index": 0, "pinned": False, "batch_index": 0, "clip": clip_page1}
        ],
    }
    page2 = {
        "id": "proj-dedup",
        "clip_count": 1,
        "current_page": 2,
        "project_clips": [
            {"relative_index": 0, "pinned": False, "batch_index": 0, "clip": clip_page2}
        ],
    }
    envelope = CaptureEnvelope(
        provider="suno",
        source_type="workspace",
        canonical_identifier="proj-dedup",
        canonical_url=None,
        captured_at=_CAPTURED_AT,
        strategy="api",
        response_units=[
            RawResponseUnit(
                unit_id="workspace-000",
                sequence=0,
                media_type="application/json",
                body=json.dumps(page1).encode(),
                endpoint_category="workspace",
                retrieved_at=_CAPTURED_AT,
            ),
            RawResponseUnit(
                unit_id="project-page-002",
                sequence=1,
                media_type="application/json",
                body=json.dumps(page2).encode(),
                endpoint_category="project-page",
                retrieved_at=_CAPTURED_AT,
            ),
        ],
    )
    result = normalize_suno(envelope, "proj-dedup")
    assert len(result["clips"]) == 1


# ---------------------------------------------------------------------------
# Incomplete archive detection
# ---------------------------------------------------------------------------


def test_incomplete_capture_detected_by_service(tmp_path: Path) -> None:
    from mindcap.core.models import CaptureRequest
    from mindcap.plugins.suno.archive.downloader import SunoAssetDownloader

    # Build a project response that claims 3 clips but only contains 1.
    project = {
        "id": "proj-incomplete",
        "name": "Incomplete Project",
        "clip_count": 3,
        "current_page": 1,
        "project_clips": [
            {
                "relative_index": 0,
                "pinned": False,
                "batch_index": 0,
                "clip": {
                    "id": "only-clip",
                    "status": "complete",
                    "metadata": {"prompt": "solo"},
                },
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/project/proj-incomplete" in request.url.path:
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                content=json.dumps(project).encode(),
            )
        return httpx.Response(404, request=request, content=b"{}")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    downloader = SunoAssetDownloader(client)
    service = SunoWorkspaceCaptureService(client=client, downloader=downloader)

    request = CaptureRequest(
        source_type="suno",
        source="proj-incomplete",
        provider="suno",
        canonical_identifier="proj-incomplete",
        canonical_url=None,
        strategy="api",
        artifact_root=tmp_path,
    )
    envelope = service.capture(request)

    assert envelope.safe_metadata["capture_complete"] is False
    assert envelope.safe_metadata["expected_clip_count"] == 3
    assert envelope.safe_metadata["clip_count"] == 1
    assert any("Incomplete capture" in w for w in envelope.warnings)


def test_complete_capture_has_no_incomplete_warning(tmp_path: Path) -> None:
    from mindcap.core.models import CaptureRequest
    from mindcap.plugins.suno.archive.downloader import SunoAssetDownloader

    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/api/project/{PROJECT_ID}":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                content=json.dumps(project).encode(),
            )
        return httpx.Response(404, request=request, content=b"{}")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    downloader = SunoAssetDownloader(client)
    service = SunoWorkspaceCaptureService(client=client, downloader=downloader)

    request = CaptureRequest(
        source_type="suno",
        source=PROJECT_ID,
        provider="suno",
        canonical_identifier=PROJECT_ID,
        canonical_url=None,
        strategy="api",
        artifact_root=tmp_path,
    )
    envelope = service.capture(request)

    assert envelope.safe_metadata["capture_complete"] is True
    assert not any("Incomplete capture" in w for w in envelope.warnings)


# ---------------------------------------------------------------------------
# Client: production endpoint is tried first
# ---------------------------------------------------------------------------


def test_client_get_workspace_tries_project_endpoint_first() -> None:
    attempted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.path)
        if request.url.path == f"/api/project/{PROJECT_ID}":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                content=json.dumps({"id": PROJECT_ID, "name": "Test"}).encode(),
            )
        return httpx.Response(404, request=request, content=b"{}")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    payload, _ = client.get_workspace(PROJECT_ID)

    assert payload["id"] == PROJECT_ID
    assert attempted[0] == f"/api/project/{PROJECT_ID}"
