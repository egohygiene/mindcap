"""Suno account-wide collection discovery for the Mindcap sync subsystem.

Discovers every workspace/project available to the authenticated Suno account
and yields canonical :class:`~mindcap.sync.models.SourceDescriptor` objects.

Discovery strategy
------------------
1. Probe known project/workspace list endpoints in priority order.
2. Paginate until a terminal signal (empty page, no ``has_more``, or
   cursor exhaustion).
3. Apply repeated-page and repeated-cursor protection.
4. Emit a :class:`~mindcap.sync.models.DiscoveryResult` with evidence.

Authentication
--------------
Uses the existing :class:`~mindcap.plugins.suno.client.SunoClient` with the
stored Clerk cookie state.  No cookies or tokens are written to descriptors.

Provider notes
--------------
The Suno project/workspace list endpoint is not publicly documented.  This
adapter probes known candidate paths using the same methodology as the existing
single-source client.  The adapter must be updated when the production contract
is observed.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from mindcap.core.progress import CaptureProgressReporter
from mindcap.plugins.suno.auth import SunoAuthState
from mindcap.plugins.suno.client import SunoClient
from mindcap.plugins.suno.errors import SunoApiError
from mindcap.sync.models import (
    CollectionRequest,
    DiscoveryResult,
    SourceDescriptor,
)

# ---------------------------------------------------------------------------
# Known candidate endpoints for project/workspace listing.
# These are probed in order; the first successful response wins.
# ---------------------------------------------------------------------------
_LIST_ENDPOINTS: tuple[str, ...] = (
    "/api/projects/",
    "/api/project/",
    "/api/workspaces/",
    "/api/workspace/",
)

# Safety caps
_MAX_PAGES = 500
_MAX_CURSOR_REPEATS = 3


class SunoCollectionDiscovery:
    """Discover all Suno account workspaces/projects.

    Implements the :class:`~mindcap.sync.protocols.CollectionDiscoveryStrategy`
    protocol.

    Parameters
    ----------
    client:
        An authenticated :class:`~mindcap.plugins.suno.client.SunoClient`.
        When ``None``, one is created from stored authentication state.
    auth_state:
        Optional override for the Suno authentication state.
    """

    def __init__(
        self,
        client: SunoClient | None = None,
        auth_state: SunoAuthState | None = None,
    ) -> None:
        self._client = client
        self._auth_state = auth_state
        self.discovery_result: DiscoveryResult | None = None

    def _get_client(self) -> SunoClient:
        if self._client is not None:
            return self._client
        return SunoClient(state=self._auth_state)

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

        # Try each known list endpoint until one succeeds.
        working_endpoint: str | None = None
        for endpoint in _LIST_ENDPOINTS:
            try:
                payload, _ = client.get_json(endpoint, category="project-list")
                working_endpoint = endpoint
                break
            except SunoApiError:
                continue
            except Exception:
                continue

        if working_endpoint is None:
            # No list endpoint resolved — yield nothing, mark incomplete.
            warnings.append(
                "No project/workspace list endpoint resolved. "
                "The Suno account may be empty or the API contract has changed."
            )
            self.discovery_result = DiscoveryResult(
                provider="suno",
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

        # Parse and yield the first page.
        items_from_page, page_expected = _extract_items(payload)
        if page_expected is not None:
            expected_count = page_expected

        page_workspace_ids: set[str] = set()
        pages_observed = 1
        for descriptor in _items_to_descriptors(items_from_page, request):
            cid = descriptor.canonical_identifier
            if cid in seen_ids:
                duplicate_count += 1
                continue
            seen_ids.add(cid)
            page_workspace_ids.add(cid)
            yield descriptor

            if request.max_items is not None and len(seen_ids) >= request.max_items:
                terminal_signal = "max-items-reached"
                break

        if terminal_signal == "max-items-reached":
            self.discovery_result = _build_result(
                request=request,
                expected_count=expected_count,
                seen_ids=seen_ids,
                pages_observed=pages_observed,
                duplicate_count=duplicate_count,
                terminal_signal=terminal_signal,
                repeated_page_triggered=repeated_page_triggered,
                repeated_cursor_triggered=repeated_cursor_triggered,
                warnings=warnings,
            )
            return

        # Paginate.
        # seen_cursors tracks cursors already *used* to request a page, so that
        # we can detect loops where the provider returns the same cursor twice.
        # Do NOT pre-populate with the cursor from the first page — it has not
        # been used as a request cursor yet.
        seen_cursors: set[str] = set()
        page_num = 2

        while page_num <= _MAX_PAGES:
            # Cursor-based pagination.
            has_more = payload.get("has_more", False)
            current_cursor = payload.get("next_cursor") or payload.get("cursor")

            if not has_more and current_cursor is None:
                terminal_signal = "no-more-pages"
                break

            if current_cursor is not None and current_cursor in seen_cursors:
                repeated_cursor_triggered = True
                warnings.append(
                    f"Repeated cursor detected on page {page_num}; stopping pagination."
                )
                terminal_signal = "repeated-cursor"
                break

            params: dict[str, Any] = {}
            if current_cursor is not None:
                params["cursor"] = current_cursor
                seen_cursors.add(current_cursor)
            else:
                params["page"] = page_num

            if reporter:
                reporter.phase(f"Loading collection page {page_num}...")

            try:
                payload, _ = client.get_json(
                    working_endpoint,
                    category="project-list",
                    params=params,
                )
            except SunoApiError as err:
                warnings.append(f"Error fetching page {page_num}: {err}")
                break

            pages_observed += 1
            items_from_page, _ = _extract_items(payload)

            if not items_from_page:
                terminal_signal = "empty-page"
                break

            new_page_ids: set[str] = set()
            for descriptor in _items_to_descriptors(items_from_page, request):
                cid = descriptor.canonical_identifier
                if cid in seen_ids:
                    duplicate_count += 1
                    continue
                seen_ids.add(cid)
                new_page_ids.add(cid)
                yield descriptor

                if request.max_items is not None and len(seen_ids) >= request.max_items:
                    terminal_signal = "max-items-reached"
                    break

            if terminal_signal == "max-items-reached":
                break

            if not new_page_ids:
                # All items on this page were duplicates — stop.
                repeated_page_triggered = True
                warnings.append(
                    f"Page {page_num} contained only duplicate items; "
                    "stopping pagination."
                )
                terminal_signal = "repeated-page"
                break

            if expected_count is not None and len(seen_ids) >= expected_count:
                terminal_signal = "expected-count-reached"
                break

            page_num += 1

        if page_num > _MAX_PAGES:
            repeated_page_triggered = True
            warnings.append(f"Pagination safety cap of {_MAX_PAGES} pages reached.")
            terminal_signal = "safety-cap"

        self.discovery_result = _build_result(
            request=request,
            expected_count=expected_count,
            seen_ids=seen_ids,
            pages_observed=pages_observed,
            duplicate_count=duplicate_count,
            terminal_signal=terminal_signal,
            repeated_page_triggered=repeated_page_triggered,
            repeated_cursor_triggered=repeated_cursor_triggered,
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_items(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    """Return (items, expected_count) from a list API response payload."""
    expected: int | None = None

    # Try common total-count fields.
    for key in ("total", "total_count", "count", "total_projects", "total_workspaces"):
        value = payload.get(key)
        if isinstance(value, int):
            expected = value
            break

    # Try common items list fields.
    for key in (
        "projects",
        "workspaces",
        "items",
        "data",
        "results",
        "project_list",
        "workspace_list",
    ):
        items = payload.get(key)
        if isinstance(items, list):
            return items, expected

    # Fall back to the response itself being a list.
    return [], expected


def _items_to_descriptors(
    items: list[dict[str, Any]],
    request: CollectionRequest,
) -> Iterable[SourceDescriptor]:
    """Convert raw API items to canonical SourceDescriptors."""
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        workspace_id = (
            item.get("id") or item.get("project_id") or item.get("workspace_id")
        )
        if not workspace_id:
            continue
        workspace_id = str(workspace_id)
        title = item.get("name") or item.get("title") or item.get("display_name")
        clip_count = item.get("clip_count") or item.get("clips_count")

        # Remote revision: use updated_at or a version/revision field.
        remote_revision: str | None = None
        for rev_key in ("updated_at", "revision", "version", "etag", "modified_at"):
            val = item.get(rev_key)
            if val is not None:
                remote_revision = str(val)
                break

        remote_updated_at: datetime | None = None
        for ts_key in ("updated_at", "modified_at", "created_at"):
            val = item.get(ts_key)
            if isinstance(val, str):
                import contextlib

                with contextlib.suppress(ValueError):
                    remote_updated_at = datetime.fromisoformat(
                        val.replace("Z", "+00:00")
                    )
                break

        safe_meta: dict[str, Any] = {}
        for safe_key in (
            "clip_count",
            "is_owned",
            "is_public",
            "is_trashed",
            "shared",
            "created_at",
            "description",
            "cover_image_path",
        ):
            val = item.get(safe_key)
            if val is not None:
                safe_meta[safe_key] = val
        if clip_count is not None:
            safe_meta["clip_count"] = clip_count

        yield SourceDescriptor(
            provider="suno",
            source_type="workspace",
            canonical_identifier=workspace_id,
            canonical_url=f"https://suno.com/create?wid={workspace_id}",
            display_title=str(title) if title else workspace_id,
            collection_position=position,
            remote_revision=remote_revision,
            remote_updated_at=remote_updated_at,
            remote_status="trashed"
            if item.get("is_trashed")
            else ("active" if item.get("is_owned") else None),
            safe_metadata=safe_meta,
        )


def _build_result(
    *,
    request: CollectionRequest,
    expected_count: int | None,
    seen_ids: set[str],
    pages_observed: int,
    duplicate_count: int,
    terminal_signal: str | None,
    repeated_page_triggered: bool,
    repeated_cursor_triggered: bool,
    warnings: list[str],
) -> DiscoveryResult:
    unique_count = len(seen_ids)
    complete = (
        terminal_signal is not None
        and terminal_signal not in {"repeated-page", "repeated-cursor", "safety-cap"}
        and (expected_count is None or unique_count >= expected_count)
    )
    if expected_count is not None and unique_count < expected_count:
        complete = False
        if f"Expected {expected_count}" not in " ".join(warnings):
            warnings.append(
                f"Expected {expected_count} items but discovered only {unique_count}."
            )
    return DiscoveryResult(
        provider="suno",
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
