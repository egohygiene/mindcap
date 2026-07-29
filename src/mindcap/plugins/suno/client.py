from __future__ import annotations

import base64
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from mindcap.config import suno_api_origin
from mindcap.core.errors import AuthenticationRequiredError, CaptureFailedError
from mindcap.core.models import RawResponseUnit
from mindcap.plugins.suno.auth import (
    SunoAuthState,
    has_refreshable_clerk_state,
    jwt_state,
    load_suno_auth_state,
    redact_signed_url,
    save_suno_auth_state,
)
from mindcap.plugins.suno.errors import SunoApiError

_CLERK_API_VERSION = "2025-11-10"
_CLERK_JS_VERSION = "5.117.0"
_CLERK_BASE = "https://auth.suno.com"


@dataclass(frozen=True)
class SunoResponseRecord:
    category: str
    url: str
    body: bytes
    media_type: str
    retrieved_at: datetime
    safe_metadata: dict[str, Any]

    def to_raw_response_unit(self, unit_id: str, sequence: int) -> RawResponseUnit:
        return RawResponseUnit(
            unit_id=unit_id,
            sequence=sequence,
            media_type=self.media_type,
            body=self.body,
            source_url=self.url,
            endpoint_category=self.category,
            retrieved_at=self.retrieved_at,
            safe_metadata=self.safe_metadata,
        )


class SunoClient:
    def __init__(
        self,
        *,
        api_origin: str | None = None,
        state: SunoAuthState | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_origin = (api_origin or suno_api_origin()).rstrip("/")
        loaded_state = state or load_suno_auth_state(required=True)
        if loaded_state is None:
            raise AuthenticationRequiredError(
                "Run `mindcap auth suno --cookie-stdin` first."
            )
        self.state = loaded_state
        self._client = httpx.Client(
            base_url=self.api_origin,
            follow_redirects=True,
            timeout=httpx.Timeout(20.0, connect=10.0, read=20.0, write=20.0),
            transport=transport,
        )

    def _browser_token(self) -> str:
        payload = json.dumps({"timestamp": int(datetime.now(UTC).timestamp() * 1000)})
        return json.dumps(
            {"token": base64.b64encode(payload.encode("utf-8")).decode("ascii")}
        )

    def _headers(self) -> dict[str, str]:
        if not self.state.clerk_client_cookie:
            raise AuthenticationRequiredError(
                "Run `mindcap auth suno --cookie-stdin` first."
            )
        headers = {
            "accept": "application/json",
            "origin": self.api_origin,
            "referer": f"{self.api_origin}/",
            "user-agent": "mindcap/0.1.0",
            "x-requested-with": "XMLHttpRequest",
            "x-device-id": self.state.device_id or "unknown-device",
            "x-browser-token": self._browser_token(),
            "cookie": self.state.cookie_header
            or f"__client={self.state.clerk_client_cookie}",
            "authorization": " ".join(("Bearer", self.state.jwt or "")),
        }
        if not self.state.jwt:
            headers.pop("authorization")
        return headers

    def _safe_headers(self, headers: dict[str, str]) -> dict[str, str]:
        safe_headers = dict(headers)
        for header in ("authorization", "cookie", "x-browser-token"):
            if header in safe_headers:
                safe_headers[header] = "<redacted>"
        return safe_headers

    def _clerk_headers(self) -> dict[str, str]:
        if not self.state.clerk_client_cookie:
            raise AuthenticationRequiredError(
                "Stored Suno auth state is missing the Clerk cookie."
            )
        return {
            "authorization": self.state.clerk_client_cookie,
            "cookie": f"__client={self.state.clerk_client_cookie}",
            "origin": self.api_origin,
            "referer": f"{self.api_origin}/",
        }

    def _refresh_session_id(self) -> None:
        response = self._client.get(
            f"{_CLERK_BASE}/v1/client",
            params={
                "__clerk_api_version": _CLERK_API_VERSION,
                "_clerk_js_version": _CLERK_JS_VERSION,
            },
            headers=self._clerk_headers(),
        )
        if response.status_code >= 400:
            raise AuthenticationRequiredError(
                f"Clerk session discovery failed with status {response.status_code}."
            )
        payload = response.json()
        response_payload = (
            payload.get("response") if isinstance(payload, dict) else None
        )
        if not isinstance(response_payload, dict):
            raise AuthenticationRequiredError(
                "Clerk session discovery returned an unexpected payload."
            )
        session_id = response_payload.get("last_active_session_id")
        if not session_id:
            sessions = response_payload.get("sessions")
            if isinstance(sessions, list) and sessions:
                first = sessions[0]
                if isinstance(first, dict):
                    session_id = first.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise AuthenticationRequiredError(
                "No active Suno session was found for the stored Clerk cookie."
            )
        self.state.session_id = session_id

    def refresh_jwt(self) -> None:
        if not has_refreshable_clerk_state(self.state):
            raise AuthenticationRequiredError(
                "Stored Suno auth state cannot refresh JWTs."
            )
        if not self.state.session_id:
            self._refresh_session_id()
        response = self._client.post(
            f"{_CLERK_BASE}/v1/client/sessions/{self.state.session_id}/tokens",
            params={
                "__clerk_api_version": _CLERK_API_VERSION,
                "_clerk_js_version": _CLERK_JS_VERSION,
            },
            headers=self._clerk_headers(),
        )
        if response.status_code >= 400:
            raise AuthenticationRequiredError(
                f"Clerk JWT refresh failed with status {response.status_code}."
            )
        payload = response.json()
        jwt = payload.get("jwt") if isinstance(payload, dict) else None
        if not isinstance(jwt, str) or not jwt:
            raise AuthenticationRequiredError("Clerk JWT refresh returned no token.")
        self.state.jwt = jwt
        self.state.jwt_expires_at = None
        save_suno_auth_state(self.state)

    def ensure_jwt(self) -> None:
        current_state, _ = jwt_state(self.state)
        if current_state in {"missing", "stale", "expired"}:
            self.refresh_jwt()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: object | None = None,
        expected_statuses: set[int] | None = None,
        retry_on_auth: bool = True,
    ) -> httpx.Response:
        self.ensure_jwt()
        headers = self._headers()
        response = self._client.request(
            method,
            path,
            params=params,
            json=json_body,
            headers=headers,
        )
        if (
            response.status_code in {401, 403}
            and retry_on_auth
            and has_refreshable_clerk_state(self.state)
        ):
            self.refresh_jwt()
            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                expected_statuses=expected_statuses,
                retry_on_auth=False,
            )
        if expected_statuses and response.status_code in expected_statuses:
            return response
        if response.status_code >= 400:
            raise SunoApiError(
                f"Suno API request failed ({response.status_code}) for {path} "
                f"with safe_headers={self._safe_headers(headers)}."
            )
        return response

    def get_json(
        self,
        path: str,
        *,
        category: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], SunoResponseRecord]:
        response = self._request("GET", path, params=params)
        body = response.content
        payload = response.json()
        if not isinstance(payload, dict):
            raise SunoApiError(f"Suno API returned a non-object payload for {path}.")
        return payload, SunoResponseRecord(
            category=category,
            url=redact_signed_url(str(response.request.url))
            or str(response.request.url),
            body=body,
            media_type=response.headers.get("content-type", "application/json"),
            retrieved_at=datetime.now(UTC),
            safe_metadata={"status_code": response.status_code},
        )

    def try_get_json(
        self,
        path: str,
        *,
        category: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], SunoResponseRecord] | None:
        response = self._request("GET", path, params=params, expected_statuses={404})
        if response.status_code == 404:
            return None
        body = response.content
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        return payload, SunoResponseRecord(
            category=category,
            url=redact_signed_url(str(response.request.url))
            or str(response.request.url),
            body=body,
            media_type=response.headers.get("content-type", "application/json"),
            retrieved_at=datetime.now(UTC),
            safe_metadata={"status_code": response.status_code},
        )

    def post_json(
        self,
        path: str,
        payload: object,
        *,
        category: str,
    ) -> tuple[dict[str, Any], SunoResponseRecord]:
        response = self._request("POST", path, json_body=payload)
        body = response.content
        data = response.json()
        if not isinstance(data, dict):
            raise SunoApiError(f"Suno API returned a non-object payload for {path}.")
        return data, SunoResponseRecord(
            category=category,
            url=redact_signed_url(str(response.request.url))
            or str(response.request.url),
            body=body,
            media_type=response.headers.get("content-type", "application/json"),
            retrieved_at=datetime.now(UTC),
            safe_metadata={"status_code": response.status_code},
        )

    def billing_info(self) -> dict[str, Any]:
        payload, _ = self.get_json("/api/billing/info/", category="billing")
        return payload

    def get_workspace(
        self, workspace_id: str
    ) -> tuple[dict[str, Any], SunoResponseRecord]:
        # Production Studio API is the primary candidate; legacy paths are fallbacks.
        for path in (
            f"/api/project/{workspace_id}",
            f"/api/project/{workspace_id}/",
            f"/api/projects/{workspace_id}",
            f"/api/workspaces/{workspace_id}",
            f"/api/workspaces/{workspace_id}/",
            f"/api/workspace/{workspace_id}",
        ):
            result = self.try_get_json(path, category="workspace")
            if result is not None:
                return result
        raise CaptureFailedError(
            "Could not resolve the Suno workspace through known API routes."
        )

    def get_project_pages(
        self,
        project_id: str,
    ) -> list[tuple[dict[str, Any], SunoResponseRecord]]:
        """Fetch all pages of a project, accumulating clips until complete.

        Uses ``clip_count`` from the first response to know when to stop.
        Detects and breaks out of repeated pages or empty pages to prevent
        infinite loops.
        """
        pages: list[tuple[dict[str, Any], SunoResponseRecord]] = []
        seen_page_numbers: set[int] = set()
        accumulated_clip_ids: set[str] = set()
        expected_count: int | None = None
        page_num = 1

        while True:
            params = {"page": page_num} if page_num > 1 else None
            result = self.try_get_json(
                f"/api/project/{project_id}",
                category="project-page",
                params=params,
            )
            if result is None:
                break

            payload, record = result
            pages.append((payload, record))

            if expected_count is None:
                raw = payload.get("clip_count")
                if isinstance(raw, int):
                    expected_count = raw

            current_page = payload.get("current_page")
            if isinstance(current_page, int):
                if current_page in seen_page_numbers:
                    break
                seen_page_numbers.add(current_page)

            project_clips = payload.get("project_clips")
            if not isinstance(project_clips, list) or not project_clips:
                break

            for entry in project_clips:
                if isinstance(entry, dict):
                    clip = entry.get("clip")
                    if isinstance(clip, dict) and clip.get("id"):
                        accumulated_clip_ids.add(str(clip["id"]))

            if (
                expected_count is not None
                and len(accumulated_clip_ids) >= expected_count
            ):
                break

            page_num += 1

        return pages

    def list_workspace_clips(
        self,
        workspace_id: str,
    ) -> list[tuple[dict[str, Any], SunoResponseRecord]]:
        pages: list[tuple[dict[str, Any], SunoResponseRecord]] = []
        for path in (
            f"/api/workspaces/{workspace_id}/clips",
            f"/api/workspaces/{workspace_id}/clips/",
            f"/api/workspace/{workspace_id}/clips",
        ):
            result = self.try_get_json(path, category="clips-page")
            if result is not None:
                pages.append(result)
                return pages
        return pages

    def get_clip_detail(
        self, clip_id: str
    ) -> tuple[dict[str, Any], SunoResponseRecord] | None:
        for path in (
            f"/api/gen/{clip_id}",
            f"/api/clips/{clip_id}",
            f"/api/clips/{clip_id}/",
            f"/api/clip/{clip_id}",
        ):
            result = self.try_get_json(path, category="clip")
            if result is not None:
                return result
        return None

    def get_lyrics(
        self, clip_id: str
    ) -> tuple[dict[str, Any], SunoResponseRecord] | None:
        for path in (
            f"/api/gen/{clip_id}/lyrics/",
            f"/api/gen/{clip_id}/lyrics",
            f"/api/clips/{clip_id}/lyrics",
        ):
            result = self.try_get_json(path, category="lyrics")
            if result is not None:
                return result
        return None

    def get_aligned_lyrics(
        self,
        clip_id: str,
    ) -> tuple[dict[str, Any], SunoResponseRecord] | None:
        return self.try_get_json(
            f"/api/gen/{clip_id}/aligned_lyrics/v2/",
            category="aligned-lyrics",
        )

    def download_to_file(self, url: str) -> tuple[Path, dict[str, Any]]:
        suffix = Path(redact_signed_url(url) or url).suffix or ".bin"
        temp_dir = Path(tempfile.mkdtemp(prefix="mindcap-suno-", dir="/tmp"))
        destination = temp_dir / f"asset{suffix}"
        part_path = destination.with_suffix(destination.suffix + ".part")
        with self._client.stream("GET", url, headers=self._headers()) as response:
            if response.status_code >= 400:
                raise CaptureFailedError(
                    f"Failed to download Suno asset ({response.status_code})."
                )
            with part_path.open("wb") as handle:
                total = 0
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    total += len(chunk)
        part_path.replace(destination)
        return destination, {
            "byte_size": destination.stat().st_size,
            "media_type": response.headers.get(
                "content-type", "application/octet-stream"
            ),
            "downloaded_at": datetime.now(UTC).isoformat(),
            "source_url": redact_signed_url(url),
        }
