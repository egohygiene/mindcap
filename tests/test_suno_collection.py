"""Tests for Suno collection discovery (no network access)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from mindcap.plugins.suno.collection import (
    SunoCollectionDiscovery,
    _extract_items,
    _items_to_descriptors,
)
from mindcap.sync.models import CollectionRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(tmp_path: Path, max_items: int | None = None) -> CollectionRequest:
    return CollectionRequest(
        provider="suno",
        collection_identifier="suno-account",
        artifact_root=tmp_path,
        max_items=max_items,
    )


def _workspace_item(
    workspace_id: str,
    title: str = "My Workspace",
    updated_at: str | None = None,
    clip_count: int | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": workspace_id,
        "name": title,
        "is_owned": True,
        "is_public": False,
        "is_trashed": False,
    }
    if updated_at:
        item["updated_at"] = updated_at
    if clip_count is not None:
        item["clip_count"] = clip_count
    return item


# ---------------------------------------------------------------------------
# _extract_items
# ---------------------------------------------------------------------------


class TestExtractItems:
    def test_extracts_from_projects_key(self) -> None:
        payload: dict[str, Any] = {
            "projects": [{"id": "p1"}, {"id": "p2"}],
            "total": 2,
        }
        items, expected = _extract_items(payload)
        assert len(items) == 2
        assert expected == 2

    def test_extracts_from_workspaces_key(self) -> None:
        payload: dict[str, Any] = {"workspaces": [{"id": "w1"}]}
        items, expected = _extract_items(payload)
        assert len(items) == 1
        assert expected is None

    def test_extracts_from_data_key(self) -> None:
        payload: dict[str, Any] = {"data": [{"id": "d1"}]}
        items, _expected = _extract_items(payload)
        assert len(items) == 1

    def test_returns_empty_for_unknown_structure(self) -> None:
        items, expected = _extract_items({})
        assert items == []
        assert expected is None


# ---------------------------------------------------------------------------
# _items_to_descriptors
# ---------------------------------------------------------------------------


class TestItemsToDescriptors:
    def test_basic_descriptor_fields(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        items = [
            _workspace_item("ws-abc", "Chill Vibes", updated_at="2026-01-01T00:00:00Z")
        ]
        descriptors = list(_items_to_descriptors(items, request))
        assert len(descriptors) == 1
        d = descriptors[0]
        assert d.canonical_identifier == "ws-abc"
        assert d.display_title == "Chill Vibes"
        assert d.provider == "suno"
        assert d.source_type == "workspace"
        assert d.canonical_url == "https://suno.com/create?wid=ws-abc"
        assert d.remote_revision == "2026-01-01T00:00:00Z"

    def test_skips_items_without_id(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        items = [{"name": "No ID"}]
        descriptors = list(_items_to_descriptors(items, request))
        assert descriptors == []

    def test_trashed_workspace_gets_trashed_status(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        items = [{"id": "ws-trash", "is_trashed": True}]
        descriptors = list(_items_to_descriptors(items, request))
        assert descriptors[0].remote_status == "trashed"

    def test_collection_position_set(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        items = [_workspace_item(f"ws-{i}") for i in range(5)]
        descriptors = list(_items_to_descriptors(items, request))
        for i, d in enumerate(descriptors):
            assert d.collection_position == i

    def test_safe_metadata_populated(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        items = [_workspace_item("ws-1", clip_count=42)]
        descriptors = list(_items_to_descriptors(items, request))
        assert descriptors[0].safe_metadata.get("clip_count") == 42


# ---------------------------------------------------------------------------
# SunoCollectionDiscovery
# ---------------------------------------------------------------------------


class TestSunoCollectionDiscoveryZeroItems:
    def test_empty_response_yields_no_descriptors(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.get_json.return_value = (
            {"projects": [], "has_more": False},
            MagicMock(),
        )
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        assert result == []

    def test_no_endpoint_resolves_yields_nothing(self, tmp_path: Path) -> None:
        from mindcap.plugins.suno.errors import SunoApiError

        mock_client = MagicMock()
        mock_client.get_json.side_effect = SunoApiError("404")
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        assert result == []
        assert discovery.discovery_result is not None
        assert not discovery.discovery_result.discovery_complete


class TestSunoCollectionDiscoverySinglePage:
    def test_single_page_yields_all_items(self, tmp_path: Path) -> None:
        items = [_workspace_item(f"ws-{i:03d}") for i in range(10)]
        mock_client = MagicMock()
        mock_client.get_json.return_value = (
            {"projects": items, "has_more": False, "total": 10},
            MagicMock(),
        )
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        assert len(result) == 10

    def test_discovery_result_set_after_iteration(self, tmp_path: Path) -> None:
        items = [_workspace_item("ws-001")]
        mock_client = MagicMock()
        mock_client.get_json.return_value = (
            {"projects": items, "has_more": False},
            MagicMock(),
        )
        discovery = SunoCollectionDiscovery(client=mock_client)
        list(discovery.discover(_request(tmp_path), reporter=None))
        assert discovery.discovery_result is not None
        assert discovery.discovery_result.unique_items_discovered == 1


class TestSunoCollectionDiscoveryPagination:
    def test_pagination_collects_all_pages(self, tmp_path: Path) -> None:
        page1_items = [_workspace_item(f"ws-p1-{i}") for i in range(5)]
        page2_items = [_workspace_item(f"ws-p2-{i}") for i in range(5)]
        responses = [
            # First call: page 1
            (
                {"projects": page1_items, "has_more": True, "next_cursor": "cursor-1"},
                MagicMock(),
            ),
            # Second call: page 2
            ({"projects": page2_items, "has_more": False}, MagicMock()),
        ]
        mock_client = MagicMock()
        mock_client.get_json.side_effect = responses
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        assert len(result) == 10

    def test_max_items_stops_pagination(self, tmp_path: Path) -> None:
        items = [_workspace_item(f"ws-{i:03d}") for i in range(50)]
        mock_client = MagicMock()
        mock_client.get_json.return_value = (
            {"projects": items, "has_more": True},
            MagicMock(),
        )
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(
            discovery.discover(_request(tmp_path, max_items=10), reporter=None)
        )
        assert len(result) == 10


class TestSunoCollectionDiscoveryDuplicates:
    def test_duplicate_ids_are_deduplicated(self, tmp_path: Path) -> None:
        items = [_workspace_item("ws-dup")] * 5
        mock_client = MagicMock()
        mock_client.get_json.return_value = (
            {"projects": items, "has_more": False},
            MagicMock(),
        )
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        assert len(result) == 1
        assert discovery.discovery_result is not None
        assert discovery.discovery_result.duplicate_identifiers_observed == 4


class TestSunoCollectionDiscoveryRepeatedPage:
    def test_repeated_cursor_protection(self, tmp_path: Path) -> None:
        """A repeated cursor must stop pagination.

        Page 2 must return *different* items so that the all-duplicates early
        exit does not fire before we reach the cursor-repeat check on page 3.
        """
        page1_items = [_workspace_item("ws-page1")]
        page2_items = [_workspace_item("ws-page2")]  # new items — no early exit
        responses = [
            # First call: probe endpoint → page 1 with cursor.
            (
                {
                    "projects": page1_items,
                    "has_more": True,
                    "next_cursor": "cursor-loop",
                },
                MagicMock(),
            ),
            # Second call: page 2 — different items but same cursor returned.
            (
                {
                    "projects": page2_items,
                    "has_more": True,
                    "next_cursor": "cursor-loop",
                },
                MagicMock(),
            ),
        ]
        mock_client = MagicMock()
        mock_client.get_json.side_effect = responses
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        # Both pages' unique items are returned (cursor loop detected at page 3 check).
        assert len(result) == 2
        assert discovery.discovery_result is not None
        assert discovery.discovery_result.repeated_cursor_protection_triggered


class TestSunoCollectionDiscovery500Items:
    """Synthetic scale test: 500 workspaces."""

    def test_500_workspaces_discovered(self, tmp_path: Path) -> None:
        items = [_workspace_item(f"ws-{i:04d}") for i in range(500)]
        mock_client = MagicMock()
        mock_client.get_json.return_value = (
            {"projects": items, "has_more": False, "total": 500},
            MagicMock(),
        )
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        assert len(result) == 500
        assert discovery.discovery_result is not None
        assert discovery.discovery_result.unique_items_discovered == 500

    def test_500_items_no_duplicates_in_output(self, tmp_path: Path) -> None:
        items = [_workspace_item(f"ws-{i:04d}") for i in range(500)]
        mock_client = MagicMock()
        mock_client.get_json.return_value = (
            {"projects": items, "has_more": False},
            MagicMock(),
        )
        discovery = SunoCollectionDiscovery(client=mock_client)
        result = list(discovery.discover(_request(tmp_path), reporter=None))
        ids = [d.canonical_identifier for d in result]
        assert len(ids) == len(set(ids))
