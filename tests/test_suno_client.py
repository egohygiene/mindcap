from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

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
    assert attempted_paths[:2] == [
        "/api/workspaces/workspace-123",
        "/api/workspaces/workspace-123/",
    ]
