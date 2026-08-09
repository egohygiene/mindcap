from __future__ import annotations

from dataclasses import dataclass

CHUNK_ALIGNMENT = 256 * 1024
DEFAULT_UPLOAD_CHUNK_SIZE_MIB = 16
DEFAULT_UPLOAD_CHUNK_SIZE_BYTES = DEFAULT_UPLOAD_CHUNK_SIZE_MIB * 1024 * 1024


@dataclass(frozen=True)
class UploadChunkSpec:
    size_bytes: int


def validate_chunk_size(size_bytes: int) -> UploadChunkSpec:
    if size_bytes <= 0:
        raise ValueError("Upload chunk size must be positive.")
    if size_bytes % CHUNK_ALIGNMENT != 0:
        raise ValueError("Upload chunk size must be a multiple of 256 KiB.")
    return UploadChunkSpec(size_bytes=size_bytes)
