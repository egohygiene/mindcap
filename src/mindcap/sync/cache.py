"""Safe cache evaluation for the Mindcap synchronization subsystem.

An existing archive directory is *never* sufficient evidence that a source was
captured successfully.  Cache evaluation requires:

1. A finalized ``vN`` archive exists (``latest.json`` or ``latest.yaml``
   pointer is present and resolves to an existing directory).
2. The archive passes offline verification.
3. Provider and canonical identifier match.
4. The collection adapter supplies trustworthy remote revision evidence.
5. The remote revision matches the revision recorded in the local archive.

When the provider exposes no trustworthy revision evidence:

1. Perform a lightweight metadata probe when the plugin supports it.
2. Compare canonical metadata or a remote fingerprint.
3. Otherwise run the normal single-source capture and allow existing archive
   versioning to return ``unchanged``.

This is deliberately slower than trusting the directory, but avoids silently
missing remote changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mindcap.sync.models import CacheDecision, SourceDescriptor


def evaluate_cache(
    descriptor: SourceDescriptor,
    artifact_root: Path,
    archive_subdir: str,
    verify_fn: VerifyFn | None = None,
) -> CacheDecision:
    """Evaluate the local cache status for one source descriptor.

    Parameters
    ----------
    descriptor:
        The canonical source descriptor from collection discovery.
    artifact_root:
        Private artifact root directory.
    archive_subdir:
        Path component below *artifact_root* to the provider's archive
        directory (e.g. ``"workspaces/suno"``).
    verify_fn:
        Optional callable that raises on verification failure.  When
        ``None``, offline verification is skipped and the decision becomes
        ``"probe"`` or ``"capture"`` based on other evidence.

    Returns
    -------
    CacheDecision
        The evidence-backed decision with full provenance.
    """
    source_root = artifact_root / archive_subdir / descriptor.canonical_identifier
    latest_path = _find_latest_pointer(source_root)

    if latest_path is None:
        return CacheDecision(
            decision="capture",
            reason="no-local-archive",
        )

    latest = _load_latest(latest_path)
    if latest is None:
        return CacheDecision(
            decision="capture",
            reason="unreadable-latest-pointer",
        )

    bundle_path = source_root / str(latest.get("bundle_path", ""))
    if not bundle_path.is_dir():
        return CacheDecision(
            decision="capture",
            reason="latest-bundle-directory-missing",
        )

    # Attempt offline verification.
    verification_result: str = "not-checked"
    if verify_fn is not None:
        try:
            verify_fn(bundle_path)
            verification_result = "pass"
        except Exception:
            return CacheDecision(
                decision="capture",
                reason="local-archive-verification-failed",
                local_archive_version=_safe_int(latest.get("version")),
                local_content_hash=latest.get("canonical_content_hash"),
                verification_result="fail",
            )
    else:
        verification_result = "not-checked"

    local_version = _safe_int(latest.get("version"))
    local_hash = latest.get("canonical_content_hash")
    local_revision = latest.get("remote_revision")

    # Without trustworthy remote revision evidence we cannot safely skip.
    if descriptor.remote_revision is None:
        if verification_result == "not-checked":
            return CacheDecision(
                decision="capture",
                reason="no-remote-revision-and-no-verification",
                local_archive_version=local_version,
                local_content_hash=local_hash,
                verification_result=verification_result,
            )
        # Verified archive but no remote revision — probe is better than skip.
        return CacheDecision(
            decision="probe",
            reason="verified-archive-but-no-remote-revision",
            local_archive_version=local_version,
            local_content_hash=local_hash,
            verification_result=verification_result,
        )

    # Remote revision is available — compare with local.
    if local_revision is None:
        # The archive predates revision tracking; probe to be safe.
        return CacheDecision(
            decision="probe",
            reason="local-archive-has-no-recorded-revision",
            local_archive_version=local_version,
            local_content_hash=local_hash,
            remote_revision=descriptor.remote_revision,
            verification_result=verification_result,
        )

    if local_revision != descriptor.remote_revision:
        return CacheDecision(
            decision="capture",
            reason="remote-revision-changed",
            local_archive_version=local_version,
            local_content_hash=local_hash,
            remote_revision=descriptor.remote_revision,
            verification_result=verification_result,
        )

    if verification_result == "not-checked":
        # Revisions match but we have not verified — probe for confidence.
        return CacheDecision(
            decision="probe",
            reason="revision-match-but-not-verified",
            local_archive_version=local_version,
            local_content_hash=local_hash,
            remote_revision=descriptor.remote_revision,
            verification_result=verification_result,
        )

    # All five conditions satisfied — safe to skip.
    return CacheDecision(
        decision="skip",
        reason="verified-archive-with-matching-revision",
        local_archive_version=local_version,
        local_content_hash=local_hash,
        remote_revision=descriptor.remote_revision,
        verification_result=verification_result,
    )


# ---------------------------------------------------------------------------
# VerifyFn type alias
# ---------------------------------------------------------------------------

from collections.abc import Callable  # noqa: E402 — deferred import for clarity

VerifyFn = Callable[[Path], None]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_latest_pointer(source_root: Path) -> Path | None:
    """Return the path of a ``latest.json`` or ``latest.yaml`` pointer."""
    for name in ("latest.json", "latest.yaml"):
        path = source_root / name
        if path.is_file():
            return path
    return None


def _load_latest(latest_path: Path) -> dict[str, Any] | None:
    """Parse and return the latest pointer dict, or ``None`` on failure."""
    try:
        text = latest_path.read_text(encoding="utf-8")
        if latest_path.suffix == ".json":
            loaded = json.loads(text)
        else:
            import yaml  # local import — pyyaml is a project dependency

            loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return loaded
    except (OSError, json.JSONDecodeError, Exception):
        pass
    return None


def _safe_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
