from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

from mindcap.vault.errors import CatalogIntegrityError
from mindcap.vault.layout import create_staging_directory
from mindcap.vault.models import CatalogRecord, PackSeal, PlannedArchive, VaultMetadata

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vault_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS catalog_generations (
            generation INTEGER PRIMARY KEY,
            previous_generation INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(previous_generation) REFERENCES catalog_generations(generation)
        );
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            run_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            source_label TEXT,
            dry_run INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            imported_count INTEGER NOT NULL,
            already_present_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS archive_units (
            archive_unit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            source_id TEXT NOT NULL,
            capture_version TEXT NOT NULL,
            title TEXT,
            captured_at TEXT,
            bundle_root TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            import_run_id TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            UNIQUE(provider, source_id, capture_version),
            FOREIGN KEY(import_run_id) REFERENCES ingestion_runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS provider_records (
            provider_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_unit_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            record_type TEXT NOT NULL,
            external_id TEXT NOT NULL,
            parent_external_id TEXT,
            title TEXT,
            created_at TEXT,
            updated_at TEXT,
            captured_at TEXT,
            payload_json TEXT NOT NULL,
            FOREIGN KEY(archive_unit_id) REFERENCES archive_units(archive_unit_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_provider_records_lookup
            ON provider_records(provider, record_type, external_id);
        CREATE TABLE IF NOT EXISTS objects (
            sha256 TEXT PRIMARY KEY,
            byte_size INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS packs (
            pack_id TEXT PRIMARY KEY,
            pack_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            member_count INTEGER NOT NULL,
            sealed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS object_locations (
            object_sha256 TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL,
            member_name TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            FOREIGN KEY(object_sha256) REFERENCES objects(sha256),
            FOREIGN KEY(pack_id) REFERENCES packs(pack_id)
        );
        CREATE TABLE IF NOT EXISTS archive_files (
            archive_unit_id INTEGER NOT NULL,
            relative_path TEXT NOT NULL,
            object_sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            PRIMARY KEY(archive_unit_id, relative_path),
            FOREIGN KEY(archive_unit_id) REFERENCES archive_units(archive_unit_id) ON DELETE CASCADE,
            FOREIGN KEY(object_sha256) REFERENCES objects(sha256)
        );
        """,
    ),
)



def connect_database(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn



def apply_migrations(conn: sqlite3.Connection, metadata: VaultMetadata) -> None:
    for version, statement in _MIGRATIONS:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
        if row is not None:
            continue
        conn.executescript(statement)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, datetime('now'))",
            (version,),
        )
    for key, value in metadata.to_dict().items():
        conn.execute(
            "INSERT OR REPLACE INTO vault_metadata(key, value) VALUES(?, ?)",
            (key, str(value) if value is not None else ""),
        )
    conn.commit()



def prepare_staged_database(
    vault_metadata: VaultMetadata,
    latest_catalog: Path | None,
    staging_directory: Path | None,
) -> tuple[Path, Path]:
    workdir = create_staging_directory(staging_directory)
    staged_db = workdir / "catalog.sqlite3"
    if latest_catalog is not None:
        shutil.copy2(latest_catalog, staged_db)
    conn = connect_database(staged_db)
    try:
        apply_migrations(conn, vault_metadata)
    finally:
        conn.close()
    return workdir, staged_db



def load_imported_units(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT provider, source_id, capture_version FROM archive_units"
    ).fetchall()
    return {
        (str(row["provider"]), str(row["source_id"]), str(row["capture_version"]))
        for row in rows
    }



def load_object_locations(conn: sqlite3.Connection) -> dict[str, tuple[str, str, int]]:
    rows = conn.execute(
        "SELECT object_sha256, pack_id, member_name, byte_size FROM object_locations"
    ).fetchall()
    return {
        str(row["object_sha256"]): (
            str(row["pack_id"]),
            str(row["member_name"]),
            int(row["byte_size"]),
        )
        for row in rows
    }



def next_generation(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(generation), 0) AS generation FROM catalog_generations").fetchone()
    assert row is not None
    return int(row["generation"]) + 1



def insert_generation(
    conn: sqlite3.Connection,
    generation: int,
    previous_generation: int | None,
    created_at: str,
) -> None:
    conn.execute(
        "INSERT INTO catalog_generations(generation, previous_generation, created_at) VALUES(?, ?, ?)",
        (generation, previous_generation, created_at),
    )



def insert_ingestion_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    provider: str,
    source_label: str | None,
    dry_run: bool,
    created_at: str,
    imported_count: int,
    already_present_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO ingestion_runs(
            run_id, provider, source_label, dry_run, created_at, imported_count, already_present_count
        ) VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            provider,
            source_label,
            1 if dry_run else 0,
            created_at,
            imported_count,
            already_present_count,
        ),
    )



def record_pack_seal(conn: sqlite3.Connection, seal: PackSeal) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO packs(pack_id, pack_path, sha256, byte_size, member_count, sealed_at)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            seal.pack_id,
            f"packs/{seal.pack_file}",
            seal.sha256,
            seal.byte_size,
            seal.member_count,
            seal.created_at,
        ),
    )
    for item in seal.objects:
        conn.execute(
            "INSERT OR IGNORE INTO objects(sha256, byte_size) VALUES(?, ?)",
            (item.sha256, item.byte_size),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO object_locations(object_sha256, pack_id, member_name, byte_size)
            VALUES(?, ?, ?, ?)
            """,
            (item.sha256, seal.pack_id, item.member_name, item.byte_size),
        )



def insert_archive(conn: sqlite3.Connection, archive: PlannedArchive, run_id: str, imported_at: str) -> None:
    cursor = conn.execute(
        """
        INSERT INTO archive_units(
            provider, source_id, capture_version, title, captured_at, bundle_root,
            manifest_json, import_run_id, imported_at
        ) VALUES(?, ?, ?, ?, ?, ?, json(?), ?, ?)
        """,
        (
            archive.descriptor.provider,
            archive.descriptor.source_id,
            archive.descriptor.capture_version,
            archive.descriptor.title,
            archive.descriptor.captured_at,
            archive.descriptor.bundle_root,
            _json_dumps(archive.descriptor.manifest),
            run_id,
            imported_at,
        ),
    )
    archive_unit_id = int(cursor.lastrowid)
    for record in archive.records:
        insert_provider_record(conn, archive_unit_id, record)
    for file in archive.files:
        conn.execute(
            """
            INSERT INTO archive_files(archive_unit_id, relative_path, object_sha256, byte_size)
            VALUES(?, ?, ?, ?)
            """,
            (
                archive_unit_id,
                file.relative_path,
                file.sha256,
                file.byte_size,
            ),
        )



def insert_provider_record(
    conn: sqlite3.Connection,
    archive_unit_id: int,
    record: CatalogRecord,
) -> None:
    conn.execute(
        """
        INSERT INTO provider_records(
            archive_unit_id, provider, record_type, external_id, parent_external_id,
            title, created_at, updated_at, captured_at, payload_json
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, json(?))
        """,
        (
            archive_unit_id,
            record.provider,
            record.record_type,
            record.external_id,
            record.parent_external_id,
            record.title,
            record.created_at,
            record.updated_at,
            record.captured_at,
            _json_dumps(record.payload),
        ),
    )



def validate_database(conn: sqlite3.Connection) -> None:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise CatalogIntegrityError(f"Catalog integrity check failed: {integrity}")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise CatalogIntegrityError(f"Catalog foreign-key check failed: {violations}")



def summarize(conn: sqlite3.Connection) -> dict[str, Any]:
    queries = {
        "archive_units": "SELECT COUNT(*) FROM archive_units",
        "provider_records": "SELECT COUNT(*) FROM provider_records",
        "logical_bytes": "SELECT COALESCE(SUM(byte_size), 0) FROM archive_files",
        "physical_bytes": "SELECT COALESCE(SUM(byte_size), 0) FROM packs",
        "pack_count": "SELECT COUNT(*) FROM packs",
        "object_count": "SELECT COUNT(*) FROM objects",
        "latest_generation": "SELECT COALESCE(MAX(generation), 0) FROM catalog_generations",
    }
    values: dict[str, Any] = {}
    for key, query in queries.items():
        row = conn.execute(query).fetchone()
        values[key] = int(row[0]) if row is not None else 0
    rows = conn.execute("SELECT DISTINCT provider FROM archive_units ORDER BY provider").fetchall()
    values["providers"] = tuple(str(row[0]) for row in rows)
    last_import = conn.execute(
        "SELECT created_at FROM ingestion_runs WHERE dry_run = 0 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    values["last_successful_import"] = (
        str(last_import[0]) if last_import is not None else None
    )
    return values



def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
