"""SoundCloud API capture strategy.

Captures a SoundCloud source (account, track, or playlist) via the
authenticated SoundCloud API.  The strategy is intentionally read-only.

Scopes supported
----------------
- ``account`` / ``"me"`` — authenticated account profile + track/playlist
  discovery.
- ``track`` — individual track metadata.
- ``playlist`` — playlist or album metadata.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from mindcap.core.errors import CaptureFailedError
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.core.progress import CaptureProgressReporter
from mindcap.plugins.soundcloud.client import SoundCloudClient
from mindcap.plugins.soundcloud.errors import SoundCloudApiError
from mindcap.plugins.soundcloud.identifiers import source_scope


def _make_unit(
    unit_id: str,
    sequence: int,
    category: str,
    body: dict,  # type: ignore[type-arg]
    safe_meta: dict,  # type: ignore[type-arg]
) -> RawResponseUnit:
    return RawResponseUnit(
        unit_id=unit_id,
        sequence=sequence,
        media_type="application/json",
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        endpoint_category=category,
        retrieved_at=datetime.now(UTC),
        safe_metadata=safe_meta,
    )


class SoundCloudApiCaptureStrategy:
    """Capture a SoundCloud source via the authenticated API.

    Parameters
    ----------
    client:
        Optional pre-built client for dependency injection in tests.
    reporter:
        Optional progress reporter.
    """

    name = "api"

    def __init__(
        self,
        client: SoundCloudClient | None = None,
        reporter: CaptureProgressReporter | None = None,
    ) -> None:
        self._client = client
        self._reporter = reporter

    def _client_instance(self) -> SoundCloudClient:
        return self._client or SoundCloudClient()

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        try:
            scope = source_scope(request.canonical_identifier)
        except Exception as exc:
            raise CaptureFailedError(
                f"Cannot determine SoundCloud scope for: {request.canonical_identifier}"
            ) from exc

        if scope == "account":
            return self._capture_account(request)
        if scope == "track":
            return self._capture_track(request)
        if scope == "playlist":
            return self._capture_playlist(request)
        raise CaptureFailedError(f"Unsupported SoundCloud scope: {scope}")

    # ------------------------------------------------------------------
    # Account capture
    # ------------------------------------------------------------------

    def _capture_account(self, request: CaptureRequest) -> CaptureEnvelope:
        client = self._client_instance()
        units: list[RawResponseUnit] = []
        warnings: list[str] = []
        sequence = 0

        if self._reporter:
            self._reporter.phase("Resolving SoundCloud account...")

        try:
            account_body, account_meta = client.get_me()
            units.append(
                _make_unit(
                    "account-me",
                    sequence,
                    "account",
                    account_body,
                    account_meta,
                )
            )
            sequence += 1
        except SoundCloudApiError as exc:
            raise CaptureFailedError(
                f"Failed to capture SoundCloud account: {exc}"
            ) from exc

        user_id = str(account_body.get("id", ""))

        if not user_id:
            raise CaptureFailedError(
                "SoundCloud /me response did not include a user ID."
            )

        # Paginate tracks.
        if self._reporter:
            self._reporter.phase("Discovering tracks...")

        next_href: str | None = None
        page_num = 0
        max_pages = int(request.options.get("max_track_pages", 500))
        seen_hrefs: set[str] = set()

        while page_num < max_pages:
            page_num += 1
            if self._reporter:
                self._reporter.phase(f"Loading track page {page_num}...")

            try:
                page_body, page_meta = client.get_user_tracks(
                    user_id, next_href=next_href
                )
                units.append(
                    _make_unit(
                        f"tracks-page-{page_num:04d}",
                        sequence,
                        "track-collection-page",
                        page_body,
                        page_meta,
                    )
                )
                sequence += 1
            except SoundCloudApiError as exc:
                warnings.append(f"Track page {page_num} fetch error: {exc}")
                break

            raw_next = page_body.get("next_href")
            if not raw_next:
                break
            if raw_next in seen_hrefs:
                warnings.append(
                    "Repeated next_href detected; stopping track pagination."
                )
                break
            seen_hrefs.add(raw_next)
            next_href = raw_next

        # Paginate playlists.
        if self._reporter:
            self._reporter.phase("Discovering playlists...")

        next_href = None
        pl_page_num = 0
        seen_pl_hrefs: set[str] = set()

        while pl_page_num < 50:
            pl_page_num += 1
            try:
                pl_body, pl_meta = client.get_user_playlists(
                    user_id, next_href=next_href
                )
                units.append(
                    _make_unit(
                        f"playlists-page-{pl_page_num:04d}",
                        sequence,
                        "playlist-collection-page",
                        pl_body,
                        pl_meta,
                    )
                )
                sequence += 1
            except SoundCloudApiError as exc:
                warnings.append(f"Playlist page {pl_page_num} fetch error: {exc}")
                break

            raw_next = pl_body.get("next_href")
            if not raw_next:
                break
            if raw_next in seen_pl_hrefs:
                warnings.append(
                    "Repeated next_href detected; stopping playlist pagination."
                )
                break
            seen_pl_hrefs.add(raw_next)
            next_href = raw_next

        permalink = account_body.get("permalink") or ""
        canonical_url = f"https://soundcloud.com/{permalink}" if permalink else None

        return CaptureEnvelope(
            provider="soundcloud",
            source_type="account",
            canonical_identifier=request.canonical_identifier,
            canonical_url=canonical_url or request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy=self.name,
            response_units=units,
            warnings=warnings,
            safe_metadata={
                "user_id": user_id,
                "track_page_count": page_num,
                "playlist_page_count": pl_page_num,
            },
        )

    # ------------------------------------------------------------------
    # Track capture
    # ------------------------------------------------------------------

    def _capture_track(self, request: CaptureRequest) -> CaptureEnvelope:
        client = self._client_instance()
        warnings: list[str] = []

        if self._reporter:
            self._reporter.phase("Capturing track metadata...")

        # Extract numeric or string track ID from canonical identifier.
        # canonical_identifier format: "soundcloud-track-<account>/<track>"
        suffix = request.canonical_identifier.removeprefix("soundcloud-track-")
        # We need a track numeric ID — not always available without an API lookup.
        # If the request URL is available, we may have a numeric ID in safe_metadata.
        # For now, attempt to resolve via the permalink URL.
        if not suffix:
            raise CaptureFailedError(
                f"Cannot extract track path from: {request.canonical_identifier}"
            )

        try:
            track_body, track_meta = client.get_json(
                "/resolve",
                category="track",
                params={
                    "url": request.canonical_url or f"https://soundcloud.com/{suffix}"
                },
            )
        except SoundCloudApiError as exc:
            raise CaptureFailedError(
                f"Failed to capture SoundCloud track: {exc}"
            ) from exc

        units = [_make_unit("track-000", 0, "track", track_body, track_meta)]

        return CaptureEnvelope(
            provider="soundcloud",
            source_type="track",
            canonical_identifier=request.canonical_identifier,
            canonical_url=request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy=self.name,
            response_units=units,
            warnings=warnings,
            safe_metadata={
                "track_id": str(track_body.get("id") or ""),
                "title": track_body.get("title"),
            },
        )

    # ------------------------------------------------------------------
    # Playlist capture
    # ------------------------------------------------------------------

    def _capture_playlist(self, request: CaptureRequest) -> CaptureEnvelope:
        client = self._client_instance()
        warnings: list[str] = []

        if self._reporter:
            self._reporter.phase("Capturing playlist metadata...")

        suffix = request.canonical_identifier.removeprefix("soundcloud-playlist-")
        if not suffix:
            raise CaptureFailedError(
                f"Cannot extract playlist path from: {request.canonical_identifier}"
            )

        try:
            pl_body, pl_meta = client.get_json(
                "/resolve",
                category="playlist",
                params={
                    "url": request.canonical_url or f"https://soundcloud.com/{suffix}"
                },
            )
        except SoundCloudApiError as exc:
            raise CaptureFailedError(
                f"Failed to capture SoundCloud playlist: {exc}"
            ) from exc

        units = [_make_unit("playlist-000", 0, "playlist", pl_body, pl_meta)]

        return CaptureEnvelope(
            provider="soundcloud",
            source_type="playlist",
            canonical_identifier=request.canonical_identifier,
            canonical_url=request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy=self.name,
            response_units=units,
            warnings=warnings,
            safe_metadata={
                "playlist_id": str(pl_body.get("id") or ""),
                "title": pl_body.get("title"),
            },
        )
