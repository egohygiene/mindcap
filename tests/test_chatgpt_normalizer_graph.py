"""Tests for graph integrity integration in the normalizer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mindcap.core.models import CaptureEnvelope, RawResponseUnit
from mindcap.plugins.chatgpt.normalizer import normalize_chatgpt

FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt"
BRANCHING = FIXTURES / "branching-conversation.json"
IDENTIFIER = "6a14b69f-7834-83ea-8257-0eceadb41691"


def _make_envelope(body: bytes) -> CaptureEnvelope:
    return CaptureEnvelope(
        provider="chatgpt",
        source_type="conversation",
        canonical_identifier=IDENTIFIER,
        canonical_url=f"https://chatgpt.com/c/{IDENTIFIER}",
        captured_at=datetime(2024, 1, 1, tzinfo=UTC),
        strategy="saved-json",
        response_units=[
            RawResponseUnit(
                unit_id="response-000",
                sequence=0,
                media_type="application/json",
                body=body,
                source_url=None,
            )
        ],
    )


def _normalize() -> dict[str, Any]:
    return normalize_chatgpt(_make_envelope(BRANCHING.read_bytes()), IDENTIFIER)


# ---------------------------------------------------------------------------
# graph_integrity field
# ---------------------------------------------------------------------------


def test_graph_integrity_field_present() -> None:
    normalized = _normalize()
    assert "graph_integrity" in normalized


def test_graph_integrity_is_dict() -> None:
    normalized = _normalize()
    assert isinstance(normalized["graph_integrity"], dict)


def test_valid_conversation_has_complete_graph() -> None:
    normalized = _normalize()
    assert normalized["graph_integrity"]["complete"] is True


def test_graph_integrity_has_required_keys() -> None:
    normalized = _normalize()
    keys = normalized["graph_integrity"].keys()
    required = {
        "complete",
        "node_count",
        "message_count",
        "cycle_count",
        "orphan_count",
        "warnings",
        "errors",
    }
    assert required.issubset(keys)


def test_graph_integrity_node_count_matches() -> None:
    normalized = _normalize()
    assert (
        normalized["graph_integrity"]["node_count"] == normalized["provider_node_count"]
    )


# ---------------------------------------------------------------------------
# Malformed graph produces warning in attachment_warnings
# ---------------------------------------------------------------------------


def test_malformed_graph_warning_surfaces() -> None:
    """A conversation with a missing parent should add a graph warning."""
    payload = {
        "id": IDENTIFIER,
        "title": "Malformed graph",
        "current_node": "asst",
        "mapping": {
            "asst": {
                "id": "asst",
                "parent": "MISSING_PARENT",
                "children": [],
                "message": {
                    "id": "asst",
                    "author": {"role": "assistant"},
                    "create_time": 1784304090,
                    "status": "finished_successfully",
                    "content": {"content_type": "text", "parts": ["Hi"]},
                    "metadata": {},
                },
            }
        },
    }
    body = json.dumps(payload).encode()
    normalized = normalize_chatgpt(_make_envelope(body), IDENTIFIER)
    assert normalized["graph_integrity"]["complete"] is False
    assert normalized["graph_integrity"]["missing_parent_count"] == 1
    # Warning should appear in attachment_warnings (merged list).
    assert any("MISSING_PARENT" in w for w in normalized["attachment_warnings"])
