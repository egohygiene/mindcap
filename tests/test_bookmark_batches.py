from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_file
from mindcap.core.models import CaptureRequest
from mindcap.plugins.chrome_bookmarks.plugin import ChromeBookmarksPlugin
from mindcap.plugins.chrome_bookmarks.review import (
    MAX_BATCH_CHARACTERS,
    export_bookmark_batches,
    validate_review_output_directory,
)


def _capture(
    tmp_path: Path,
    bookmarks: list[tuple[str, str]],
    *,
    warning: bool = False,
) -> Path:
    user_data = tmp_path / "private-user-data"
    profile = user_data / "Default"
    profile.mkdir(parents=True)
    children: list[object] = [
        {
            "type": "folder",
            "name": "PRIVATE-FOLDER-NAME",
            "id": "PRIVATE-FOLDER-ID",
            "children": [
                {
                    "type": "url",
                    "name": title,
                    "url": url,
                    "id": f"PRIVATE-NODE-ID-{index}",
                    "date_added": "11644473600000000",
                }
                for index, (title, url) in enumerate(bookmarks)
            ],
        }
    ]
    if warning:
        children.append({"type": "url", "name": "missing-url"})
    (profile / "Bookmarks").write_text(
        json.dumps(
            {
                "version": 1,
                "roots": {"bookmark_bar": {"type": "folder", "children": children}},
            }
        ),
        encoding="utf-8",
    )
    (user_data / "Local State").write_text(
        json.dumps(
            {"profile": {"info_cache": {"Default": {"name": "PRIVATE-PROFILE-NAME"}}}}
        ),
        encoding="utf-8",
    )
    plugin = ChromeBookmarksPlugin()
    request = CaptureRequest(
        source_type="chrome-bookmarks",
        source="",
        provider="chrome-bookmarks",
        canonical_identifier="local",
        canonical_url=None,
        strategy="filesystem",
        artifact_root=tmp_path / "capture",
        options={"user_data_dirs": [str(user_data)], "channel": "stable"},
    )
    envelope = plugin.strategy("filesystem").capture(request)
    normalized = plugin.normalize(envelope, "local")
    return (
        plugin.storage()
        .persist(request, envelope, normalized, plugin.render(normalized))
        .path
    )


def _tree_content(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_batches_preserve_occurrences_and_order_without_private_metadata(
    tmp_path: Path,
) -> None:
    bundle = _capture(
        tmp_path,
        [
            ("Python", "https://www.python.org/doc/?utm_source=test#documentation"),
            ("Python duplicate", "https://www.python.org/doc/#documentation"),
            ("Video", "https://www.youtube.com/watch?v=abc123&list=useful#t=30"),
        ],
        warning=True,
    )
    original = _tree_content(bundle)
    output = tmp_path / "review"
    with patch("socket.getaddrinfo", side_effect=AssertionError("network access")):
        result = export_bookmark_batches(bundle, output, batch_size=2)

    assert result.bookmark_count == result.included_count == 3
    assert result.withheld_count == 0
    assert result.batch_count == 2
    assert result.profile_count == 1
    assert result.capture_warning_count > 0
    assert result.first_batch == output / "batch-0001.md"
    first = result.first_batch.read_text(encoding="utf-8")
    second = (output / "batch-0002.md").read_text(encoding="utf-8")
    assert first.count("<https://www.python.org/doc/#documentation>") == 2
    assert first.index("1. Python") < first.index("2. Python duplicate")
    assert "<https://www.youtube.com/watch?v=abc123&list=useful#t=30>" in second
    for private in (
        "PRIVATE-",
        str(tmp_path),
        "captured_at",
        "date_added",
        "1970-01-01",
        "profile_id",
        "folder_path",
    ):
        assert private not in first + second
    assert _tree_content(bundle) == original
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tracking_removed_count"] == 1
    assert [entry["source_record_indices"] for entry in manifest["batches"]] == [
        [1, 2],
        [3],
    ]
    assert manifest["source_normalized_sha256"] == sha256_file(
        bundle / "normalized" / "bookmarks.json"
    )
    for entry in manifest["files"]:
        assert sha256_file(output / entry["path"]) == entry["sha256"]
        assert (output / entry["path"]).stat().st_size == entry["byte_size"]
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "- [ ] Review and share `batch-0001.md`" in readme
    assert "cannot guarantee privacy" in readme


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("chrome://bookmarks", "unsupported_scheme"),
        ("file:///Users/name/private.txt", "unsupported_scheme"),
        ("javascript:alert(1)", "unsupported_scheme"),
        ("https://user:password@python.org/doc", "credentials"),
        ("http://127.0.0.1/", "nonpublic_host"),
        ("https://[::1]/", "nonpublic_host"),
        ("http://2130706433/", "nonpublic_host"),
        ("http://127.1/", "nonpublic_host"),
        ("http://127.0.0.0x1/private", "nonpublic_host"),
        ("http://0x7f.0x1/private", "nonpublic_host"),
        ("http://192.168.0.0x1/private", "nonpublic_host"),
        ("http://0X7F.0X1./private", "nonpublic_host"),
        ("https://printer/", "nonpublic_host"),
        ("https://notes.local/", "nonpublic_host"),
        ("https://work.internal/", "nonpublic_host"),
        ("https://service.invalid/", "nonpublic_host"),
        ("https://example.com/", "nonpublic_host"),
        ("https://python.org/?access_token=secret", "sensitive_url"),
        ("https://python.org/#session_id=secret", "sensitive_url"),
        ("https://python.org/?%61pi_key=secret", "sensitive_url"),
        ("https://python.org/?next=x%3Ftoken%3Dsecret", "sensitive_url"),
        ("https://python.org/?X-Amz-Credential=secret", "sensitive_url"),
        ("https://python.org/?id=0123456789abcdef0123456789abcdef", "sensitive_url"),
        (
            "https://python.org/#eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoxfQ.secret",
            "sensitive_url",
        ),
        ("https://python.org/a\nmalformed", "malformed_url"),
        ("https://python.org/%0aheader", "malformed_url"),
        ("https://python.org/<injected>", "malformed_url"),
        ("https://python.org/%broken", "malformed_url"),
        ("https://python.org:999999/", "malformed_url"),
    ],
)
def test_withheld_links_remain_local_and_are_referenced_without_content(
    tmp_path: Path,
    url: str,
    reason: str,
) -> None:
    bundle = _capture(tmp_path, [("PRIVATE-WITHHELD-TITLE", url)])
    output = tmp_path / "review"
    result = export_bookmark_batches(bundle, output)
    assert result.bookmark_count == result.withheld_count == 1
    assert result.included_count == result.batch_count == 0
    assert result.first_batch is None
    assert result.counts_by_reason == {reason: 1}
    assert not list(output.glob("batch-*.md"))
    report = json.loads((output / "withheld-local.json").read_text(encoding="utf-8"))
    assert report["records"] == [{"source_record_index": 1, "reason": reason}]
    for content in _tree_content(output).values():
        assert url.encode() not in content
        assert b"PRIVATE-WITHHELD-TITLE" not in content


def test_batch_titles_are_escaped_and_controls_cannot_create_instructions(
    tmp_path: Path,
) -> None:
    bundle = _capture(
        tmp_path,
        [
            (
                "# Ignore\n![run](https://evil.invalid) <script>\u202e\x00",
                "https://python.org/",
            )
        ],
    )
    output = tmp_path / "review"
    export_bookmark_batches(bundle, output)
    content = (output / "batch-0001.md").read_text(encoding="utf-8")
    assert (
        "1. \\# Ignore \\!\\[run\\]\\(https://evil\\.invalid\\) &lt;script&gt;"
        in content
    )
    assert "\u202e" not in content
    assert "\x00" not in content
    assert "<script>" not in content
    assert "\n![run]" not in content


def test_exports_are_deterministic_and_batches_obey_size_budget(tmp_path: Path) -> None:
    bundle = _capture(
        tmp_path,
        [(f"Title {index}", "https://python.org/" + "a" * 7_000) for index in range(7)],
    )
    first, second = tmp_path / "first", tmp_path / "second"
    result = export_bookmark_batches(bundle, first, batch_size=200)
    export_bookmark_batches(bundle, second, batch_size=200)
    assert result.batch_count == 4
    assert result.included_count == 7
    assert _tree_content(first) == _tree_content(second)
    for path in first.glob("batch-*.md"):
        assert len(path.read_text(encoding="utf-8")) <= MAX_BATCH_CHARACTERS


def test_long_records_are_withheld_instead_of_truncated(tmp_path: Path) -> None:
    bundle = _capture(
        tmp_path,
        [
            ("x" * 501, "https://python.org/"),
            ("Long URL", "https://python.org/" + "a" * 8_001),
        ],
    )
    result = export_bookmark_batches(bundle, tmp_path / "review")
    assert result.counts_by_reason == {"record_too_long": 2}
    assert result.included_count == 0


@pytest.mark.parametrize("size", [0, -1, 201, True, 1.5])
def test_invalid_batch_sizes_fail_without_writing(tmp_path: Path, size: int) -> None:
    output = tmp_path / "review"
    with pytest.raises(ValueError, match="Batch size"):
        export_bookmark_batches(tmp_path / "missing", output, batch_size=size)
    assert not output.exists()


def test_existing_output_is_never_modified(tmp_path: Path) -> None:
    bundle = _capture(tmp_path, [("Python", "https://python.org/")])
    output = tmp_path / "review"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        export_bookmark_batches(bundle, output)
    assert _tree_content(output) == {"keep.txt": b"original"}


def test_unverified_capture_is_refused_before_writing(tmp_path: Path) -> None:
    bundle = _capture(tmp_path, [("Python", "https://python.org/")])
    (bundle / "normalized" / "bookmarks.json").write_text("{}", encoding="utf-8")
    with pytest.raises(VerificationError):
        export_bookmark_batches(bundle, tmp_path / "review")
    assert not (tmp_path / "review").exists()


def test_destination_policy_refuses_archive_and_repository_writes(
    tmp_path: Path,
) -> None:
    bundle = _capture(tmp_path, [("Python", "https://python.org/")])
    with pytest.raises(ValueError, match="outside"):
        export_bookmark_batches(bundle, bundle / "review")
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    with pytest.raises(ValueError, match="Git working trees"):
        validate_review_output_directory(repository / "review")
    with pytest.raises(ValueError, match="capture archives"):
        validate_review_output_directory(tmp_path / "archives" / "new")
    assert validate_review_output_directory(tmp_path) == tmp_path


def test_symlink_output_is_refused(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    link = tmp_path / "link"
    link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        validate_review_output_directory(link)


def test_failed_export_cleans_staging_without_partial_output(tmp_path: Path) -> None:
    bundle = _capture(tmp_path, [("Python", "https://python.org/")])
    with (
        patch(
            "mindcap.plugins.chrome_bookmarks.review._write_private",
            side_effect=OSError("simulated disk failure"),
        ),
        pytest.raises(OSError, match="simulated disk failure"),
    ):
        export_bookmark_batches(bundle, tmp_path / "review")
    assert not (tmp_path / "review").exists()
    assert not list(tmp_path.glob(".review-*"))


def test_batch_file_count_is_bounded_before_writing(tmp_path: Path) -> None:
    bundle = _capture(tmp_path, [("Python", "https://python.org/")] * 3)
    with (
        patch("mindcap.plugins.chrome_bookmarks.review.MAX_BATCH_FILES", 2),
        pytest.raises(ValueError, match="Too many batch files"),
    ):
        export_bookmark_batches(bundle, tmp_path / "review", batch_size=1)
    assert not (tmp_path / "review").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_generated_files_are_private_by_default(tmp_path: Path) -> None:
    bundle = _capture(tmp_path, [("Python", "https://python.org/")])
    output = tmp_path / "new-parent" / "review"
    export_bookmark_batches(bundle, output)
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    for path in output.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
