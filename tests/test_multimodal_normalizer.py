"""Tests for ChatGPT message classification, multimodal normalisation,
attachment registry, and visible-path rendering using the salmon fixture."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mindcap.core.models import CaptureEnvelope, RawResponseUnit
from mindcap.plugins.chatgpt.normalizer import normalize_chatgpt
from mindcap.plugins.chatgpt.renderer import render_chatgpt_markdown

IDENTIFIER = "6a14b69f-7834-83ea-8257-0eceadb41691"
FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "chatgpt"
    / "salmon-multimodal-conversation.json"
)

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
# Classification: structural root
# ---------------------------------------------------------------------------


def test_root_node_classified_structural() -> None:
    normalized = _normalize()
    root = normalized["messages"]["root"]
    assert root["visibility"] == "structural"
    assert root["semantic_type"] == "structural_node"
    assert root["renderable"] is False
    assert root["knowledge_eligible"] is False
    assert root["exclusion_reason"] == "no_provider_message"


def test_structural_root_content_is_empty() -> None:
    """Hidden/structural nodes must have no content in derived artifacts."""
    normalized = _normalize()
    root = normalized["messages"]["root"]
    assert root["content"] == []


# ---------------------------------------------------------------------------
# Classification: explicitly hidden nodes
# ---------------------------------------------------------------------------


def test_system_empty_nodes_classified_hidden() -> None:
    normalized = _normalize()
    for node_id in ("system-empty-1", "system-empty-2"):
        msg = normalized["messages"][node_id]
        assert msg["visibility"] == "hidden", f"{node_id} should be hidden"
        assert msg["renderable"] is False
        assert msg["knowledge_eligible"] is False


def test_user_editable_context_classified_hidden() -> None:
    normalized = _normalize()
    msg = normalized["messages"]["user-context"]
    assert msg["visibility"] == "hidden"
    assert msg["semantic_type"] == "internal_context"
    assert msg["exclusion_reason"] == "user_editable_context"
    assert msg["content"] == []


def test_model_editable_context_classified_hidden() -> None:
    normalized = _normalize()
    msg = normalized["messages"]["model-context"]
    assert msg["visibility"] == "hidden"
    assert msg["semantic_type"] == "internal_context"
    assert msg["exclusion_reason"] == "model_editable_context"
    assert msg["content"] == []


def test_contextual_system_messages_classified_hidden() -> None:
    normalized = _normalize()
    for node_id in (
        "system-context-1",
        "system-context-2",
        "system-attachment-routing",
    ):
        msg = normalized["messages"][node_id]
        assert msg["visibility"] == "hidden", f"{node_id} not hidden: {msg}"
        assert msg["knowledge_eligible"] is False


# ---------------------------------------------------------------------------
# Classification: explicitly visible nodes
# ---------------------------------------------------------------------------


def test_user_multimodal_classified_visible() -> None:
    normalized = _normalize()
    msg = normalized["messages"]["user-multimodal"]
    assert msg["visibility"] == "visible"
    assert msg["semantic_type"] == "user_message"
    assert msg["knowledge_eligible"] is True


def test_assistant_visible_classified_visible() -> None:
    normalized = _normalize()
    msg = normalized["messages"]["assistant-visible"]
    assert msg["visibility"] == "visible"
    assert msg["semantic_type"] == "assistant_message"
    assert msg["knowledge_eligible"] is True


# ---------------------------------------------------------------------------
# Message counts
# ---------------------------------------------------------------------------


def test_visible_message_count_is_two() -> None:
    """Only the user and assistant messages are visible."""
    normalized = _normalize()
    assert normalized["visible_message_count"] == 2


def test_structural_node_count() -> None:
    normalized = _normalize()
    assert normalized["structural_node_count"] == 1  # root only


def test_provider_node_count() -> None:
    """Fixture has exactly 10 nodes."""
    normalized = _normalize()
    assert normalized["provider_node_count"] == 10


def test_hidden_message_count() -> None:
    """Fixture has 7 hidden nodes (6 system/context + model-context)."""
    normalized = _normalize()
    assert normalized["hidden_message_count"] == 7


def test_knowledge_eligible_message_count_is_two() -> None:
    normalized = _normalize()
    assert normalized["knowledge_eligible_message_count"] == 2


# ---------------------------------------------------------------------------
# Normalized paths
# ---------------------------------------------------------------------------


def test_visible_selected_path_contains_only_two_messages() -> None:
    normalized = _normalize()
    vpath = normalized["visible_selected_path"]
    assert vpath is not None
    assert len(vpath) == 2
    assert "user-multimodal" in vpath
    assert "assistant-visible" in vpath


def test_provider_selected_path_contains_all_path_nodes() -> None:
    normalized = _normalize()
    ppath = normalized["provider_selected_path"]
    assert ppath is not None
    # All path nodes from root to current_node
    assert "root" in ppath
    assert "assistant-visible" in ppath
    assert len(ppath) > 2


def test_knowledge_selected_path_matches_visible_path() -> None:
    normalized = _normalize()
    assert normalized["knowledge_selected_path"] == normalized["visible_selected_path"]


def test_selected_path_backward_compat() -> None:
    """selected_path must remain as backward-compat alias for provider path."""
    normalized = _normalize()
    assert normalized["selected_path"] == normalized["provider_selected_path"]


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------


def test_participants_are_only_visible_roles() -> None:
    """Structural and hidden nodes must not contribute to participants."""
    normalized = _normalize()
    roles = {p["role"] for p in normalized["participants"]}
    # Only user and assistant are visible in this fixture.
    assert roles == {"user", "assistant"}
    assert len(normalized["participants"]) == 2


# ---------------------------------------------------------------------------
# Multimodal content parts
# ---------------------------------------------------------------------------


def test_multimodal_image_part_is_attachment_reference() -> None:
    normalized = _normalize()
    msg = normalized["messages"]["user-multimodal"]
    image_parts = [p for p in msg["content"] if p["type"] == "attachment_reference"]
    assert len(image_parts) == 1
    img = image_parts[0]
    assert img["attachment_id"] == "file_0000000091e071fd974af51a1240f6e2"
    assert img["mime_type"] == "image/webp"
    assert img["value"] is None


def test_multimodal_text_part_is_text_type() -> None:
    normalized = _normalize()
    msg = normalized["messages"]["user-multimodal"]
    text_parts = [p for p in msg["content"] if p["type"] == "text"]
    assert len(text_parts) == 1
    assert "candy salmon" in str(text_parts[0]["value"])


# ---------------------------------------------------------------------------
# Attachment registry
# ---------------------------------------------------------------------------


def test_attachments_list_is_not_empty() -> None:
    normalized = _normalize()
    assert len(normalized["attachments"]) == 1


def test_attachment_record_fields() -> None:
    normalized = _normalize()
    att = normalized["attachments"][0]
    assert att["attachment_id"] == "file_0000000091e071fd974af51a1240f6e2"
    assert att["provider_attachment_id"] == "file_0000000091e071fd974af51a1240f6e2"
    assert att["message_id"] == "user-multimodal"
    assert att["filename"] == "7508.webp"
    assert att["mime_type"] == "image/webp"
    assert att["size_bytes"] == 286873
    assert att["width"] == 1200
    assert att["height"] == 1200
    assert att["capture_status"] == "discovered"
    assert att["sha256"] is None
    assert att["archive_path"] is None


def test_attachment_warnings_not_empty() -> None:
    normalized = _normalize()
    assert len(normalized["attachment_warnings"]) == 1
    assert "file_0000000091e071fd974af51a1240f6e2" in (
        normalized["attachment_warnings"][0]
    )


# ---------------------------------------------------------------------------
# Classification fields present on all messages
# ---------------------------------------------------------------------------


def test_all_messages_have_classification_fields() -> None:
    normalized = _normalize()
    required = {
        "visibility",
        "semantic_type",
        "renderable",
        "knowledge_eligible",
        "exclusion_reason",
        "content_preserved_in_raw",
    }
    for msg_id, msg in normalized["messages"].items():
        assert required.issubset(msg.keys()), (
            f"Missing classification fields on {msg_id}: "
            f"{required - msg.keys()}"
        )


def test_hidden_nodes_content_preserved_in_raw() -> None:
    normalized = _normalize()
    for msg_id, msg in normalized["messages"].items():
        if msg["visibility"] in {"hidden", "structural"}:
            assert msg["content_preserved_in_raw"] is True, (
                f"{msg_id} should have content_preserved_in_raw=True"
            )


def test_visible_nodes_not_content_preserved_in_raw() -> None:
    normalized = _normalize()
    for msg_id, msg in normalized["messages"].items():
        if msg["visibility"] == "visible":
            assert msg["content_preserved_in_raw"] is False, (
                f"{msg_id} should have content_preserved_in_raw=False"
            )


# ---------------------------------------------------------------------------
# Renderer: visible transcript
# ---------------------------------------------------------------------------


def test_rendered_transcript_contains_user_question() -> None:
    normalized = _normalize()
    transcript = render_chatgpt_markdown(normalized)
    assert "candy salmon" in transcript


def test_rendered_transcript_contains_assistant_answer() -> None:
    normalized = _normalize()
    transcript = render_chatgpt_markdown(normalized)
    assert "salmon candy" in transcript.lower() or "smoked salmon" in transcript.lower()


def test_rendered_transcript_does_not_contain_profile_context() -> None:
    """Hidden user_editable_context must not appear in the transcript."""
    normalized = _normalize()
    transcript = render_chatgpt_markdown(normalized)
    assert "Alice" not in transcript
    assert "friendly tone" not in transcript


def test_rendered_transcript_does_not_contain_model_context() -> None:
    normalized = _normalize()
    transcript = render_chatgpt_markdown(normalized)
    assert "model internal context" not in transcript.lower()


def test_rendered_transcript_attachment_placeholder() -> None:
    """An undownloaded attachment must render as a labeled placeholder."""
    normalized = _normalize()
    transcript = render_chatgpt_markdown(normalized)
    assert "7508.webp" in transcript
    assert "unavailable" in transcript.lower()


def test_rendered_transcript_has_coverage_warning() -> None:
    normalized = _normalize()
    transcript = render_chatgpt_markdown(normalized)
    assert "Coverage Warning" in transcript or "⚠" in transcript


def test_rendered_transcript_title() -> None:
    normalized = _normalize()
    transcript = render_chatgpt_markdown(normalized)
    assert "Salmon Candy Recipe" in transcript
