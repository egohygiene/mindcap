from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_bytes
from mindcap.core.models import CaptureRequest
from mindcap.plugins.chrome_bookmarks.archive.verifier import (
    verify_chrome_bookmarks_bundle,
)
from mindcap.plugins.chrome_bookmarks.plugin import ChromeBookmarksPlugin

FIXTURE_ROOT = (
    Path(__file__).parent
    / "fixtures"
    / "chrome_bookmarks"
    / "multiple_profiles"
    / "User Data"
)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    plugin = ChromeBookmarksPlugin()
    request = CaptureRequest(
        source_type="chrome-bookmarks",
        source="",
        provider="chrome-bookmarks",
        canonical_identifier="local",
        canonical_url=None,
        strategy="filesystem",
        artifact_root=tmp_path,
        options={"user_data_dirs": [str(FIXTURE_ROOT)], "channel": "stable"},
    )
    envelope = plugin.strategy("filesystem").capture(request)
    normalized = plugin.normalize(envelope, request.canonical_identifier)
    return (
        plugin.storage()
        .persist(request, envelope, normalized, plugin.render(normalized))
        .path
    )


def read_json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def replace_checked_json(bundle: Path, relative: str, value: object) -> None:
    write_json(bundle / relative, value)
    payload = (bundle / relative).read_bytes()
    checksums = read_json(bundle / "checksums.json")
    for entry in checksums["files"]:
        if entry["path"] == relative:
            entry["sha256"] = sha256_bytes(payload)
            entry["byte_size"] = len(payload)
    write_json(bundle / "checksums.json", checksums)


def test_accepts_storage_generated_chrome_bundle(bundle: Path) -> None:
    verify_chrome_bookmarks_bundle(bundle)


@pytest.mark.parametrize(
    "relative", ["manifest.json", "checksums.json", "normalized/bookmarks.json"]
)
def test_rejects_missing_required_artifact(bundle: Path, relative: str) -> None:
    (bundle / relative).unlink()
    with pytest.raises(VerificationError):
        verify_chrome_bookmarks_bundle(bundle)


def test_rejects_tampered_normalized_artifact(bundle: Path) -> None:
    path = bundle / "normalized/bookmarks.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(VerificationError, match="checksum or size"):
        verify_chrome_bookmarks_bundle(bundle)


@pytest.mark.parametrize("files", [None, [], {}, "private bookmark title"])
def test_rejects_missing_or_malformed_checksum_list(
    bundle: Path, files: object
) -> None:
    write_json(bundle / "checksums.json", {"files": files})
    with pytest.raises(VerificationError, match="checksums"):
        verify_chrome_bookmarks_bundle(bundle)


def test_rejects_unchecked_normalized_artifact(bundle: Path) -> None:
    checksums = read_json(bundle / "checksums.json")
    checksums["files"] = [
        entry
        for entry in checksums["files"]
        if entry["path"] != "normalized/bookmarks.json"
    ]
    write_json(bundle / "checksums.json", checksums)
    with pytest.raises(VerificationError, match="lack checksums"):
        verify_chrome_bookmarks_bundle(bundle)


@pytest.mark.parametrize("location", ["manifest", "checksums", "raw-index"])
@pytest.mark.parametrize(
    "unsafe",
    [
        "../outside-private.json",
        "/outside-private.json",
        "C:\\private.json",
        "normalized/../outside.json",
        "./README.md",
    ],
)
def test_rejects_unsafe_paths_before_hashing(
    bundle: Path, location: str, unsafe: str
) -> None:
    if location == "manifest":
        manifest = read_json(bundle / "manifest.json")
        manifest["normalized_path"] = unsafe
        write_json(bundle / "manifest.json", manifest)
    elif location == "checksums":
        checksums = read_json(bundle / "checksums.json")
        checksums["files"][-1]["path"] = unsafe
        write_json(bundle / "checksums.json", checksums)
    else:
        raw_index = read_json(bundle / "raw/index.json")
        raw_index["units"][0]["path"] = unsafe
        replace_checked_json(bundle, "raw/index.json", raw_index)
    with patch(
        "mindcap.plugins.chrome_bookmarks.archive.verifier.sha256_file"
    ) as hash_file:
        with pytest.raises(VerificationError, match="unsafe artifact path") as error:
            verify_chrome_bookmarks_bundle(bundle)
        hash_file.assert_not_called()
    assert unsafe not in str(error.value)
    assert str(bundle) not in str(error.value)


@pytest.mark.parametrize(
    "relative",
    ["manifest.json", "checksums.json", "normalized/bookmarks.json", "raw/index.json"],
)
def test_rejects_symlink_artifacts_without_reading_target(
    bundle: Path, relative: str, tmp_path: Path
) -> None:
    target = tmp_path / "private-outside.json"
    target.write_text("private bookmark content", encoding="utf-8")
    path = bundle / relative
    path.unlink()
    path.symlink_to(target)
    original_read = Path.read_text
    reads: list[Path] = []

    def tracked_read(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(path)
        return original_read(path, *args, **kwargs)

    with (
        patch.object(Path, "read_text", tracked_read),
        pytest.raises(VerificationError, match="symlinks"),
    ):
        verify_chrome_bookmarks_bundle(bundle)
    assert target not in reads
    assert path not in reads


def test_rejects_symlink_parent_without_reading_target(
    bundle: Path, tmp_path: Path
) -> None:
    normalized = bundle / "normalized"
    normalized.rename(tmp_path / "private-normalized")
    normalized.symlink_to(tmp_path / "private-normalized", target_is_directory=True)
    with pytest.raises(VerificationError, match="symlinks"):
        verify_chrome_bookmarks_bundle(bundle)


@pytest.mark.parametrize(
    "relative",
    ["manifest.json", "checksums.json", "raw/index.json", "normalized/bookmarks.json"],
)
@pytest.mark.parametrize(
    "payload", [b"{private bookmark content", b"[]", b"null", b"\xff"]
)
def test_rejects_malformed_json_without_content_in_error(
    bundle: Path, relative: str, payload: bytes
) -> None:
    (bundle / relative).write_bytes(payload)
    if relative not in {"manifest.json", "checksums.json"}:
        checksums = read_json(bundle / "checksums.json")
        for entry in checksums["files"]:
            if entry["path"] == relative:
                entry["sha256"] = sha256_bytes(payload)
                entry["byte_size"] = len(payload)
        write_json(bundle / "checksums.json", checksums)
    with pytest.raises(VerificationError) as error:
        verify_chrome_bookmarks_bundle(bundle)
    assert "private bookmark" not in str(error.value)
    assert str(bundle) not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "other/v1"),
        ("provider", "chatgpt"),
        ("source_type", "conversation"),
        ("capture_version", True),
        ("raw_unit_count", "2"),
        ("source_id", []),
        ("checksums_path", "other.json"),
    ],
)
def test_rejects_invalid_manifest_identity_or_types(
    bundle: Path, field: str, value: object
) -> None:
    manifest = read_json(bundle / "manifest.json")
    manifest[field] = value
    write_json(bundle / "manifest.json", manifest)
    with pytest.raises(VerificationError):
        verify_chrome_bookmarks_bundle(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "other/v1"),
        ("provider", "chatgpt"),
        ("source_id", "other"),
        ("canonical_identifier", "other"),
        ("bookmark_records", {}),
        ("folder_records", [None]),
        ("summary", {"bookmark_count": True}),
    ],
)
def test_rejects_checked_but_invalid_normalized_data(
    bundle: Path, field: str, value: object
) -> None:
    normalized = read_json(bundle / "normalized/bookmarks.json")
    normalized[field] = value
    replace_checked_json(bundle, "normalized/bookmarks.json", normalized)
    with pytest.raises(VerificationError):
        verify_chrome_bookmarks_bundle(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "bad hash"),
        ("sha256", 123),
        ("byte_size", True),
        ("byte_size", -1),
        ("byte_size", "100"),
        ("path", None),
    ],
)
def test_rejects_invalid_checksum_entry_types(
    bundle: Path, field: str, value: object
) -> None:
    checksums = read_json(bundle / "checksums.json")
    checksums["files"][0][field] = value
    write_json(bundle / "checksums.json", checksums)
    with pytest.raises(VerificationError):
        verify_chrome_bookmarks_bundle(bundle)


def test_rejects_duplicate_checksum_paths(bundle: Path) -> None:
    checksums = read_json(bundle / "checksums.json")
    checksums["files"].append(checksums["files"][0])
    write_json(bundle / "checksums.json", checksums)
    with pytest.raises(VerificationError, match="duplicated"):
        verify_chrome_bookmarks_bundle(bundle)


def test_rejects_false_digest_with_correct_size(bundle: Path) -> None:
    checksums = read_json(bundle / "checksums.json")
    checksums["files"][0]["sha256"] = "0" * 64
    write_json(bundle / "checksums.json", checksums)
    with pytest.raises(VerificationError, match="checksum or size"):
        verify_chrome_bookmarks_bundle(bundle)


def test_rejects_duplicate_manifest_roles(bundle: Path) -> None:
    manifest = read_json(bundle / "manifest.json")
    manifest["normalized_path"] = manifest["raw_index_path"]
    write_json(bundle / "manifest.json", manifest)
    with pytest.raises(VerificationError, match="distinct"):
        verify_chrome_bookmarks_bundle(bundle)


def test_rejects_duplicate_json_object_keys(bundle: Path) -> None:
    (bundle / "checksums.json").write_text(
        '{"files": [], "files": []}', encoding="utf-8"
    )
    with pytest.raises(VerificationError, match="duplicate fields"):
        verify_chrome_bookmarks_bundle(bundle)


def test_rejects_alternate_normalized_path_with_unchecked_canonical(
    bundle: Path,
) -> None:
    canonical_path = bundle / "normalized/bookmarks.json"
    alternate_relative = "normalized/checked-alternate.json"
    (bundle / alternate_relative).write_bytes(canonical_path.read_bytes())
    manifest = read_json(bundle / "manifest.json")
    manifest["normalized_path"] = alternate_relative
    write_json(bundle / "manifest.json", manifest)
    checksums = read_json(bundle / "checksums.json")
    for entry in checksums["files"]:
        if entry["path"] == "normalized/bookmarks.json":
            entry["path"] = alternate_relative
    write_json(bundle / "checksums.json", checksums)
    canonical_path.write_text("unchecked private bookmark content", encoding="utf-8")

    with pytest.raises(VerificationError, match="canonical layout"):
        verify_chrome_bookmarks_bundle(bundle)
