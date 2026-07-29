from __future__ import annotations

from pathlib import Path, PurePosixPath

from mindcap.core.errors import VerificationError


def source_root(artifact_root: Path, provider: str, source_id: str) -> Path:
    return artifact_root / "workspaces" / provider / source_id


def bundle_path(artifact_root: Path, provider: str, source_id: str, version: int) -> Path:
    return source_root(artifact_root, provider, source_id) / f"v{version}"


def safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise VerificationError(f'Unsafe relative asset path: "{value}"')
    return path.as_posix()
