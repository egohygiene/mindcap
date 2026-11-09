"""Explicit bookmark projection into the provider-neutral captured-source handoff."""

from __future__ import annotations

import json
import re
from pathlib import Path

from mindcap.contracts.source_bundle import (
    HandoffUnit,
    Producer,
    build_source_bundle,
    portable_member,
)
from mindcap.core.hashing import canonical_json_bytes, sha256_bytes
from mindcap.plugins.chrome_bookmarks.archive.verifier import (
    verify_chrome_bookmarks_bundle,
)


def bookmark_source_bundle(
    directory: Path,
    *,
    source_id: str,
    producer_revision: str,
    profile_identities: dict[str, str],
    previous_bundle_sha256: str | None = None,
) -> bytes:
    """Export explicit captured bytes; never discover a browser profile."""
    directory = directory.absolute()
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("HANDOFF_PATH: explicit ordinary capture directory required")
    files = {}
    total = 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ValueError("HANDOFF_PATH: capture contains an unsafe member")
        if path.is_file():
            name = portable_member(path.relative_to(directory).as_posix())
            if path.stat().st_size > 100 * 1024 * 1024:
                raise ValueError("HANDOFF_BUDGET: capture exceeds the byte limit")
            if total + path.stat().st_size > 100 * 1024 * 1024 or len(files) >= 4095:
                raise ValueError("HANDOFF_BUDGET: capture exceeds inventory bounds")
            with path.open("rb") as stream:
                content = stream.read(100 * 1024 * 1024 - total + 1)
            total += len(content)
            if total > 100 * 1024 * 1024:
                raise ValueError("HANDOFF_BUDGET: capture exceeds inventory bounds")
            files[name] = content
    if sum(map(len, files.values())) > 100 * 1024 * 1024 or len(files) > 4095:
        raise ValueError("HANDOFF_BUDGET: capture exceeds the inventory limit")
    manifest = json.loads(files["manifest.json"])
    normalized_name = portable_member(manifest["normalized_path"])
    # Validate declared paths before the historical verifier opens them.
    for field in (
        "normalized_path",
        "readme_path",
        "report_json_path",
        "raw_index_path",
    ):
        if portable_member(manifest[field]) not in files:
            raise ValueError("HANDOFF_REFERENCE: declared capture member is absent")
    checksums = json.loads(files["checksums.json"])
    for entry in checksums["files"]:
        if portable_member(entry["path"]) not in files:
            raise ValueError("HANDOFF_REFERENCE: checksummed capture member is absent")
    verify_chrome_bookmarks_bundle(directory)
    document = json.loads(files[normalized_name])
    if document["schema"] != "mindcap.chrome-bookmarks.normalized/v0.1":
        raise ValueError(
            "HANDOFF_VERSION: supported normalized bookmark capture required"
        )
    payloads = {"capture/" + name: content for name, content in files.items()}
    units = []
    normalized = set()
    seen = set()
    for index, record in enumerate(document["bookmark_records"]):
        profile = profile_identities.get(record["profile_id"])
        if not profile or not re.fullmatch(r"[a-zA-Z0-9:._-]{1,512}", profile):
            raise ValueError("HANDOFF_IDENTITY: supply logical profile identities")
        provider_id = record.get("source_guid") or record.get("source_node_id")
        basis = "observed" if provider_id else "snapshot-local"
        identity_data = (
            [profile, provider_id]
            if provider_id
            else [sha256_bytes(files[normalized_name]), index]
        )
        identity = "bookmark-" + sha256_bytes(canonical_json_bytes(identity_data))
        if identity in seen:
            raise ValueError(
                "HANDOFF_IDENTITY: ambiguous bookmark identity; retain capture"
            )
        seen.add(identity)
        # Observation time belongs to the manifest, not unchanged bookmark content.
        stable = {
            key: value
            for key, value in record.items()
            if key
            not in {"captured_at", "profile_id", "profile_name", "profile_directory"}
        }
        stable["profile_id"] = profile
        member = "normalized/" + identity + ".json"
        payloads[member] = canonical_json_bytes(stable)
        normalized.add(member)
        units.append(
            HandoffUnit(
                identity=identity,
                member=member,
                media_type="application/json",
                locators=("json:/title", "json:/url"),
                identity_basis=basis,
                original_members=("capture/" + normalized_name,),
            )
        )
    warnings = ("CAPTURE_WARNINGS_PRESENT",) if document.get("warnings") else ()
    return build_source_bundle(
        source_id=source_id,
        source_type="bookmark-collection",
        producer=Producer(version="0.1.0", revision=producer_revision),
        captured_at=document["captured_at"],
        status="partial" if warnings else "complete",
        files=payloads,
        normalized_members=normalized,
        units=tuple(units),
        previous_bundle_sha256=previous_bundle_sha256,
        warnings=warnings,
    )
