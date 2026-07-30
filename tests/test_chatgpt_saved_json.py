"""Tests for hardened saved-json shape detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mindcap.core.errors import UnsupportedConversationSchemaError
from mindcap.core.models import CaptureRequest
from mindcap.plugins.chatgpt.strategies.saved_json import SavedJsonCaptureStrategy

FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt"
BRANCHING = FIXTURES / "branching-conversation.json"


def _make_request(path: Path, tmp_path: Path) -> CaptureRequest:
    return CaptureRequest(
        source_type="chatgpt",
        source=str(path),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_single_conversation_object_accepted(tmp_path: Path) -> None:
    strategy = SavedJsonCaptureStrategy()
    request = _make_request(BRANCHING, tmp_path)
    envelope = strategy.capture(request)
    assert envelope.strategy == "saved-json"
    assert envelope.safe_metadata["detected_shape"] == "single-conversation"


def test_array_of_conversations_accepted(tmp_path: Path) -> None:
    conv_array = json.loads(BRANCHING.read_bytes())
    array_file = tmp_path / "conversations.json"
    array_file.write_text(json.dumps([conv_array]), encoding="utf-8")
    strategy = SavedJsonCaptureStrategy()
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(array_file),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )
    envelope = strategy.capture(request)
    assert "conversation-array" in envelope.safe_metadata["detected_shape"]


def test_wrapped_conversation_accepted(tmp_path: Path) -> None:
    inner = json.loads(BRANCHING.read_bytes())
    wrapped = {"conversation": inner}
    wrapped_file = tmp_path / "wrapped.json"
    wrapped_file.write_text(json.dumps(wrapped), encoding="utf-8")
    strategy = SavedJsonCaptureStrategy()
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(wrapped_file),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )
    envelope = strategy.capture(request)
    assert envelope.strategy == "saved-json"


# ---------------------------------------------------------------------------
# Rejection of unsupported shapes
# ---------------------------------------------------------------------------


def test_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{not valid json")
    strategy = SavedJsonCaptureStrategy()
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(bad),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )
    with pytest.raises(UnsupportedConversationSchemaError, match="Cannot parse"):
        strategy.capture(request)


def test_dict_without_mapping_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"some_key": "value"}), encoding="utf-8")
    strategy = SavedJsonCaptureStrategy()
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(bad),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )
    with pytest.raises(UnsupportedConversationSchemaError, match="Detected shape"):
        strategy.capture(request)


def test_non_object_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("42", encoding="utf-8")
    strategy = SavedJsonCaptureStrategy()
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(bad),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )
    with pytest.raises(UnsupportedConversationSchemaError, match="not a JSON object"):
        strategy.capture(request)


def test_error_message_contains_filename(tmp_path: Path) -> None:
    bad = tmp_path / "my-conversation.json"
    bad.write_text("{}", encoding="utf-8")
    strategy = SavedJsonCaptureStrategy()
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(bad),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )
    with pytest.raises(UnsupportedConversationSchemaError) as exc_info:
        strategy.capture(request)
    assert "my-conversation.json" in str(exc_info.value)


def test_nonexistent_file_raises_capture_failed(tmp_path: Path) -> None:
    from mindcap.core.errors import CaptureFailedError

    strategy = SavedJsonCaptureStrategy()
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(tmp_path / "nonexistent.json"),
        provider="chatgpt",
        canonical_identifier="6a14b69f-7834-83ea-8257-0eceadb41691",
        canonical_url=None,
        strategy="saved-json",
        artifact_root=tmp_path,
    )
    with pytest.raises(CaptureFailedError):
        strategy.capture(request)
