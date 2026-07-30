"""SoundCloud account-wide collection discovery for the Mindcap sync subsystem.

Discovers every track visible to the authenticated account and yields canonical
:class:`~mindcap.sync.models.SourceDescriptor` objects for each.

Discovery strategy
------------------
1. Resolve the authenticated account ID via GET /me.
2. Paginate GET /users/<id>/tracks until exhausted.
3. Apply repeated-page and repeated-cursor protection.
4. Emit a :class:`~mindcap.sync.models.DiscoveryResult` with evidence.

Authentication
--------------
Uses the existing :class:`~mindcap.plugins.soundcloud.client.SoundCloudClient`
with the stored OAuth token state.  No tokens or cookies are written to
descriptors.

Provider notes
--------------
SoundCloud paginates track collections via ``next_href`` in the response body.
Each ``next_href`` is followed exactly as issued by the provider; cursors are
never synthesized or mutated.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from mindcap.core.progress import CaptureProgressReporter
from mindcap.plugins.soundcloud.auth import SoundCloudAuthState
from mindcap.plugins.soundcloud.client import SoundCloudClient
from mindcap.plugins.soundcloud.errors import SoundCloudApiError
from mindcap.sync.models import (
    CollectionRequest,
    DiscoveryResult,
    SourceDescriptor,
)

# Safety cap: stop after this many pages regardless of provider signals.
_MAX_PAGES = 500


class SoundCloudCollectionDiscovery:
    """Discover all tracks in the authenticated SoundCloud account.

    Implements the :class:`~mindcap.sync.protocols.CollectionDiscoveryStrategy`
    protocol.

    Parameters
    ----------
    client:
        An authenticated :class:`~mindcap.plugins.soundcloud.client.SoundCloudClient`.
        When ``None``, one is created from stored authentication state.
    auth_state:
        Optional override for the SoundCloud authentication state.
    """

    def __init__(
        self,
        client: SoundCloudClient | None = None,
        auth_state: SoundCloudAuthState | None = None,
    ) -> None:
        self._client = client
        self._auth_state = auth_state
        self.discovery_result: DiscoveryResult | None = None

    def _get_client(self) -> SoundCloudClient:
        if self._client is not None:
            return self._client
        return SoundCloudClient(state=self._auth_state)

    def discover(
        self,
        request: CollectionRequest,
        reporter: CaptureProgressReporter | None,
    ) -> Iterable[SourceDescriptor]:
        """Yield :class:`~mindcap.sync.models.SourceDescriptor` objects.

        The discovery result is stored in :attr:`discovery_result` after the
        iterable is exhausted.
        """
        return list(self._discover_iter(request, reporter))

    def _discover_iter(
        self,
        request: CollectionRequest,
        reporter: CaptureProgressReporter | None,
    ) -> Iterable[SourceDescriptor]:
        client = self._get_client()
        warnings: list[str] = []
        seen_ids: set[str] = set()
        duplicate_count = 0
        pages_observed = 0
        expected_count: int | None = None
        terminal_signal: str | None = None
        repeated_page_triggered = False
        repeated_cursor_triggered = False

        # Resolve the authenticated account.
        try:
            account_payload, _ = client.get_me()
        except SoundCloudApiError as err:
            warnings.append(f"Could not resolve SoundCloud account: {err}")
            self.discovery_result = DiscoveryResult(
                provider="soundcloud",
                collection_identifier=request.collection_identifier,
                collection_url=request.collection_url,
                expected_item_count=None,
                unique_items_discovered=0,
                pages_observed=0,
                discovery_complete=False,
                warnings=warnings,
                discovered_at=datetime.now(UTC),
                terminal_signal=None,
            )
            return

        user_id = str(account_payload.get("id", ""))
        expected_count = account_payload.get("track_count")

        if reporter:
            reporter.phase(
                f"Resolved SoundCloud account: {user_id}. Discovering tracks..."
            )

        # Follow provider-issued next_href links exactly.
        next_href: str | None = None
        seen_next_hrefs: set[str] = set()

        while True:
            page_num = pages_observed + 1
            if page_num > _MAX_PAGES:
                warnings.append(f"Pagination safety cap of {_MAX_PAGES} pages reached.")
                terminal_signal = "safety-cap"
                repeated_page_triggered = True
                break

            if reporter:
                reporter.phase(f"Loading track page {page_num}...")

            try:
                payload, _ = client.get_user_tracks(user_id, next_href=next_href)
            except SoundCloudApiError as err:
                warnings.append(f"Error fetching track page {page_num}: {err}")
                break

            pages_observed += 1
            items = payload.get("collection") or []

            if not items and pages_observed == 1:
                terminal_signal = "empty-collection"
                break

            new_ids: set[str] = set()
            for item in items:
                if not isinstance(item, dict):
                    continue
                track_id = item.get("id")
                if track_id is None:
                    continue
                tid = str(track_id)
                if tid in seen_ids:
                    duplicate_count += 1
                    continue
                seen_ids.add(tid)
                new_ids.add(tid)
                yield _item_to_descriptor(item, len(seen_ids) - 1, request)

                if request.max_items is not None and len(seen_ids) >= request.max_items:
                    terminal_signal = "max-items-reached"
                    break

            if terminal_signal == "max-items-reached":
                break

            if not new_ids and items:
                repeated_page_triggered = True
                warnings.append(
                    f"Page {page_num} contained only duplicate items; "
                    "stopping pagination."
                )
                terminal_signal = "repeated-page"
                break

            # Advance to next page.
            raw_next = payload.get("next_href")
            if not raw_next:
                terminal_signal = "no-more-pages"
                break

            if raw_next in seen_next_hrefs:
                repeated_cursor_triggered = True
                warnings.append(
                    f"Repeated next_href detected on page {page_num}; "
                    "stopping pagination."
                )
                terminal_signal = "repeated-cursor"
                break

            seen_next_hrefs.add(raw_next)
            next_href = raw_next

            if expected_count is not None and len(seen_ids) >= expected_count:
                terminal_signal = "expected-count-reached"
                break

        unique_count = len(seen_ids)
        complete = (
            terminal_signal is not None
            and terminal_signal
            not in {"repeated-page", "repeated-cursor", "safety-cap"}
            and (expected_count is None or unique_count >= expected_count)
        )
        if expected_count is not None and unique_count < expected_count:
            complete = False
            warnings.append(
                f"Expected {expected_count} tracks but discovered only {unique_count}."
            )

        self.discovery_result = DiscoveryResult(
            provider="soundcloud",
            collection_identifier=request.collection_identifier,
            collection_url=request.collection_url,
            expected_item_count=expected_count,
            unique_items_discovered=unique_count,
            pages_observed=pages_observed,
            duplicate_identifiers_observed=duplicate_count,
            terminal_signal=terminal_signal,
            repeated_page_protection_triggered=repeated_page_triggered,
            repeated_cursor_protection_triggered=repeated_cursor_triggered,
            discovery_complete=complete,
            warnings=warnings,
            discovered_at=datetime.now(UTC),
        )


def _item_to_descriptor(
    item: dict[str, Any],
    position: int,
    request: CollectionRequest,
) -> SourceDescriptor:
    """Convert a raw track item to a :class:`SourceDescriptor`."""
    track_id = str(item.get("id", ""))
    title = item.get("title") or item.get("permalink")
    permalink = item.get("permalink") or ""
    permalink_url = item.get("permalink_url") or ""

    # Remote revision: prefer last_modified, fall back to created_at.
    remote_revision: str | None = None
    for rev_key in ("last_modified", "updated_at", "created_at"):
        val = item.get(rev_key)
        if val is not None:
            remote_revision = str(val)
            break

    remote_updated_at: datetime | None = None
    for ts_key in ("last_modified", "updated_at", "created_at"):
        val = item.get(ts_key)
        if isinstance(val, str):
            with contextlib.suppress(ValueError):
                remote_updated_at = datetime.fromisoformat(val.replace("Z", "+00:00"))
            break

    safe_meta: dict[str, Any] = {}
    for key in (
        "sharing",
        "access",
        "state",
        "duration",
        "genre",
        "downloadable",
        "has_downloads_left",
        "created_at",
        "last_modified",
    ):
        val = item.get(key)
        if val is not None:
            safe_meta[key] = val

    canonical_id = (
        f"soundcloud-track-{permalink}" if permalink else f"soundcloud-track-{track_id}"
    )
    canonical_url = (
        permalink_url if permalink_url.startswith("https://soundcloud.com/") else None
    )

    return SourceDescriptor(
        provider="soundcloud",
        source_type="track",
        canonical_identifier=canonical_id,
        canonical_url=canonical_url,
        display_title=str(title) if title else canonical_id,
        collection_position=position,
        remote_revision=remote_revision,
        remote_updated_at=remote_updated_at,
        remote_status=item.get("sharing") or item.get("state"),
        safe_metadata=safe_meta,
    )
