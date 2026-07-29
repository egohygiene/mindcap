"""Tests for manifest and raw-index completeness per the source-capture spec."""

from __future__ import annotations

from pathlib import Path

import yaml

from mindcap.core.models import CaptureRequest
from mindcap.plugins.chatgpt.plugin import ChatGPTPlugin
from mindcap.storage.filesystem import FilesystemStorageStrategy

IDENTIFIER = "6a14b69f-7834-83ea-8257-0eceadb41691"
FIXTURE = Path(__file__).parent / "fixtures" / "chatgpt" / "branching-conversation.json"


def _request(tmp_path: Path) -> CaptureRequest:
    return CaptureRequest(
        source_type="chatgpt",
        source=str(FIXTURE),
        provider="chatgpt",
        canonical_identifier=IDENTIFIER,
        canonical_url=f"https://chatgpt.com/c/{IDENTIFIER}",
        strategy="saved-json",
        artifact_root=tmp_path,
    )


def _run_pipeline(tmp_path: Path) -> Path:
    plugin = ChatGPTPlugin()
    request = _request(tmp_path)
    envelope = plugin.strategy("saved-json").capture(request)
    normalized = plugin.normalize(envelope, IDENTIFIER)
    transcript = plugin.render(normalized)
    stored = FilesystemStorageStrategy().persist(
        request, envelope, normalized, transcript
    )
    return stored.path


# ---------------------------------------------------------------------------
# Manifest required fields
# ---------------------------------------------------------------------------


def test_manifest_has_previous_version(tmp_path: Path) -> None:
    bundle_path = _run_pipeline(tmp_path)
    manifest = yaml.safe_load((bundle_path / "manifest.yaml").read_text())
    assert "previous_version" in manifest
    assert manifest["previous_version"] is None  # v1 has no predecessor


def test_manifest_has_previous_version_on_v2(tmp_path: Path) -> None:
    """Second capture (different content) should set previous_version = 1."""
    plugin = ChatGPTPlugin()
    request = _request(tmp_path)

    # First capture
    envelope = plugin.strategy("saved-json").capture(request)
    normalized = plugin.normalize(envelope, IDENTIFIER)
    transcript = plugin.render(normalized)
    storage = FilesystemStorageStrategy()
    first = storage.persist(request, envelope, normalized, transcript)

    # Mutate the normalized content to force a new version.
    norm2 = dict(normalized)
    norm2["title"] = "Modified Title to Force New Version"
    from mindcap.plugins.chatgpt.renderer import render_chatgpt_markdown

    transcript2 = render_chatgpt_markdown(norm2)

    second = storage.persist(request, envelope, norm2, transcript2)

    assert first.version == 1
    assert second.version == 2
    manifest2 = yaml.safe_load((second.path / "manifest.yaml").read_text())
    assert manifest2["previous_version"] == 1


def test_manifest_has_redaction_ledger_path(tmp_path: Path) -> None:
    bundle_path = _run_pipeline(tmp_path)
    manifest = yaml.safe_load((bundle_path / "manifest.yaml").read_text())
    assert "redaction_ledger_path" in manifest
    assert manifest["redaction_ledger_path"] is None


def test_manifest_has_versions_block(tmp_path: Path) -> None:
    bundle_path = _run_pipeline(tmp_path)
    manifest = yaml.safe_load((bundle_path / "manifest.yaml").read_text())
    assert "versions" in manifest
    versions = manifest["versions"]
    assert isinstance(versions, dict)
    required_keys = (
        "schema",
        "normalized_schema",
        "raw_index_schema",
        "normalizer",
        "renderer",
        "canonicalizer",
    )
    for key in required_keys:
        assert key in versions, f"Missing key in versions: {key}"


def test_manifest_has_conversation_block(tmp_path: Path) -> None:
    bundle_path = _run_pipeline(tmp_path)
    manifest = yaml.safe_load((bundle_path / "manifest.yaml").read_text())
    assert "conversation" in manifest
    conv = manifest["conversation"]
    assert isinstance(conv, dict)
    assert "message_count" in conv
    assert "participant_count" in conv
    assert conv["message_count"] > 0


# ---------------------------------------------------------------------------
# Raw index required fields
# ---------------------------------------------------------------------------


def test_raw_index_has_source_id(tmp_path: Path) -> None:
    bundle_path = _run_pipeline(tmp_path)
    raw_index = yaml.safe_load((bundle_path / "raw" / "index.yaml").read_text())
    assert "source_id" in raw_index
    assert raw_index["source_id"] == f"chatgpt-{IDENTIFIER}"


def test_raw_index_has_capture_version(tmp_path: Path) -> None:
    bundle_path = _run_pipeline(tmp_path)
    raw_index = yaml.safe_load((bundle_path / "raw" / "index.yaml").read_text())
    assert "capture_version" in raw_index
    assert raw_index["capture_version"] == 1


def test_raw_index_has_security_transformations(tmp_path: Path) -> None:
    bundle_path = _run_pipeline(tmp_path)
    raw_index = yaml.safe_load((bundle_path / "raw" / "index.yaml").read_text())
    assert "security_transformations" in raw_index
    assert raw_index["security_transformations"] == 0


# ---------------------------------------------------------------------------
# Latest pointer
# ---------------------------------------------------------------------------


def test_latest_yaml_has_updated_at(tmp_path: Path) -> None:
    _run_pipeline(tmp_path)
    source_root = tmp_path / "conversations" / "chatgpt" / f"chatgpt-{IDENTIFIER}"
    latest = yaml.safe_load((source_root / "latest.yaml").read_text())
    assert "updated_at" in latest
    assert latest["updated_at"]  # non-empty string


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------


def test_version_history_has_previous_version(tmp_path: Path) -> None:
    _run_pipeline(tmp_path)
    source_root = tmp_path / "conversations" / "chatgpt" / f"chatgpt-{IDENTIFIER}"
    history = yaml.safe_load((source_root / "version-history.yaml").read_text())
    assert history["versions"][0]["previous_version"] is None
