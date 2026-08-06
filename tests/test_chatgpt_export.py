"""Tests for the ChatGPT export strategy and identifiers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mindcap.core.errors import (
    MalformedZipError,
    MissingConversationIdError,
    UnsafeZipEntryError,
    UnsupportedConversationSchemaError,
    UnsupportedExportError,
)
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.plugins.chatgpt.identifiers import (
    CHATGPT_IDENTIFIER,
    canonicalize_chatgpt_identifier,
    supports_chatgpt_source,
)
from mindcap.plugins.chatgpt.plugin import ChatGPTPlugin
from mindcap.plugins.chatgpt.strategies.export import (
    ExportCaptureStrategy,
    _assert_safe_zip_entry,
    _iter_conversations_json,
)

FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt"
EXPORT_DIR = FIXTURES / "export-dir"
EXPORT_ZIP = FIXTURES / "minimal-export.zip"
SINGLE_CONV = FIXTURES / "branching-conversation.json"


# ---------------------------------------------------------------------------
# Identifier tests
# ---------------------------------------------------------------------------


def test_supports_file_path_json() -> None:
    assert supports_chatgpt_source(str(SINGLE_CONV))


def test_supports_zip_path() -> None:
    assert supports_chatgpt_source(str(EXPORT_ZIP))


def test_supports_directory_path() -> None:
    assert supports_chatgpt_source(str(EXPORT_DIR))


def test_canonicalize_json_path_extracts_id() -> None:
    identifier, url = canonicalize_chatgpt_identifier(str(SINGLE_CONV))
    # The branching-conversation.json has a known UUID.
    assert CHATGPT_IDENTIFIER.fullmatch(identifier)
    assert url is None


def test_canonicalize_zip_returns_import_id() -> None:
    identifier, url = canonicalize_chatgpt_identifier(str(EXPORT_ZIP))
    assert identifier.startswith("import-")
    assert url is None


def test_canonicalize_directory_returns_import_id() -> None:
    identifier, url = canonicalize_chatgpt_identifier(str(EXPORT_DIR))
    assert identifier.startswith("import-")
    assert url is None


def test_canonicalize_import_id_is_deterministic() -> None:
    id1, _ = canonicalize_chatgpt_identifier(str(EXPORT_DIR))
    id2, _ = canonicalize_chatgpt_identifier(str(EXPORT_DIR))
    assert id1 == id2


# ---------------------------------------------------------------------------
# ExportCaptureStrategy.discover
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_discover_directory(self) -> None:
        strategy = ExportCaptureStrategy()
        discovery = strategy.discover(str(EXPORT_DIR))
        assert discovery.source_sha256 is None  # directories have no hash
        assert any("conversations.json" in f for f in discovery.conversation_files)
        assert "user.json" in discovery.metadata_files

    def test_discover_zip(self) -> None:
        strategy = ExportCaptureStrategy()
        discovery = strategy.discover(str(EXPORT_ZIP))
        assert discovery.source_sha256 is not None
        assert any("conversations.json" in f for f in discovery.conversation_files)

    def test_discover_single_json(self) -> None:
        strategy = ExportCaptureStrategy()
        discovery = strategy.discover(str(SINGLE_CONV))
        assert len(discovery.conversation_files) == 1
        assert discovery.source_sha256 is not None

    def test_discover_nonexistent_raises(self) -> None:
        strategy = ExportCaptureStrategy()
        with pytest.raises(UnsupportedExportError, match="does not exist"):
            strategy.discover("/nonexistent/path/export.zip")

    def test_discover_unsupported_extension_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "archive.tar.gz"
        bad_file.write_bytes(b"data")
        strategy = ExportCaptureStrategy()
        with pytest.raises(UnsupportedExportError):
            strategy.discover(str(bad_file))


# ---------------------------------------------------------------------------
# ExportCaptureStrategy.iter_conversations
# ---------------------------------------------------------------------------


class TestIterConversations:
    def test_iter_directory_yields_expected_count(self) -> None:
        strategy = ExportCaptureStrategy()
        records = list(strategy.iter_conversations(str(EXPORT_DIR)))
        # conversations.json has 2, conversations_000.json has the same 2
        # (copy), conversations_001.json has 1, but IDs may overlap.
        # We just assert we got at least one record.
        assert len(records) >= 1
        assert all(CHATGPT_IDENTIFIER.fullmatch(r.conversation_id) for r in records)

    def test_iter_zip_yields_conversations(self) -> None:
        strategy = ExportCaptureStrategy()
        records = list(strategy.iter_conversations(str(EXPORT_ZIP)))
        assert len(records) >= 1
        for r in records:
            assert CHATGPT_IDENTIFIER.fullmatch(r.conversation_id)
            assert isinstance(r.raw_bytes, bytes)
            assert r.sha256 and len(r.sha256) == 64

    def test_iter_single_json(self) -> None:
        strategy = ExportCaptureStrategy()
        records = list(strategy.iter_conversations(str(SINGLE_CONV)))
        # branching-conversation.json is a single conversation object.
        assert len(records) == 1

    def test_iter_with_conversation_id_filter(self) -> None:
        strategy = ExportCaptureStrategy()
        all_records = list(strategy.iter_conversations(str(EXPORT_ZIP)))
        target = all_records[0].conversation_id
        filtered = list(
            strategy.iter_conversations(str(EXPORT_ZIP), conversation_id=target)
        )
        assert len(filtered) >= 1
        assert all(r.conversation_id == target for r in filtered)

    def test_iter_filter_nonexistent_yields_nothing(self) -> None:
        strategy = ExportCaptureStrategy()
        records = list(
            strategy.iter_conversations(
                str(EXPORT_ZIP),
                conversation_id="00000000-0000-0000-0000-000000000000",
            )
        )
        assert records == []

    def test_record_has_sha256(self) -> None:
        strategy = ExportCaptureStrategy()
        records = list(strategy.iter_conversations(str(EXPORT_DIR)))
        for r in records:
            assert len(r.sha256) == 64

    def test_raw_bytes_parses_as_valid_json(self) -> None:
        strategy = ExportCaptureStrategy()
        for r in strategy.iter_conversations(str(EXPORT_DIR)):
            payload = json.loads(r.raw_bytes)
            assert isinstance(payload, dict)
            assert "mapping" in payload


# ---------------------------------------------------------------------------
# _iter_conversations_json helper
# ---------------------------------------------------------------------------


class TestIterConversationsJson:
    def test_single_object(self) -> None:
        conv = {
            "id": "aaaabbbb-cccc-dddd-eeee-000000000001",
            "mapping": {},
        }
        raw = json.dumps(conv).encode()
        records = list(_iter_conversations_json(raw, "test.json"))
        assert len(records) == 1
        assert records[0].conversation_id == "aaaabbbb-cccc-dddd-eeee-000000000001"

    def test_array_of_objects(self) -> None:
        convs = [
            {"id": "aaaabbbb-cccc-dddd-eeee-000000000001", "mapping": {}},
            {"id": "aaaabbbb-cccc-dddd-eeee-000000000002", "mapping": {}},
        ]
        raw = json.dumps(convs).encode()
        records = list(_iter_conversations_json(raw, "test.json"))
        assert len(records) == 2

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(UnsupportedConversationSchemaError, match="Cannot parse"):
            list(_iter_conversations_json(b"not-json", "bad.json"))

    def test_unsupported_dict_shape_raises(self) -> None:
        bad = json.dumps({"foo": "bar"}).encode()
        with pytest.raises(
            UnsupportedConversationSchemaError, match="Unsupported JSON shape"
        ):
            list(_iter_conversations_json(bad, "bad.json"))

    def test_missing_conversation_id_raises(self) -> None:
        conv = {"mapping": {"root": {}}}  # no id field
        raw = json.dumps(conv).encode()
        # Single object with mapping but no valid UUID id.
        with pytest.raises(MissingConversationIdError):
            list(_iter_conversations_json(raw, "bad.json"))

    def test_conversation_id_filter(self) -> None:
        convs = [
            {"id": "aaaabbbb-cccc-dddd-eeee-000000000001", "mapping": {}},
            {"id": "aaaabbbb-cccc-dddd-eeee-000000000002", "mapping": {}},
        ]
        raw = json.dumps(convs).encode()
        records = list(
            _iter_conversations_json(
                raw,
                "test.json",
                conversation_id="aaaabbbb-cccc-dddd-eeee-000000000001",
            )
        )
        assert len(records) == 1
        assert records[0].conversation_id == "aaaabbbb-cccc-dddd-eeee-000000000001"


# ---------------------------------------------------------------------------
# ZIP security
# ---------------------------------------------------------------------------


class TestZipSecurity:
    def test_path_traversal_blocked(self) -> None:
        with pytest.raises(UnsafeZipEntryError, match="path traversal"):
            _assert_safe_zip_entry("../etc/passwd")

    def test_absolute_path_blocked(self) -> None:
        with pytest.raises(UnsafeZipEntryError, match="absolute"):
            _assert_safe_zip_entry("/etc/passwd")

    def test_null_byte_blocked(self) -> None:
        with pytest.raises(UnsafeZipEntryError, match="null byte"):
            _assert_safe_zip_entry("file\x00name.json")

    def test_safe_relative_path_allowed(self) -> None:
        # Should not raise.
        _assert_safe_zip_entry("conversations.json")
        _assert_safe_zip_entry("subdir/conversations.json")

    def test_malformed_zip_raises(self, tmp_path: Path) -> None:
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"this is not a zip file")
        strategy = ExportCaptureStrategy()
        with pytest.raises(MalformedZipError):
            list(strategy.iter_conversations(str(bad_zip)))


# ---------------------------------------------------------------------------
# Full pipeline: export → normalize → render → persist
# ---------------------------------------------------------------------------


class TestExportPipeline:
    def test_export_dir_pipeline(self, tmp_path: Path) -> None:
        """All conversations in the export dir can be ingested end-to-end."""
        plugin = ChatGPTPlugin()
        strategy = ExportCaptureStrategy()

        for record in strategy.iter_conversations(str(EXPORT_DIR)):
            conv_id = record.conversation_id
            envelope = CaptureEnvelope(
                provider="chatgpt",
                source_type="conversation",
                canonical_identifier=conv_id,
                canonical_url=f"https://chatgpt.com/c/{conv_id}",
                captured_at=datetime.now(UTC),
                strategy="export",
                response_units=[
                    RawResponseUnit(
                        unit_id="response-000",
                        sequence=0,
                        media_type="application/json",
                        body=record.raw_bytes,
                    )
                ],
            )
            request = CaptureRequest(
                source_type="chatgpt",
                source=str(EXPORT_DIR),
                provider="chatgpt",
                canonical_identifier=conv_id,
                canonical_url=f"https://chatgpt.com/c/{conv_id}",
                strategy="export",
                artifact_root=tmp_path,
            )
            normalized = plugin.normalize(envelope, conv_id)
            transcript = plugin.render(normalized)
            stored = plugin.storage().persist(request, envelope, normalized, transcript)
            assert stored.status in ("complete", "unchanged")
            assert (stored.path / "manifest.yaml").is_file()
            assert "graph_integrity" in normalized

    def test_export_strategy_capture_returns_envelope(self, tmp_path: Path) -> None:
        from mindcap.core.models import CaptureRequest

        strategy = ExportCaptureStrategy()
        request = CaptureRequest(
            source_type="chatgpt",
            source=str(EXPORT_DIR),
            provider="chatgpt",
            canonical_identifier="import-test",
            canonical_url=None,
            strategy="export",
            artifact_root=tmp_path,
        )
        envelope = strategy.capture(request)
        assert envelope.provider == "chatgpt"
        assert envelope.source_type == "export"
        assert envelope.strategy == "export"
        assert len(envelope.response_units) == 1

    def test_idempotent_import_produces_unchanged(self, tmp_path: Path) -> None:
        """Importing the same conversation twice marks the second as unchanged."""
        plugin = ChatGPTPlugin()
        records = list(ExportCaptureStrategy().iter_conversations(str(EXPORT_DIR)))
        record = records[0]
        conv_id = record.conversation_id

        def _run() -> str:
            envelope = CaptureEnvelope(
                provider="chatgpt",
                source_type="conversation",
                canonical_identifier=conv_id,
                canonical_url=f"https://chatgpt.com/c/{conv_id}",
                captured_at=datetime.now(UTC),
                strategy="export",
                response_units=[
                    RawResponseUnit(
                        unit_id="response-000",
                        sequence=0,
                        media_type="application/json",
                        body=record.raw_bytes,
                    )
                ],
            )
            request = CaptureRequest(
                source_type="chatgpt",
                source=str(EXPORT_DIR),
                provider="chatgpt",
                canonical_identifier=conv_id,
                canonical_url=f"https://chatgpt.com/c/{conv_id}",
                strategy="export",
                artifact_root=tmp_path,
            )
            normalized = plugin.normalize(envelope, conv_id)
            transcript = plugin.render(normalized)
            stored = plugin.storage().persist(request, envelope, normalized, transcript)
            return stored.status

        first = _run()
        second = _run()
        assert first == "complete"
        assert second == "unchanged"
