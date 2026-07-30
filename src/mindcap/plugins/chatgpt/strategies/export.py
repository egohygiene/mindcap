"""Official ChatGPT export ingestion strategy.

Supports:
- Official ChatGPT data-export ZIP archives
- Already-extracted export directories
- A single conversations.json file
- Numbered export partitions (conversations_000.json, conversations_001.json, …)

All operations are offline and require no ChatGPT credentials.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mindcap.core.errors import (
    CaptureFailedError,
    MalformedZipError,
    MissingConversationIdError,
    UnsafeZipEntryError,
    UnsupportedConversationSchemaError,
    UnsupportedExportError,
)
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.plugins.chatgpt.identifiers import CHATGPT_IDENTIFIER

# Maximum decompressed size per ZIP entry before we abort (1 GiB).
_MAX_ENTRY_BYTES = 1 << 30

# Pattern for numbered conversation partition files.
_PARTITION_RE = re.compile(r"^conversations(?:_\d+)?\.json$", re.IGNORECASE)

# Known top-level export metadata filenames.
_EXPORT_METADATA_FILES = frozenset(
    {
        "user.json",
        "shared_conversations.json",
        "message_feedback.json",
        "model_comparisons.json",
    }
)


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass
class ConversationRecord:
    """A single raw conversation extracted from the export source."""

    conversation_id: str
    source_file: str
    raw_bytes: bytes
    sha256: str
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Populated lazily for optional duplicate detection.
    update_time: float | None = None


@dataclass
class ExportDiscovery:
    """Result of scanning an export source before ingestion begins."""

    source_path: str
    source_sha256: str | None  # None for directories
    export_timestamp: str | None  # From user.json when present
    conversation_files: list[str]
    total_conversations: int
    unknown_files: list[str]
    metadata_files: list[str]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class ExportCaptureStrategy:
    """Ingest a ChatGPT official export ZIP or directory offline."""

    name = "export"

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        """Return a summary envelope representing the export source.

        This method is provided so that the strategy satisfies the
        CaptureStrategy protocol.  For real batch ingestion the CLI uses
        :meth:`discover` and :meth:`iter_conversations` directly.
        """
        path = Path(request.source).expanduser().resolve()
        if not path.exists():
            raise CaptureFailedError(f'Export source does not exist: "{path}"')
        discovery = self.discover(str(path))
        summary_bytes = json.dumps(
            {
                "source_path": discovery.source_path,
                "total_conversations": discovery.total_conversations,
                "conversation_files": discovery.conversation_files,
                "unknown_files": discovery.unknown_files,
                "metadata_files": discovery.metadata_files,
                "warnings": discovery.warnings,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        return CaptureEnvelope(
            provider="chatgpt",
            source_type="export",
            canonical_identifier=request.canonical_identifier,
            canonical_url=None,
            captured_at=datetime.now(UTC),
            strategy=self.name,
            response_units=[
                RawResponseUnit(
                    unit_id="export-summary",
                    sequence=0,
                    media_type="application/json",
                    body=summary_bytes,
                )
            ],
            safe_metadata={
                "input_kind": "export",
                "total_conversations": discovery.total_conversations,
                "source_sha256": discovery.source_sha256,
            },
            warnings=list(discovery.warnings),
        )

    def discover(self, source: str) -> ExportDiscovery:
        """Scan the source and return an :class:`ExportDiscovery` summary."""
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise UnsupportedExportError(f'Export source does not exist: "{path}"')
        if path.suffix.lower() == ".zip":
            return self._discover_zip(path)
        if path.is_dir():
            return self._discover_directory(path)
        if path.suffix.lower() == ".json":
            return self._discover_single_json(path)
        raise UnsupportedExportError(
            f'Unrecognised export source (expected .zip, directory, or .json): "{path}"'
        )

    def iter_conversations(
        self,
        source: str,
        *,
        conversation_id: str | None = None,
    ) -> Iterator[ConversationRecord]:
        """Yield :class:`ConversationRecord` objects from the source.

        Parameters
        ----------
        source:
            Path to a ZIP archive, export directory, or conversations JSON.
        conversation_id:
            When provided, yield only the matching conversation.
        """
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise UnsupportedExportError(f'Export source does not exist: "{path}"')
        if path.suffix.lower() == ".zip":
            yield from self._iter_zip(path, conversation_id=conversation_id)
        elif path.is_dir():
            yield from self._iter_directory(path, conversation_id=conversation_id)
        elif path.suffix.lower() == ".json":
            yield from self._iter_json_file(
                path, str(path), conversation_id=conversation_id
            )
        else:
            raise UnsupportedExportError(f'Unrecognised export source: "{path}"')

    # ------------------------------------------------------------------
    # ZIP support
    # ------------------------------------------------------------------

    def _discover_zip(self, path: Path) -> ExportDiscovery:
        source_sha256 = _sha256_file(path)
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
        except zipfile.BadZipFile as exc:
            raise MalformedZipError(f'Cannot open export ZIP "{path}": {exc}') from exc

        return _build_discovery(
            source_path=str(path),
            source_sha256=source_sha256,
            filenames=names,
        )

    def _iter_zip(
        self,
        path: Path,
        *,
        conversation_id: str | None,
    ) -> Iterator[ConversationRecord]:
        try:
            zf_handle = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile as exc:
            raise MalformedZipError(f'Cannot open export ZIP "{path}": {exc}') from exc

        with zf_handle as zf:
            for entry_name in zf.namelist():
                basename = Path(entry_name).name
                if not _PARTITION_RE.match(basename):
                    continue
                raw = _safe_read_zip_entry(zf, entry_name)
                yield from _iter_conversations_json(
                    raw,
                    source_file=entry_name,
                    conversation_id=conversation_id,
                )

    # ------------------------------------------------------------------
    # Directory support
    # ------------------------------------------------------------------

    def _discover_directory(self, path: Path) -> ExportDiscovery:
        names = [entry.name for entry in sorted(path.iterdir()) if entry.is_file()]
        return _build_discovery(
            source_path=str(path),
            source_sha256=None,
            filenames=names,
        )

    def _iter_directory(
        self,
        path: Path,
        *,
        conversation_id: str | None,
    ) -> Iterator[ConversationRecord]:
        for file_path in sorted(path.iterdir()):
            if not file_path.is_file():
                continue
            if not _PARTITION_RE.match(file_path.name):
                continue
            raw = file_path.read_bytes()
            yield from _iter_conversations_json(
                raw,
                source_file=str(file_path),
                conversation_id=conversation_id,
            )

    # ------------------------------------------------------------------
    # Single JSON support
    # ------------------------------------------------------------------

    def _discover_single_json(self, path: Path) -> ExportDiscovery:
        # A single JSON file provided directly is always treated as a
        # conversation source regardless of its filename.
        return ExportDiscovery(
            source_path=str(path),
            source_sha256=_sha256_file(path),
            export_timestamp=None,
            conversation_files=[path.name],
            total_conversations=1,
            unknown_files=[],
            metadata_files=[],
        )

    def _iter_json_file(
        self,
        path: Path,
        source_label: str,
        *,
        conversation_id: str | None,
    ) -> Iterator[ConversationRecord]:
        raw = path.read_bytes()
        yield from _iter_conversations_json(
            raw,
            source_file=source_label,
            conversation_id=conversation_id,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_discovery(
    *,
    source_path: str,
    source_sha256: str | None,
    filenames: list[str],
) -> ExportDiscovery:
    """Classify the filenames in the source and return an ExportDiscovery."""
    conversation_files: list[str] = []
    metadata_files: list[str] = []
    unknown_files: list[str] = []
    warnings: list[str] = []

    for name in filenames:
        basename = Path(name).name
        if _PARTITION_RE.match(basename):
            conversation_files.append(name)
        elif basename in _EXPORT_METADATA_FILES:
            metadata_files.append(name)
        else:
            unknown_files.append(name)

    if not conversation_files:
        warnings.append(
            "No conversation files (conversations.json or conversations_NNN.json) "
            "found in the export source."
        )

    # Count conversations lazily (just a heuristic; exact count requires parsing).
    return ExportDiscovery(
        source_path=source_path,
        source_sha256=source_sha256,
        export_timestamp=None,
        conversation_files=sorted(conversation_files),
        total_conversations=len(conversation_files),
        unknown_files=unknown_files,
        metadata_files=metadata_files,
        warnings=warnings,
    )


def _iter_conversations_json(
    raw: bytes,
    source_file: str,
    *,
    conversation_id: str | None = None,
) -> Iterator[ConversationRecord]:
    """Parse a conversations JSON buffer and yield ConversationRecord objects."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UnsupportedConversationSchemaError(
            f'Cannot parse conversation JSON from "{source_file}": {exc}. '
            f"Source filename: {source_file!r}. "
            f"Check that the file is valid UTF-8 JSON."
        ) from exc

    conversations: list[Any]
    if isinstance(payload, list):
        conversations = payload
    elif isinstance(payload, dict) and isinstance(payload.get("mapping"), dict):
        # Single conversation object.
        conversations = [payload]
    elif isinstance(payload, dict):
        raise UnsupportedConversationSchemaError(
            f'Unsupported JSON shape in "{source_file}": expected a conversation '
            f'array or a single conversation object with a "mapping" field. '
            f'Detected shape: dict without "mapping". '
            f"Source filename: {source_file!r}."
        )
    else:
        raise UnsupportedConversationSchemaError(
            f'Unsupported JSON shape in "{source_file}": expected array or object, '
            f"got {type(payload).__name__!r}. "
            f"Source filename: {source_file!r}."
        )

    for item in conversations:
        if not isinstance(item, dict):
            continue
        conv_id = str(item.get("id") or item.get("conversation_id") or "").strip()
        if not conv_id or not CHATGPT_IDENTIFIER.fullmatch(conv_id):
            raise MissingConversationIdError(
                f'Conversation in "{source_file}" has no valid UUID identifier. '
                f"Found id={item.get('id')!r}. "
                f"Source filename: {source_file!r}. "
                f"Provide the conversation ID explicitly or verify the export."
            )
        if conversation_id is not None and conv_id.lower() != conversation_id.lower():
            continue
        raw_conv_bytes = json.dumps(item, ensure_ascii=False).encode("utf-8")
        sha256 = hashlib.sha256(raw_conv_bytes).hexdigest()
        update_time: float | None = None
        with contextlib.suppress(TypeError, ValueError):
            raw_ut = item.get("update_time")
            update_time = float(raw_ut) if raw_ut is not None else None
        yield ConversationRecord(
            conversation_id=conv_id.lower(),
            source_file=source_file,
            raw_bytes=raw_conv_bytes,
            sha256=sha256,
            update_time=update_time,
        )


def _safe_read_zip_entry(zf: zipfile.ZipFile, entry_name: str) -> bytes:
    """Read a ZIP entry safely, preventing path traversal and zip bombs."""
    _assert_safe_zip_entry(entry_name)
    info = zf.getinfo(entry_name)
    if info.file_size > _MAX_ENTRY_BYTES:
        raise UnsafeZipEntryError(
            f'ZIP entry "{entry_name}" decompressed size '
            f"({info.file_size:,} bytes) exceeds the safety limit "
            f"({_MAX_ENTRY_BYTES:,} bytes)."
        )
    with zf.open(entry_name) as fh:
        reader = io.BufferedReader(fh)  # type: ignore[arg-type]
        buf = bytearray()
        while True:
            chunk = reader.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > _MAX_ENTRY_BYTES:
                raise UnsafeZipEntryError(
                    f'ZIP entry "{entry_name}" exceeded the decompression '
                    f"safety limit during streaming."
                )
        return bytes(buf)


def _assert_safe_zip_entry(entry_name: str) -> None:
    """Raise UnsafeZipEntryError if the entry name could escape extraction dir."""
    # Absolute paths
    if entry_name.startswith("/") or (len(entry_name) >= 2 and entry_name[1] == ":"):
        raise UnsafeZipEntryError(f'ZIP entry "{entry_name}" uses an absolute path.')
    # Path traversal
    norm = Path(entry_name)
    for part in norm.parts:
        if part == "..":
            raise UnsafeZipEntryError(
                f'ZIP entry "{entry_name}" contains a path traversal sequence.'
            )
    # Null bytes
    if "\x00" in entry_name:
        raise UnsafeZipEntryError(
            f"ZIP entry name contains a null byte: {entry_name!r}."
        )


def _sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file, streaming to avoid large allocations."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def _temp_extract_dir() -> Iterator[Path]:
    """Context manager that creates and cleans up a private temporary directory."""
    tmp = Path(tempfile.mkdtemp(prefix="mindcap-export-"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
