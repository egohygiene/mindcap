from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from mindcap.vault.errors import SourceMutationError, VaultError
from mindcap.vault.layout import incomplete_dir, pack_path, pack_seal_path, read_json, write_json_atomic
from mindcap.vault.models import PackIndex, PackObject, PackSeal

_CHUNK_SIZE = 1024 * 1024



def member_name_for_sha256(digest: str) -> str:
    return f"objects/{digest[:2]}/{digest[2:4]}/{digest}"



def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()



def validate_sealed_pack(pack_file: Path, seal: PackSeal) -> None:
    if not pack_file.is_file():
        raise VaultError(f'Sealed pack is missing: "{pack_file}"')
    if sha256_file(pack_file) != seal.sha256:
        raise VaultError(f'Pack seal hash mismatch: "{pack_file}"')
    with zipfile.ZipFile(pack_file, "r") as archive:
        if archive.testzip() is not None:
            raise VaultError(f'Pack ZIP validation failed: "{pack_file}"')
        names = set(archive.namelist())
    expected = {item.member_name for item in seal.objects}
    if not expected.issubset(names):
        raise VaultError(f'Pack seal members do not match ZIP contents: "{pack_file}"')



def load_pack_index(vault_path: Path) -> PackIndex:
    index = PackIndex()
    for seal_file in sorted(vault_path.joinpath("packs").glob("*.seal.json")):
        seal = PackSeal.from_dict(read_json(seal_file))
        pack_file = pack_path(vault_path, seal.pack_id)
        validate_sealed_pack(pack_file, seal)
        index.pack_seals[seal.pack_id] = seal
        for item in seal.objects:
            index.object_locations.setdefault(
                item.sha256,
                (seal.pack_id, item.member_name, item.byte_size),
            )
    return index


@dataclass
class _OpenPack:
    pack_id: str
    temporary_path: Path
    final_path: Path
    zip_handle: zipfile.ZipFile
    estimated_size: int = 0
    objects: list[PackObject] = field(default_factory=list)


class PackWriter:
    def __init__(self, vault_path: Path, run_id: str, target_size_bytes: int) -> None:
        self._vault_path = vault_path
        self._run_id = run_id
        self._target_size_bytes = target_size_bytes
        self._counter = 0
        self._current: _OpenPack | None = None
        self._sealed: list[PackSeal] = []

    def add_object(self, source_path: Path, expected_sha256: str, byte_size: int) -> tuple[str, str]:
        if (
            self._current is not None
            and self._current.objects
            and self._current.estimated_size + byte_size > self._target_size_bytes
        ):
            self._seal_current()
        if self._current is None:
            self._current = self._open_pack()
        assert self._current is not None
        member_name = member_name_for_sha256(expected_sha256)
        self._stream_file_into_zip(
            source_path=source_path,
            expected_sha256=expected_sha256,
            byte_size=byte_size,
            archive=self._current.zip_handle,
            member_name=member_name,
        )
        self._current.objects.append(
            PackObject(
                sha256=expected_sha256,
                member_name=member_name,
                byte_size=byte_size,
            )
        )
        self._current.estimated_size += byte_size
        return (self._current.pack_id, member_name)

    def finish(self) -> tuple[PackSeal, ...]:
        if self._current is not None:
            self._seal_current()
        return tuple(self._sealed)

    def _open_pack(self) -> _OpenPack:
        self._counter += 1
        pack_id = f"pack-{self._run_id}-{self._counter:06d}"
        temporary_path = incomplete_dir(self._vault_path) / f"{pack_id}.partial.zip"
        final_path = pack_path(self._vault_path, pack_id)
        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        zip_handle = zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        )
        return _OpenPack(
            pack_id=pack_id,
            temporary_path=temporary_path,
            final_path=final_path,
            zip_handle=zip_handle,
        )

    def _seal_current(self) -> None:
        assert self._current is not None
        current = self._current
        current.zip_handle.close()
        current.final_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current.temporary_path), str(current.final_path))
        created_at = datetime.now(UTC).isoformat()
        digest = sha256_file(current.final_path)
        seal = PackSeal(
            schema="mindcap.vault-pack-seal/v1",
            pack_id=current.pack_id,
            pack_file=current.final_path.name,
            created_at=created_at,
            sha256=digest,
            byte_size=current.final_path.stat().st_size,
            member_count=len(current.objects),
            objects=tuple(current.objects),
        )
        validate_sealed_pack(current.final_path, seal)
        write_json_atomic(pack_seal_path(self._vault_path, current.pack_id), seal.to_dict())
        self._sealed.append(seal)
        self._current = None

    def _stream_file_into_zip(
        self,
        *,
        source_path: Path,
        expected_sha256: str,
        byte_size: int,
        archive: zipfile.ZipFile,
        member_name: str,
    ) -> None:
        stat_before = source_path.stat()
        info = zipfile.ZipInfo(filename=member_name)
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = byte_size
        digest = hashlib.sha256()
        written = 0
        with source_path.open("rb") as source, archive.open(info, mode="w", force_zip64=True) as target:
            written = _copy_and_hash(source, target, digest)
        stat_after = source_path.stat()
        if stat_before.st_size != stat_after.st_size or stat_before.st_mtime_ns != stat_after.st_mtime_ns:
            raise SourceMutationError(f'Source file changed during ingestion: "{source_path}"')
        actual_sha256 = digest.hexdigest()
        if written != byte_size or actual_sha256 != expected_sha256:
            raise SourceMutationError(f'Source file changed during ingest planning: "{source_path}"')



def read_pack_seal(path: Path) -> PackSeal:
    return PackSeal.from_dict(read_json(path))



def _copy_and_hash(source: BinaryIO, target: BinaryIO, digest: hashlib._Hash) -> int:
    total = 0
    while chunk := source.read(_CHUNK_SIZE):
        target.write(chunk)
        digest.update(chunk)
        total += len(chunk)
    return total
