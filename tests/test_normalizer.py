"""Unit tests for the ChatGPT normalizer: branch index, required fields."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mindcap.core.models import CaptureEnvelope, RawResponseUnit
from mindcap.plugins.chatgpt.normalizer import (
    _compute_branch_index,
    normalize_chatgpt,
)

IDENTIFIER = "6a14b69f-7834-83ea-8257-0eceadb41691"
FIXTURE = Path(__file__).parent / "fixtures" / "chatgpt" / "branching-conversation.json"

_CAPTURED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def _make_envelope(body: bytes) -> CaptureEnvelope:
    return CaptureEnvelope(
        provider="chatgpt",
        source_type="conversation",
        canonical_identifier=IDENTIFIER,
        canonical_url=f"https://chatgpt.com/c/{IDENTIFIER}",
        captured_at=_CAPTURED_AT,
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


def _normalize() -> dict:
    return normalize_chatgpt(_make_envelope(FIXTURE.read_bytes()), IDENTIFIER)


# ---------------------------------------------------------------------------
# Branch index
# ---------------------------------------------------------------------------


def test_branch_index_is_present() -> None:
    normalized = _normalize()
    assert "branch_index" in normalized
    assert isinstance(normalized["branch_index"], list)


def test_branch_index_covers_all_paths() -> None:
    """Branching fixture has 3 branches: trunk + 2 continuations."""
    normalized = _normalize()
    branch_index = normalized["branch_index"]
    # trunk [root -> user-1] + alternate + selected
    assert len(branch_index) == 3


def test_branch_index_trunk_is_selected() -> None:
    normalized = _normalize()
    trunk = next(
        b for b in normalized["branch_index"] if b["branch_point_message_id"] is None
    )
    assert trunk["is_selected"] is True


def test_branch_index_selected_branch_is_selected() -> None:
    normalized = _normalize()
    selected = [
        b
        for b in normalized["branch_index"]
        if b["branch_point_message_id"] == "user-1" and b["is_selected"] is True
    ]
    assert len(selected) == 1
    assert "assistant-selected" in selected[0]["message_ids"]


def test_branch_index_alternate_branch_not_selected() -> None:
    normalized = _normalize()
    alternates = [
        b
        for b in normalized["branch_index"]
        if b["branch_point_message_id"] == "user-1" and b["is_selected"] is False
    ]
    assert len(alternates) == 1
    assert "assistant-alternate" in alternates[0]["message_ids"]


def test_branch_index_fields_present() -> None:
    normalized = _normalize()
    required = {
        "branch_id",
        "branch_point_message_id",
        "parent_branch_id",
        "branch_index",
        "message_ids",
        "leaf_message_id",
        "is_selected",
    }
    for entry in normalized["branch_index"]:
        assert required.issubset(entry.keys()), f"Missing fields in {entry}"


def test_branch_index_sibling_indices_are_zero_based() -> None:
    normalized = _normalize()
    siblings = [
        b
        for b in normalized["branch_index"]
        if b["branch_point_message_id"] == "user-1"
    ]
    assert sorted(b["branch_index"] for b in siblings) == [0, 1]


def test_branch_index_linear_conversation() -> None:
    """A conversation with no branching produces exactly one branch."""
    mapping = {
        "m-0": {"id": "m-0", "parent": None, "children": ["m-1"], "message": None},
        "m-1": {"id": "m-1", "parent": "m-0", "children": [], "message": None},
    }
    result = _compute_branch_index(mapping, ["m-0"], ["m-0", "m-1"])
    assert len(result) == 1
    assert result[0]["message_ids"] == ["m-0", "m-1"]
    assert result[0]["leaf_message_id"] == "m-1"
    assert result[0]["is_selected"] is True
    assert result[0]["branch_point_message_id"] is None
    assert result[0]["parent_branch_id"] is None


def test_branch_index_no_selected_path_gives_null_is_selected() -> None:
    mapping = {
        "m-0": {"id": "m-0", "parent": None, "children": [], "message": None},
    }
    result = _compute_branch_index(mapping, ["m-0"], [])
    assert result[0]["is_selected"] is None


def test_branch_index_cycle_does_not_hang() -> None:
    """A cycle in the mapping must not cause an infinite loop."""
    mapping = {
        "m-0": {"id": "m-0", "parent": None, "children": ["m-1"], "message": None},
        "m-1": {"id": "m-1", "parent": "m-0", "children": ["m-0"], "message": None},
    }
    result = _compute_branch_index(mapping, ["m-0"], [])
    # Should produce at least one branch without hanging.
    assert isinstance(result, list)
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# Required top-level fields
# ---------------------------------------------------------------------------


def test_normalized_has_redactions_field() -> None:
    normalized = _normalize()
    assert "redactions" in normalized
    assert normalized["redactions"] == []


def test_normalized_has_branch_index_field() -> None:
    normalized = _normalize()
    assert "branch_index" in normalized


# ---------------------------------------------------------------------------
# Message-level required fields
# ---------------------------------------------------------------------------


def test_messages_have_redactions() -> None:
    normalized = _normalize()
    for msg in normalized["messages"].values():
        assert "redactions" in msg, f"Missing redactions in {msg['message_id']}"
        assert msg["redactions"] == []


def test_messages_have_provider_metadata() -> None:
    normalized = _normalize()
    for msg in normalized["messages"].values():
        assert "provider_metadata" in msg, (
            f"Missing provider_metadata in {msg['message_id']}"
        )
        assert isinstance(msg["provider_metadata"], dict)


# ---------------------------------------------------------------------------
# Content part required fields
# ---------------------------------------------------------------------------


def test_content_parts_have_attachment_id() -> None:
    normalized = _normalize()
    for msg in normalized["messages"].values():
        for part in msg["content"]:
            assert "attachment_id" in part, f"Missing attachment_id in {part}"
            assert part["attachment_id"] is None


def test_content_parts_have_provider_metadata() -> None:
    normalized = _normalize()
    for msg in normalized["messages"].values():
        for part in msg["content"]:
            assert "provider_metadata" in part, f"Missing provider_metadata in {part}"
            assert isinstance(part["provider_metadata"], dict)


# ---------------------------------------------------------------------------
# Participant required fields
# ---------------------------------------------------------------------------


def test_participants_have_provider_id() -> None:
    normalized = _normalize()
    for participant in normalized["participants"]:
        assert "provider_id" in participant, (
            f"Missing provider_id in {participant['participant_id']}"
        )
        assert participant["provider_id"] is None


def test_participants_have_provider_metadata() -> None:
    normalized = _normalize()
    for participant in normalized["participants"]:
        assert "provider_metadata" in participant, (
            f"Missing provider_metadata in {participant['participant_id']}"
        )
        assert isinstance(participant["provider_metadata"], dict)
