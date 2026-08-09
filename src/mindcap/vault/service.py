from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from mindcap.registry import build_registry
from mindcap.vault import catalog
from mindcap.vault.errors import (
    StaleVaultLockError,
    UnsupportedVaultFormatError,
    VaultError,
    VaultLockError,
)
from mindcap.vault.ingest import hash_source_file
from mindcap.vault.layout import (
    catalog_path,
    catalog_seal_path,
    ensure_vault_layout,
    incomplete_dir,
    initialize_vault,
    list_incomplete_artifacts,
    load_vault_metadata,
    safe_relative_path,
    vault_metadata_path,
    write_json_atomic,
    writer_lock_path,
)
from mindcap.vault.models import (
    ArchiveDescriptor,
    CatalogSeal,
    IngestSummary,
    InspectSummary,
    LockInfo,
    PackSeal,
    PlannedArchive,
    PlannedFile,
    RestoreSummary,
    VaultMetadata,
    VerifySummary,
)
from mindcap.vault.packs import PackWriter, load_pack_index, sha256_file
from mindcap.vault.protocols import VaultArchiveAdapter
from mindcap.vault.receipts import new_run_id, write_import_receipt
from mindcap.vault.restore import restore_archive_unit
from mindcap.vault.verifier import load_latest_valid_catalog, verify_vault


class VaultService:
    def __init__(
        self,
        registry_builder: Callable[[], Any] | None = None,
        *,
        stale_lock_timeout: timedelta = timedelta(hours=6),
    ) -> None:
        self._registry_builder = registry_builder or build_registry
        self._stale_lock_timeout = stale_lock_timeout

    def ingest(
        self,
        *,
        provider: str,
        source: Path,
        destination: Path,
        source_label: str | None = None,
        pack_size_mib: int = 512,
        staging_directory: Path | None = None,
        dry_run: bool = False,
    ) -> IngestSummary:
        source = source.expanduser().resolve()
        destination = destination.expanduser().resolve()
        self._validate_preflight(source, destination)
        adapter = self._adapter_for(provider)
        created_at = datetime.now(UTC).isoformat()
        lock_context = (
            self._writer_lock(destination) if not dry_run else _null_context()
        )
        with lock_context:
            has_existing_metadata = vault_metadata_path(destination).exists()
            metadata = self._load_or_initialize_metadata(
                destination, provider=provider, created_at=created_at, dry_run=dry_run
            )
            if has_existing_metadata:
                generation, latest_catalog, _seal = self._latest_catalog(destination)
            else:
                generation, latest_catalog, _seal = (None, None, None)
            existing_units: set[tuple[str, str, str]] = set()
            existing_locations: dict[str, tuple[str, str, int]] = {}
            if latest_catalog is not None:
                conn = catalog.connect_database(latest_catalog)
                try:
                    existing_units = catalog.load_imported_units(conn)
                    existing_locations = catalog.load_object_locations(conn)
                finally:
                    conn.close()
            pack_index = (
                load_pack_index(destination)
                if vault_metadata_path(destination).exists()
                else None
            )
            reusable_locations = dict(existing_locations)
            reusable_pack_seals: dict[str, PackSeal] = {}
            if pack_index is not None:
                for digest, location in pack_index.object_locations.items():
                    reusable_locations.setdefault(digest, location)
                reusable_pack_seals = dict(pack_index.pack_seals)
            discovered_paths = sorted(
                path.resolve() for path in adapter.discover(source)
            )
            planned_archives: list[PlannedArchive] = []
            discovered = 0
            already_present = 0
            files_examined = 0
            objects_deduplicated = 0
            logical_bytes = 0
            unique_objects: dict[str, tuple[Path, int]] = {}
            for bundle_path in discovered_paths:
                discovered += 1
                adapter.validate(bundle_path)
                descriptor = adapter.describe(bundle_path)
                identity = descriptor.identity
                if identity in existing_units:
                    already_present += 1
                    continue
                planned_files: list[PlannedFile] = []
                for archive_file in adapter.iter_files(bundle_path):
                    digest, byte_size = hash_source_file(archive_file.absolute_path)
                    files_examined += 1
                    logical_bytes += byte_size
                    needs_write = (
                        digest not in reusable_locations
                        and digest not in unique_objects
                    )
                    if digest in reusable_locations or digest in unique_objects:
                        objects_deduplicated += 1
                    else:
                        unique_objects[digest] = (archive_file.absolute_path, byte_size)
                    planned_files.append(
                        PlannedFile(
                            absolute_path=archive_file.absolute_path,
                            relative_path=archive_file.relative_path,
                            byte_size=byte_size,
                            sha256=digest,
                            needs_write=needs_write,
                        )
                    )
                planned_archives.append(
                    PlannedArchive(
                        bundle_path=bundle_path,
                        descriptor=descriptor,
                        files=tuple(planned_files),
                        records=tuple(adapter.iter_records(bundle_path)),
                    )
                )
            if dry_run or not planned_archives:
                return IngestSummary(
                    vault_path=destination,
                    dry_run=dry_run,
                    planning_mode="exact",
                    discovered_archives=discovered,
                    imported_archives=0,
                    already_present_archives=already_present,
                    files_examined=files_examined,
                    unique_objects_added=len(unique_objects),
                    objects_deduplicated=objects_deduplicated,
                    logical_source_bytes=logical_bytes,
                    physical_bytes_written=0,
                    pack_files_created=0,
                    catalog_generation_published=None,
                    import_receipt_path=None,
                )
            run_id = new_run_id("import")
            pack_writer = PackWriter(
                destination,
                run_id,
                max(pack_size_mib, 1) * 1024 * 1024,
            )
            created_pack_seals: tuple[PackSeal, ...] = ()
            if unique_objects:
                for digest, (path, byte_size) in sorted(unique_objects.items()):
                    pack_id, member_name = pack_writer.add_object(
                        path, digest, byte_size
                    )
                    reusable_locations[digest] = (pack_id, member_name, byte_size)
                created_pack_seals = pack_writer.finish()
            published_generation: int | None = None
            receipt_path: Path | None = None
            staging_root: Path | None = None
            try:
                staging_root, staged_db = catalog.prepare_staged_database(
                    metadata,
                    latest_catalog,
                    staging_directory,
                )
                conn = catalog.connect_database(staged_db)
                try:
                    new_generation = catalog.next_generation(conn)
                    catalog.insert_generation(
                        conn, new_generation, generation, created_at
                    )
                    catalog.insert_ingestion_run(
                        conn,
                        run_id=run_id,
                        provider=provider,
                        source_label=source_label,
                        dry_run=False,
                        created_at=created_at,
                        imported_count=len(planned_archives),
                        already_present_count=already_present,
                    )
                    referenced_pack_ids = {
                        reusable_locations[file.sha256][0]
                        for archive in planned_archives
                        for file in archive.files
                    }
                    for pack_id in sorted(referenced_pack_ids):
                        if pack_id in reusable_pack_seals:
                            catalog.record_pack_seal(conn, reusable_pack_seals[pack_id])
                    for seal in created_pack_seals:
                        catalog.record_pack_seal(conn, seal)
                    for archive in planned_archives:
                        bundle_root = safe_relative_path(archive.descriptor.bundle_root)
                        descriptor = ArchiveDescriptor(
                            provider=archive.descriptor.provider,
                            source_id=archive.descriptor.source_id,
                            capture_version=archive.descriptor.capture_version,
                            title=archive.descriptor.title,
                            captured_at=archive.descriptor.captured_at,
                            bundle_root=bundle_root,
                            manifest=archive.descriptor.manifest,
                        )
                        catalog.insert_archive(
                            conn,
                            PlannedArchive(
                                bundle_path=archive.bundle_path,
                                descriptor=descriptor,
                                files=archive.files,
                                records=archive.records,
                            ),
                            run_id,
                            created_at,
                        )
                    conn.commit()
                    catalog.validate_database(conn)
                finally:
                    conn.close()
                published_generation = self._publish_catalog_generation(
                    destination,
                    staged_db,
                    new_generation,
                    generation,
                    created_at,
                )
                receipt_path = write_import_receipt(
                    destination,
                    {
                        "run_id": run_id,
                        "provider": provider,
                        "source_label": source_label,
                        "dry_run": False,
                        "planning_mode": "exact",
                        "discovered_archives": discovered,
                        "imported_archives": len(planned_archives),
                        "already_present_archives": already_present,
                        "files_examined": files_examined,
                        "unique_objects_added": len(unique_objects),
                        "objects_deduplicated": objects_deduplicated,
                        "logical_source_bytes": logical_bytes,
                        "physical_bytes_written": sum(
                            seal.byte_size for seal in created_pack_seals
                        ),
                        "pack_files_created": len(created_pack_seals),
                        "catalog_generation_published": published_generation,
                        "source_data_modified_or_deleted": "no",
                    },
                    run_id,
                )
            finally:
                if staging_root is not None:
                    shutil.rmtree(staging_root, ignore_errors=True)
            verify_vault(destination, deep=False)
            return IngestSummary(
                vault_path=destination,
                dry_run=False,
                planning_mode="exact",
                discovered_archives=discovered,
                imported_archives=len(planned_archives),
                already_present_archives=already_present,
                files_examined=files_examined,
                unique_objects_added=len(unique_objects),
                objects_deduplicated=objects_deduplicated,
                logical_source_bytes=logical_bytes,
                physical_bytes_written=sum(
                    seal.byte_size for seal in created_pack_seals
                ),
                pack_files_created=len(created_pack_seals),
                catalog_generation_published=published_generation,
                import_receipt_path=receipt_path,
            )

    def inspect(self, vault_path: Path) -> InspectSummary:
        vault_path = vault_path.expanduser().resolve()
        metadata = load_vault_metadata(vault_path)
        generation, latest_catalog, _seal = self._latest_catalog(vault_path)
        incomplete = list_incomplete_artifacts(vault_path)
        if latest_catalog is None:
            return InspectSummary(
                vault_path=vault_path,
                vault_id=metadata.vault_id,
                format=metadata.format,
                format_version=metadata.format_version,
                latest_generation=None,
                providers=(),
                archive_units=0,
                provider_records=0,
                logical_bytes=0,
                physical_bytes=0,
                deduplicated_bytes_saved=0,
                pack_count=0,
                incomplete_artifacts=incomplete,
                orphaned_sealed_packs=(),
                last_successful_import=None,
                verification_status="empty",
            )
        conn = catalog.connect_database(latest_catalog)
        try:
            summary = catalog.summarize(conn)
            pack_rows = conn.execute(
                "SELECT pack_id FROM packs ORDER BY pack_id"
            ).fetchall()
            pack_ids = {str(row["pack_id"]) for row in pack_rows}
        finally:
            conn.close()
        pack_index = load_pack_index(vault_path)
        orphaned = tuple(sorted(set(pack_index.pack_seals) - pack_ids))
        logical_bytes = int(summary["logical_bytes"])
        physical_bytes = int(summary["physical_bytes"])
        return InspectSummary(
            vault_path=vault_path,
            vault_id=metadata.vault_id,
            format=metadata.format,
            format_version=metadata.format_version,
            latest_generation=generation,
            providers=cast(tuple[str, ...], summary["providers"]),
            archive_units=int(summary["archive_units"]),
            provider_records=int(summary["provider_records"]),
            logical_bytes=logical_bytes,
            physical_bytes=physical_bytes,
            deduplicated_bytes_saved=max(logical_bytes - physical_bytes, 0),
            pack_count=int(summary["pack_count"]),
            incomplete_artifacts=incomplete,
            orphaned_sealed_packs=orphaned,
            last_successful_import=cast(str | None, summary["last_successful_import"]),
            verification_status="valid",
        )

    def verify(self, vault_path: Path, *, deep: bool = False) -> VerifySummary:
        return verify_vault(vault_path.expanduser().resolve(), deep=deep)

    def restore(
        self,
        *,
        vault_path: Path,
        provider: str,
        source_id: str,
        capture_version: str,
        destination: Path,
        overwrite: bool = False,
    ) -> RestoreSummary:
        summary = restore_archive_unit(
            vault_path=vault_path.expanduser().resolve(),
            provider=provider,
            source_id=source_id,
            capture_version=capture_version,
            destination=destination.expanduser().resolve(),
            overwrite=overwrite,
        )
        adapter = self._adapter_for(provider)
        adapter.validate(summary.restored_bundle_path)
        return summary

    def recover_stale_lock(self, vault_path: Path) -> None:
        lock_path = writer_lock_path(vault_path)
        info = self._read_lock_info(lock_path)
        if info is None:
            return
        if not self._is_stale(info):
            raise VaultLockError("The current writer lock is still active.")
        lock_path.unlink(missing_ok=True)

    def _adapter_for(self, provider: str) -> VaultArchiveAdapter:
        registry = self._registry_builder()
        plugin = registry.get(provider)
        adapter_factory = getattr(plugin, "vault_adapter", None)
        if adapter_factory is None:
            raise VaultError(f'Provider does not expose a vault adapter: "{provider}"')
        adapter = cast(VaultArchiveAdapter, adapter_factory())
        return adapter

    def _latest_catalog(
        self, vault_path: Path
    ) -> tuple[int | None, Path | None, CatalogSeal | None]:
        return load_latest_valid_catalog(vault_path)

    def _load_or_initialize_metadata(
        self,
        destination: Path,
        *,
        provider: str,
        created_at: str,
        dry_run: bool,
    ) -> VaultMetadata:
        if vault_metadata_path(destination).exists():
            return load_vault_metadata(destination)
        if dry_run:
            from mindcap.vault.models import (
                FORMAT_ID,
                FORMAT_VERSION,
                HASH_ALGORITHM,
                VaultMetadata,
            )

            return VaultMetadata(
                vault_id="dry-run",
                format=FORMAT_ID,
                format_version=FORMAT_VERSION,
                created_at=created_at,
                hashing_algorithm=HASH_ALGORITHM,
                provider=provider,
            )
        return initialize_vault(destination, provider=provider, created_at=created_at)

    def _validate_preflight(self, source: Path, destination: Path) -> None:
        if source == destination:
            raise VaultError("Source and destination must be different paths.")
        if destination.is_relative_to(source):
            raise VaultError("Destination must not be inside the source path.")
        if source.is_relative_to(destination):
            raise VaultError("Source must not be inside the destination path.")
        if not source.exists():
            raise VaultError(f'Source path does not exist: "{source}"')

    def _publish_catalog_generation(
        self,
        vault_path: Path,
        staged_db: Path,
        generation: int,
        previous_generation: int | None,
        created_at: str,
    ) -> int:
        target = catalog_path(vault_path, generation)
        temporary = (
            incomplete_dir(vault_path) / f"catalog-{generation:08d}.partial.sqlite3"
        )
        temporary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_db, temporary)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(target)
        digest = sha256_file(target)
        write_json_atomic(
            catalog_seal_path(vault_path, generation),
            {
                "schema": "mindcap.vault-catalog-seal/v1",
                "generation": generation,
                "catalog_file": target.name,
                "created_at": created_at,
                "sha256": digest,
                "byte_size": target.stat().st_size,
                "previous_generation": previous_generation,
            },
        )
        return generation

    @contextmanager
    def _writer_lock(self, vault_path: Path) -> Iterator[None]:
        ensure_vault_layout(vault_path)
        lock_path = writer_lock_path(vault_path)
        info = LockInfo(
            owner=new_run_id("writer"),
            created_at=datetime.now(UTC).isoformat(),
            pid=os.getpid(),
        )
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            existing = self._read_lock_info(lock_path)
            if existing is not None and self._is_stale(existing):
                raise StaleVaultLockError(
                    "A stale vault writer lock was found. Recover it explicitly "
                    "before retrying."
                ) from error
            raise VaultLockError(
                "Another writer already owns the vault lock."
            ) from error
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            import json

            json.dump(info.to_dict(), handle, indent=2, sort_keys=True)
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    def _read_lock_info(self, lock_path: Path) -> LockInfo | None:
        if not lock_path.is_file():
            return None
        try:
            import json

            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        created_at = payload.get("created_at")
        owner = payload.get("owner")
        if not isinstance(created_at, str) or not isinstance(owner, str):
            return None
        pid = payload.get("pid")
        return LockInfo(
            owner=owner, created_at=created_at, pid=int(pid) if pid else None
        )

    def _is_stale(self, info: LockInfo) -> bool:
        try:
            created_at = datetime.fromisoformat(info.created_at)
        except ValueError as error:
            raise UnsupportedVaultFormatError(
                "Invalid writer lock timestamp."
            ) from error
        return datetime.now(UTC) - created_at >= self._stale_lock_timeout


@contextmanager
def _null_context() -> Iterator[None]:
    yield
