from __future__ import annotations

from pathlib import Path, PurePosixPath

from mindcap.core.errors import VerificationError


def source_root(artifact_root: Path, provider: str, source_id: str) -> Path:
    """Return the per-source archive root directory."""
    return artifact_root / "archives" / provider / source_id


def bundle_path(
    artifact_root: Path, provider: str, source_id: str, version: int
) -> Path:
    """Return the versioned bundle directory path."""
    return source_root(artifact_root, provider, source_id) / f"v{version}"


def safe_relative_path(value: str) -> str:
    """Validate and return *value* as a safe POSIX relative path.

    Raises :exc:`~mindcap.core.errors.VerificationError` for absolute paths or
    path-traversal attempts.
    """
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise VerificationError(f'Unsafe relative asset path: "{value}"')
    return path.as_posix()
