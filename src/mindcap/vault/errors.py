from __future__ import annotations

from mindcap.core.errors import MindcapError


class VaultError(MindcapError):
    """Raised when a vault operation cannot proceed safely."""


class UnsupportedVaultFormatError(VaultError):
    """Raised when an on-disk vault format is unsupported."""


class VaultLockError(VaultError):
    """Raised when another writer owns the vault lock."""


class StaleVaultLockError(VaultLockError):
    """Raised when a stale vault lock requires explicit recovery."""


class SourceMutationError(VaultError):
    """Raised when a source file changes during hashing or packing."""


class CatalogIntegrityError(VaultError):
    """Raised when a catalog fails integrity or foreign-key validation."""


class UnsafeRestorePathError(VaultError):
    """Raised when a restore target would escape the destination root."""
