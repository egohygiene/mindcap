from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FORMAT_ID = "mindcap.vault/v1"
FORMAT_VERSION = 1
HASH_ALGORITHM = "sha256"
DEFAULT_PACK_SIZE_MIB = 512


@dataclass(frozen=True)
class VaultMetadata:
    vault_id: str
    format: str
    format_version: int
    created_at: str
    hashing_algorithm: str
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VaultMetadata:
        return cls(
            vault_id=str(value["vault_id"]),
            format=str(value["format"]),
            format_version=int(value["format_version"]),
            created_at=str(value["created_at"]),
            hashing_algorithm=str(value["hashing_algorithm"]),
            provider=(str(value["provider"]) if value.get("provider") else None),
        )


@dataclass(frozen=True)
class ArchiveDescriptor:
    provider: str
    source_id: str
    capture_version: str
    title: str | None
    captured_at: str | None
    bundle_root: str
    manifest: dict[str, Any]

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.provider, self.source_id, self.capture_version)


@dataclass(frozen=True)
class ArchiveFile:
    absolute_path: Path
    relative_path: str
    byte_size: int


@dataclass(frozen=True)
class CatalogRecord:
    provider: str
    record_type: str
    external_id: str
    parent_external_id: str | None
    title: str | None
    created_at: str | None
    updated_at: str | None
    captured_at: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class PlannedFile:
    absolute_path: Path
    relative_path: str
    byte_size: int
    sha256: str
    needs_write: bool


@dataclass(frozen=True)
class PlannedArchive:
    bundle_path: Path
    descriptor: ArchiveDescriptor
    files: tuple[PlannedFile, ...]
    records: tuple[CatalogRecord, ...]


@dataclass(frozen=True)
class PackObject:
    sha256: str
    member_name: str
    byte_size: int


@dataclass(frozen=True)
class PackSeal:
    schema: str
    pack_id: str
    pack_file: str
    created_at: str
    sha256: str
    byte_size: int
    member_count: int
    objects: tuple[PackObject, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "pack_id": self.pack_id,
            "pack_file": self.pack_file,
            "created_at": self.created_at,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "member_count": self.member_count,
            "objects": [asdict(item) for item in self.objects],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PackSeal:
        objects = tuple(
            PackObject(
                sha256=str(item["sha256"]),
                member_name=str(item["member_name"]),
                byte_size=int(item["byte_size"]),
            )
            for item in value.get("objects", [])
            if isinstance(item, dict)
        )
        return cls(
            schema=str(value["schema"]),
            pack_id=str(value["pack_id"]),
            pack_file=str(value["pack_file"]),
            created_at=str(value["created_at"]),
            sha256=str(value["sha256"]),
            byte_size=int(value["byte_size"]),
            member_count=int(value["member_count"]),
            objects=objects,
        )


@dataclass(frozen=True)
class CatalogSeal:
    schema: str
    generation: int
    catalog_file: str
    created_at: str
    sha256: str
    byte_size: int
    previous_generation: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CatalogSeal:
        return cls(
            schema=str(value["schema"]),
            generation=int(value["generation"]),
            catalog_file=str(value["catalog_file"]),
            created_at=str(value["created_at"]),
            sha256=str(value["sha256"]),
            byte_size=int(value["byte_size"]),
            previous_generation=(
                int(value["previous_generation"])
                if value.get("previous_generation") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class IngestSummary:
    vault_path: Path
    dry_run: bool
    planning_mode: str
    discovered_archives: int
    imported_archives: int
    already_present_archives: int
    files_examined: int
    unique_objects_added: int
    objects_deduplicated: int
    logical_source_bytes: int
    physical_bytes_written: int
    pack_files_created: int
    catalog_generation_published: int | None
    import_receipt_path: Path | None
    source_data_modified_or_deleted: str = "no"
    warning: str = (
        "Successful filesystem verification does not confirm that a Google Drive "
        "client has finished remote synchronization."
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["vault_path"] = str(self.vault_path)
        data["import_receipt_path"] = (
            str(self.import_receipt_path) if self.import_receipt_path else None
        )
        return data


@dataclass(frozen=True)
class InspectSummary:
    vault_path: Path
    vault_id: str
    format: str
    format_version: int
    latest_generation: int | None
    providers: tuple[str, ...]
    archive_units: int
    provider_records: int
    logical_bytes: int
    physical_bytes: int
    deduplicated_bytes_saved: int
    pack_count: int
    incomplete_artifacts: tuple[str, ...] = ()
    orphaned_sealed_packs: tuple[str, ...] = ()
    last_successful_import: str | None = None
    verification_status: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["vault_path"] = str(self.vault_path)
        return data


@dataclass(frozen=True)
class VerifySummary:
    vault_path: Path
    latest_generation: int | None
    deep: bool
    pack_count: int
    object_count: int
    archive_units: int
    incomplete_artifacts: tuple[str, ...]
    orphaned_sealed_packs: tuple[str, ...]
    valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["vault_path"] = str(self.vault_path)
        return data


@dataclass(frozen=True)
class RestoreSummary:
    vault_path: Path
    destination: Path
    restored_bundle_path: Path
    restored_files: int
    receipt_path: Path

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["vault_path"] = str(self.vault_path)
        data["destination"] = str(self.destination)
        data["restored_bundle_path"] = str(self.restored_bundle_path)
        data["receipt_path"] = str(self.receipt_path)
        return data


@dataclass(frozen=True)
class LockInfo:
    owner: str
    created_at: str
    pid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackIndex:
    object_locations: dict[str, tuple[str, str, int]] = field(default_factory=dict)
    pack_seals: dict[str, PackSeal] = field(default_factory=dict)
