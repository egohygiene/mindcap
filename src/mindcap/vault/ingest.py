from __future__ import annotations

import hashlib
from pathlib import Path

from mindcap.vault.errors import SourceMutationError

_CHUNK_SIZE = 1024 * 1024


def hash_source_file(path: Path) -> tuple[str, int]:
    stat_before = path.stat()
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
            total += len(chunk)
    stat_after = path.stat()
    if (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
    ):
        raise SourceMutationError(f'Source file changed during hashing: "{path}"')
    return (digest.hexdigest(), total)
