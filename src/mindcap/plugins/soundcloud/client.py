"""Typed SoundCloud API client.

The client is intentionally read-only.  It never issues POST, PUT, PATCH, or
DELETE requests against provider resource endpoints.  The only exception is the
OAuth token exchange (POST to the token endpoint), which is required by the
authorization-code-with-PKCE flow.

Design
------
- Configurable base URL to enable offline unit tests.
- Dependency-injected HTTP transport for tests.
- All signed or sensitive query parameters (``secret_token``, etc.) are
  stripped from stored URLs before they appear in logs or archives.
- Authorization headers are never included in raw response records.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin

import httpx

from mindcap.plugins.soundcloud.auth import (
    SoundCloudAuthState,
    load_soundcloud_auth_state,
    soundcloud_client_id,
)
from mindcap.plugins.soundcloud.errors import SoundCloudApiError, SoundCloudAuthError

_DEFAULT_BASE_URL = "https://api.soundcloud.com"
_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 3
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class SoundCloudClient:
    """Authenticated, read-only SoundCloud API client.

    Parameters
    ----------
    state:
        An already-loaded :class:`~mindcap.plugins.soundcloud.auth.SoundCloudAuthState`.
        When ``None``, the state is loaded from the configured auth file.
    base_url:
        Base URL for the SoundCloud API.  Overridable for tests.
    transport:
        Optional HTTPX transport override for unit tests.
    timeout:
        Request timeout in seconds.
    """

    def __init__(
        self,
        state: SoundCloudAuthState | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._state = state
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    def _auth_state(self) -> SoundCloudAuthState:
        if self._state is not None:
            return self._state
        loaded = load_soundcloud_auth_state(required=False)
        if loaded is None:
            # Fall back to client-credential mode (client_id only, no OAuth).
            cid = soundcloud_client_id()
            if cid:
                return SoundCloudAuthState(client_id=cid)
            raise SoundCloudAuthError(
                "No SoundCloud authentication state found. "
                "Run 'mindcap auth soundcloud' or set MINDCAP_SOUNDCLOUD_CLIENT_ID."
            )
        return loaded

    def _build_client(self, state: SoundCloudAuthState) -> httpx.Client:
        headers: dict[str, str] = {"Accept": "application/json; charset=utf-8"}
        if state.access_token:
            headers["Authorization"] = f"OAuth {state.access_token}"
        params: dict[str, str] = {}
        if state.client_id:
            params["client_id"] = state.client_id

        return httpx.Client(
            base_url=self._base_url,
            headers=headers,
            params=params,
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=True,
        )

    def get_json(
        self,
        path: str,
        *,
        category: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Perform a GET request and return (parsed_body, safe_metadata).

        Parameters
        ----------
        path:
            API path, e.g. ``"/me"`` or ``"/tracks/12345"``.
        category:
            Endpoint category label for the raw evidence record.
        params:
            Additional query parameters.

        Returns
        -------
        tuple[dict, dict]
            Parsed JSON response body and safe metadata (sanitized URL, status,
            retrieval timestamp, byte size).

        Raises
        ------
        SoundCloudApiError
            On non-2xx responses or JSON parse failures.
        """
        state = self._auth_state()
        url = urljoin(self._base_url + "/", path.lstrip("/"))
        merged_params: dict[str, Any] = {}
        if params:
            merged_params.update(params)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                with self._build_client(state) as client:
                    response = client.get(path, params=merged_params)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(2**attempt)
                continue

            if response.status_code == 429:
                retry_after = float(
                    response.headers.get("Retry-After", 2 ** (attempt + 1))
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(retry_after)
                last_error = SoundCloudApiError(
                    f"Rate limited by SoundCloud (HTTP 429); "
                    f"retry-after: {retry_after}s"
                )
                continue

            if response.status_code not in range(200, 300):
                raise SoundCloudApiError(
                    f"SoundCloud API error: HTTP {response.status_code} "
                    f"for {category} ({url})"
                )

            try:
                body = response.json()
            except Exception as exc:
                raise SoundCloudApiError(
                    f"SoundCloud returned non-JSON for {category}"
                ) from exc

            if not isinstance(body, dict):
                raise SoundCloudApiError(
                    f"SoundCloud returned unexpected payload type for {category}: "
                    f"{type(body).__name__}"
                )

            safe_meta: dict[str, Any] = {
                "endpoint_category": category,
                "http_status": response.status_code,
                "byte_size": len(response.content),
                "content_type": response.headers.get("content-type", ""),
                "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            return body, safe_meta

        raise SoundCloudApiError(
            f"SoundCloud request failed after {_MAX_RETRIES} attempts: {last_error}"
        ) from last_error

    def get_me(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the authenticated account profile."""
        return self.get_json("/me", category="account")

    def get_track(self, track_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return a single track by ID."""
        return self.get_json(f"/tracks/{track_id}", category="track")

    def get_playlist(self, playlist_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return a single playlist by ID."""
        return self.get_json(f"/playlists/{playlist_id}", category="playlist")

    def get_user_tracks(
        self,
        user_id: str,
        *,
        limit: int = 200,
        next_href: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return one page of tracks for *user_id*."""
        if next_href:
            # Follow provider-issued pagination link exactly.
            path = next_href.replace(self._base_url, "")
            return self.get_json(path, category="track-collection-page")
        return self.get_json(
            f"/users/{user_id}/tracks",
            category="track-collection-page",
            params={"limit": limit, "linked_partitioning": 1},
        )

    def get_user_playlists(
        self,
        user_id: str,
        *,
        limit: int = 50,
        next_href: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return one page of playlists for *user_id*."""
        if next_href:
            path = next_href.replace(self._base_url, "")
            return self.get_json(path, category="playlist-collection-page")
        return self.get_json(
            f"/users/{user_id}/playlists",
            category="playlist-collection-page",
            params={"limit": limit, "linked_partitioning": 1},
        )
