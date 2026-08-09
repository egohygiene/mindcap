from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from typing import Any

from mindcap.vault.errors import UnsupportedVaultFormatError, VaultError
from mindcap.vault.models import (
    FORMAT_ID,
    FORMAT_VERSION,
    HASH_ALGORITHM,
    VaultMetadata,
)


def safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise VaultError(f'Unsafe relative path: "{value}"')
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise VaultError("Relative path must not be empty.")
    return normalized


def vault_metadata_path(vault_path: Path) -> Path:
    return vault_path / "vault.json"


def catalog_generations_dir(vault_path: Path) -> Path:
    return vault_path / "catalog" / "generations"


def catalog_path(vault_path: Path, generation: int) -> Path:
    return catalog_generations_dir(vault_path) / f"catalog-{generation:08d}.sqlite3"


def catalog_seal_path(vault_path: Path, generation: int) -> Path:
    return catalog_generations_dir(vault_path) / f"catalog-{generation:08d}.seal.json"


def packs_dir(vault_path: Path) -> Path:
    return vault_path / "packs"


def pack_path(vault_path: Path, pack_id: str) -> Path:
    return packs_dir(vault_path) / f"{pack_id}.zip"


def pack_seal_path(vault_path: Path, pack_id: str) -> Path:
    return packs_dir(vault_path) / f"{pack_id}.seal.json"


def imports_dir(vault_path: Path) -> Path:
    return vault_path / "imports"


def reports_dir(vault_path: Path) -> Path:
    return vault_path / "reports"


def incomplete_dir(vault_path: Path) -> Path:
    return vault_path / "incomplete"


def writer_lock_path(vault_path: Path) -> Path:
    return incomplete_dir(vault_path) / "writer.lock.json"


def ensure_vault_layout(vault_path: Path) -> None:
    for path in (
        catalog_generations_dir(vault_path),
        packs_dir(vault_path),
        imports_dir(vault_path),
        reports_dir(vault_path),
        incomplete_dir(vault_path),
    ):
        path.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VaultError(f'Expected a JSON object in "{path}".')
    return value


def initialize_vault(
    vault_path: Path,
    *,
    provider: str,
    created_at: str,
) -> VaultMetadata:
    vault_path.mkdir(parents=True, exist_ok=True)
    ensure_vault_layout(vault_path)
    metadata_file = vault_metadata_path(vault_path)
    if metadata_file.exists():
        return load_vault_metadata(vault_path)
    metadata = VaultMetadata(
        vault_id=f"vault-{os.urandom(8).hex()}",
        format=FORMAT_ID,
        format_version=FORMAT_VERSION,
        created_at=created_at,
        hashing_algorithm=HASH_ALGORITHM,
        provider=provider,
    )
    write_json_atomic(metadata_file, metadata.to_dict())
    return metadata


def load_vault_metadata(vault_path: Path) -> VaultMetadata:
    metadata_path = vault_metadata_path(vault_path)
    if not metadata_path.is_file():
        raise VaultError(f'Vault metadata is missing: "{metadata_path}"')
    metadata = VaultMetadata.from_dict(read_json(metadata_path))
    if metadata.format != FORMAT_ID or metadata.format_version != FORMAT_VERSION:
        raise UnsupportedVaultFormatError(
            f"Unsupported vault format: {metadata.format} v{metadata.format_version}"
        )
    if metadata.hashing_algorithm != HASH_ALGORITHM:
        raise UnsupportedVaultFormatError(
            f"Unsupported vault hashing algorithm: {metadata.hashing_algorithm}"
        )
    return metadata


def list_catalog_generations(vault_path: Path) -> list[int]:
    generations: list[int] = []
    for path in catalog_generations_dir(vault_path).glob("catalog-*.sqlite3"):
        stem = path.stem
        try:
            generations.append(int(stem.rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return sorted(generations)


def list_incomplete_artifacts(vault_path: Path) -> tuple[str, ...]:
    root = incomplete_dir(vault_path)
    if not root.exists():
        return ()
    artifacts = [
        str(path.relative_to(vault_path))
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != writer_lock_path(vault_path).name
    ]
    return tuple(artifacts)


def create_staging_directory(base: Path | None = None) -> Path:
    if base is None:
        return Path(mkdtemp(prefix="mindcap-vault-stage-"))
    base.mkdir(parents=True, exist_ok=True)
    return Path(mkdtemp(prefix="catalog-", dir=base))
