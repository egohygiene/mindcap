from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from mindcap.vault.models import ArchiveDescriptor, ArchiveFile, CatalogRecord


class VaultArchiveAdapter(Protocol):
    provider: str

    def discover(self, source: Path) -> Iterable[Path]: ...

    def validate(self, bundle: Path) -> None: ...

    def describe(self, bundle: Path) -> ArchiveDescriptor: ...

    def iter_files(self, bundle: Path) -> Iterable[ArchiveFile]: ...

    def iter_records(self, bundle: Path) -> Iterable[CatalogRecord]: ...
