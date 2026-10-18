from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from mindcap.core.errors import CaptureFailedError
from mindcap.plugins.suno.auth import SunoAuthState
from mindcap.plugins.suno.client import SunoClient
from mindcap.plugins.suno.errors import SunoApiError


def _encode_json(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _jwt_with_future_expiration() -> str:
    payload = {
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    header = _encode_json({"alg": "HS256", "typ": "JWT"})
    encoded_payload = _encode_json(payload)
    return f"{header}.{encoded_payload}.signature"


def _state() -> SunoAuthState:
    return SunoAuthState(
        clerk_client_cookie="clerk-cookie",
        cookie_header="__client=clerk-cookie; other=value",
        jwt=_jwt_with_future_expiration(),
        device_id="device-123",
    )


def test_headers_use_bearer_jwt_for_live_requests() -> None:
    state = _state()
    client = SunoClient(state=state)
    headers = client._headers()
    assert headers["authorization"] == f"{'Bearer'} {state.jwt}"


def test_safe_headers_and_errors_never_expose_cookie_or_jwt() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(
            500,
            request=request,
            headers={"content-type": "application/json"},
            content=b"{}",
        )

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))

    with pytest.raises(SunoApiError) as error:
        client.get_json("/api/workspaces/workspace-123", category="workspace")

    assert seen_headers["authorization"].startswith(f"{'Bearer'} ")
    assert seen_headers["cookie"] == "__client=clerk-cookie; other=value"

    safe_headers = client._safe_headers(client._headers())
    assert safe_headers["authorization"] == "<redacted>"
    assert safe_headers["cookie"] == "<redacted>"

    message = str(error.value)
    assert "<redacted>" in message
    assert seen_headers["authorization"] not in message
    assert "clerk-cookie" not in message


def test_get_workspace_probes_known_paths_until_one_succeeds() -> None:
    attempted_paths: list[str] = []
    workspace_payload: dict[str, Any] = {"id": "workspace-123", "title": "Workspace"}

    def handler(request: httpx.Request) -> httpx.Response:
        attempted_paths.append(request.url.path)
        if request.url.path == "/api/workspaces/workspace-123/":
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "application/json"},
                content=json.dumps(workspace_payload).encode("utf-8"),
            )
        return httpx.Response(
            404,
            request=request,
            headers={"content-type": "application/json"},
            content=b"{}",
        )

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    payload, _ = client.get_workspace("workspace-123")

    assert payload["id"] == "workspace-123"
    # Production Studio API paths are probed first; legacy paths follow.
    assert attempted_paths[:2] == [
        "/api/project/workspace-123",
        "/api/project/workspace-123/",
    ]
    assert "/api/workspaces/workspace-123/" in attempted_paths


def test_media_download_omits_api_credentials_and_cookie_jars() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        forbidden = {
            "authorization",
            "cookie",
            "x-browser-token",
            "x-device-id",
            "origin",
            "referer",
        }
        if forbidden & set(request.headers):
            return httpx.Response(403, content=b"not allowed")
        return httpx.Response(
            200, headers={"content-type": "audio/mpeg"}, content=b"audio"
        )

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    client._client.cookies.set("api-cookie", "secret", domain="suno.com")
    client._media_client.cookies.set("media-cookie", "secret", domain="suno.com")
    url = "https://cdn.suno.com/audio.mp3?signature=private-signature&expires=100"
    path, metadata = client.download_to_file(url)
    try:
        assert path.read_bytes() == b"audio"
        assert str(requests[0].url) == url
        assert metadata["source_url"] == "https://cdn.suno.com/audio.mp3"
        assert metadata["byte_size"] == 5
        assert metadata["media_type"] == "audio/mpeg"
        assert "private-signature" not in str(metadata)
    finally:
        shutil.rmtree(path.parent)


@pytest.mark.parametrize(
    "asset_url",
    [
        "https://studio-api-prod.suno.com/asset.mp3",
        "/asset.mp3",
    ],
)
def test_api_origin_asset_keeps_api_authentication(asset_url: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"audio")

    state = _state()
    client = SunoClient(state=state, transport=httpx.MockTransport(handler))
    path, _ = client.download_to_file(asset_url)
    try:
        assert requests[0].headers["authorization"] == f"Bearer {state.jwt}"
        assert requests[0].headers["cookie"] == state.cookie_header
        assert "x-browser-token" in requests[0].headers
    finally:
        shutil.rmtree(path.parent)


@pytest.mark.parametrize(
    "asset_url",
    [
        "https://studio-api-prod.suno.com:444/asset.mp3",
        "http://studio-api-prod.suno.com/asset.mp3",
        "https://studio-api-prod.suno.com.evil.test/asset.mp3",
    ],
)
def test_similar_asset_origin_never_gets_api_authentication(asset_url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "x-browser-token" not in request.headers
        return httpx.Response(200, content=b"audio")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    path, _ = client.download_to_file(asset_url)
    shutil.rmtree(path.parent)


def test_media_redirect_drops_auth_and_never_restores_it() -> None:
    requests: list[httpx.Request] = []
    urls = [
        "https://studio-api-prod.suno.com/asset.mp3",
        "https://cdn.suno.com/asset.mp3?signature=secret",
        "https://studio-api-prod.suno.com/final.mp3",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(
                302,
                headers={
                    "location": urls[len(requests)],
                    "set-cookie": "redirect-cookie=secret; Domain=.suno.com; Path=/",
                },
            )
        return httpx.Response(200, content=b"audio")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    path, _ = client.download_to_file(urls[0])
    try:
        assert [str(request.url) for request in requests] == urls
        assert "authorization" in requests[0].headers
        for request in requests[1:]:
            assert "authorization" not in request.headers
            assert "cookie" not in request.headers
            assert "x-browser-token" not in request.headers
            assert "x-device-id" not in request.headers
    finally:
        shutil.rmtree(path.parent)


def test_media_403_is_actionable_and_removes_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary = tmp_path / "download"
    temporary.mkdir()
    monkeypatch.setattr(
        "mindcap.plugins.suno.client.tempfile.mkdtemp", lambda **kwargs: str(temporary)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"body containing private-provider-data")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    with pytest.raises(CaptureFailedError) as error:
        client.download_to_file("https://cdn.suno.com/audio.mp3?signature=private")
    message = str(error.value)
    assert "403" in message
    assert "Download works" in message
    assert "private" not in message
    assert "cdn.suno.com" not in message
    assert not temporary.exists()


@pytest.mark.parametrize("failure", ["empty", "stream", "redirect-loop"])
def test_failed_media_transfer_removes_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    temporary = tmp_path / "download"
    temporary.mkdir()
    monkeypatch.setattr(
        "mindcap.plugins.suno.client.tempfile.mkdtemp", lambda **kwargs: str(temporary)
    )

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"partial audio"
            raise httpx.ReadError("private-provider-data")

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "stream":
            return httpx.Response(200, stream=BrokenStream())
        if failure == "redirect-loop":
            return httpx.Response(302, headers={"location": "/audio.mp3"})
        return httpx.Response(200, content=b"")

    client = SunoClient(state=_state(), transport=httpx.MockTransport(handler))
    with pytest.raises(CaptureFailedError) as error:
        client.download_to_file("https://cdn.suno.com/audio.mp3?signature=secret")
    assert "private-provider-data" not in str(error.value)
    assert "signature=secret" not in str(error.value)
    assert not temporary.exists()
