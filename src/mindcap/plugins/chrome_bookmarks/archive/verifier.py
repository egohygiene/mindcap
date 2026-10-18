from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_file

_ARTIFACT_PATHS = {
    "readme_path": "README.md",
    "report_json_path": "reports/capture-report.json",
    "normalized_path": "normalized/bookmarks.json",
    "raw_index_path": "raw/index.json",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _artifact_path(root: Path, value: object) -> Path:
    # Reject alternate spellings as well as traversal, including Windows paths
    # when inspecting a bundle on another platform. Never echo private values.
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
    ):
        raise VerificationError("Bundle contains an unsafe artifact path.")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != value
        or value == "."
    ):
        raise VerificationError("Bundle contains an unsafe artifact path.")
    candidate = root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise VerificationError("Bundle artifact paths must not use symlinks.")
    if not candidate.is_file():
        raise VerificationError("A required bundle artifact is missing.")
    return candidate


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError("Bundle JSON contains duplicate fields.")
        result[key] = value
    return result


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise VerificationError("Bundle artifact could not be read as JSON.") from exc
    if not isinstance(value, dict):
        raise VerificationError("Bundle JSON must contain an object.")
    return value


def _integer(value: object, *, minimum: int = 0) -> TypeGuard[int]:
    return type(value) is int and value >= minimum


def _verify_normalized(normalized: dict[str, Any], manifest: dict[str, Any]) -> None:
    if normalized.get("schema") != "mindcap.chrome-bookmarks.normalized/v0.1":
        raise VerificationError("Unsupported Chrome Bookmarks normalized schema.")
    for field in ("provider", "source_type", "source_id", "canonical_identifier"):
        if normalized.get(field) != manifest[field]:
            raise VerificationError(
                "Bundle normalized identity does not match manifest."
            )
    summary = normalized.get("summary")
    if not isinstance(summary, dict):
        raise VerificationError("Bundle normalized summary is invalid.")
    for field, count_field in (
        ("profiles", "profile_count"),
        ("bookmark_records", "bookmark_count"),
        ("folder_records", "folder_count"),
    ):
        records = normalized.get(field)
        if (
            not isinstance(records, list)
            or not all(isinstance(record, dict) for record in records)
            or not _integer(summary.get(count_field))
            or summary[count_field] != len(records)
        ):
            raise VerificationError("Bundle normalized records or counts are invalid.")


def verify_chrome_bookmarks_bundle(bundle_path: Path) -> None:
    """Verify a saved Chrome capture before a consumer reads its private records."""
    try:
        if bundle_path.is_symlink() or not bundle_path.is_dir():
            raise VerificationError("Bundle must be a directory without symlinks.")
        root = bundle_path.resolve(strict=True)
        manifest = _read_object(_artifact_path(root, "manifest.json"))
        if (
            manifest.get("schema") != "mindcap.chrome-bookmarks.archive/v0.1"
            or manifest.get("provider") != "chrome-bookmarks"
            or manifest.get("source_type") != "bookmark-collection"
            or not isinstance(manifest.get("source_id"), str)
            or not manifest["source_id"]
            or not isinstance(manifest.get("canonical_identifier"), str)
            or not manifest["canonical_identifier"]
            or not _integer(manifest.get("capture_version"), minimum=1)
            or not _integer(manifest.get("raw_unit_count"))
        ):
            raise VerificationError("Invalid Chrome Bookmarks bundle manifest.")
        if manifest.get("checksums_path") != "checksums.json":
            raise VerificationError("Bundle must declare its checksums artifact.")

        paths = {
            key: _artifact_path(root, manifest.get(key)) for key in _ARTIFACT_PATHS
        }
        declared = {manifest[key] for key in _ARTIFACT_PATHS}
        if len(declared) != len(_ARTIFACT_PATHS) or declared & {
            "manifest.json",
            "checksums.json",
        }:
            raise VerificationError("Bundle artifact declarations must be distinct.")
        # v0.1 consumers use the fixed storage layout. Bind the declarations to
        # those exact artifacts so a checked alternate cannot hide an unchecked
        # canonical file that a downstream consumer would subsequently read.
        if any(manifest[key] != value for key, value in _ARTIFACT_PATHS.items()):
            raise VerificationError("Bundle artifacts must use the canonical layout.")
        checksums = _read_object(_artifact_path(root, "checksums.json"))
        entries = checksums.get("files")
        if not isinstance(entries, list) or not entries:
            raise VerificationError("Bundle checksums must list its artifacts.")
        checked: dict[str, tuple[Path, str, int]] = {}
        # Validate every declared path before hashing or parsing private artifacts.
        for entry in entries:
            if not isinstance(entry, dict):
                raise VerificationError("Bundle checksum entry is invalid.")
            path = _artifact_path(root, entry.get("path"))
            relative = entry["path"]
            digest = entry.get("sha256")
            byte_size = entry.get("byte_size")
            if (
                relative in checked
                or relative in {"manifest.json", "checksums.json"}
                or not isinstance(digest, str)
                or _SHA256.fullmatch(digest) is None
                or not _integer(byte_size)
            ):
                raise VerificationError(
                    "Bundle checksum entry is invalid or duplicated."
                )
            checked[relative] = (path, digest, byte_size)
        if not declared <= checked.keys():
            raise VerificationError("Required bundle artifacts lack checksums.")

        raw_index = _read_object(paths["raw_index_path"])
        units = raw_index.get("units")
        if (
            raw_index.get("schema") != "mindcap.chrome-bookmarks.raw-index/v0.1"
            or raw_index.get("source_id") != manifest["source_id"]
            or type(raw_index.get("capture_version")) is not int
            or raw_index["capture_version"] != manifest["capture_version"]
            or not isinstance(units, list)
            or len(units) != manifest["raw_unit_count"]
        ):
            raise VerificationError("Bundle raw index is invalid or inconsistent.")
        unit_paths: set[str] = set()
        for unit in units:
            if not isinstance(unit, dict):
                raise VerificationError("Bundle raw unit is invalid.")
            _artifact_path(root, unit.get("path"))
            relative = unit["path"]
            if (
                relative in unit_paths
                or relative not in checked
                or not _integer(unit.get("byte_size"))
                or unit["byte_size"] != checked[relative][2]
            ):
                raise VerificationError("Bundle raw unit is invalid or unchecked.")
            unit_paths.add(relative)

        for path, digest, byte_size in checked.values():
            if path.stat().st_size != byte_size or sha256_file(path) != digest:
                raise VerificationError(
                    "Bundle artifact checksum or size does not match."
                )
        _verify_normalized(_read_object(paths["normalized_path"]), manifest)
    except OSError as exc:
        raise VerificationError("Bundle artifacts could not be verified.") from exc
