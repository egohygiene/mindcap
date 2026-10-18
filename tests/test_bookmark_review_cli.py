from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from mindcap import MindcapApplication
from mindcap.contracts import PrepareBookmarksCommand
from mindcap.core.errors import CaptureFailedError
from mindcap_cli.app import app


def make_profiles(tmp_path: Path) -> Path:
    root = tmp_path / "Chrome User Data"
    entries = {
        "Default": [
            {
                "name": "Public guide",
                "url": "https://www.python.org/guide?utm_source=mail",
            },
            {"name": "Video", "url": "https://www.youtube.com/watch?v=demo123"},
        ],
        "Profile 1": [
            {
                "name": "Duplicate kept",
                "url": "https://www.python.org/guide?utm_source=mail",
            },
            {"name": "Private browser item", "url": "chrome://settings"},
            {
                "name": "Private token",
                "url": "https://www.python.org/?token=secret-marker",
            },
        ],
    }
    for profile, bookmarks in entries.items():
        folder = root / profile
        folder.mkdir(parents=True)
        (folder / "Bookmarks").write_text(
            json.dumps(
                {
                    "version": 1,
                    "roots": {
                        "bookmark_bar": {
                            "type": "folder",
                            "name": "Sensitive folder marker",
                            "children": [
                                {"type": "url", "id": str(index), **bookmark}
                                for index, bookmark in enumerate(bookmarks)
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
    (root / "Local State").write_text(
        json.dumps(
            {"profile": {"info_cache": {"Default": {"name": "Private profile marker"}}}}
        ),
        encoding="utf-8",
    )
    return root


def test_prepare_captures_then_batches_without_network_or_source_changes(
    tmp_path: Path,
) -> None:
    source = make_profiles(tmp_path)
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with patch("socket.create_connection", side_effect=AssertionError("network")):
        result = CliRunner().invoke(
            app,
            [
                "bookmarks",
                "prepare",
                "--user-data-dir",
                str(source),
                "--output",
                str(tmp_path / "review workspace"),
                "--batch-size",
                "2",
                "--json",
            ],
        )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["profile_count"] == 2
    assert summary["bookmark_count"] == 5
    assert summary["included_count"] == 3
    assert summary["withheld_count"] == 2
    assert summary["batch_count"] == 2
    assert summary["batch_size"] == 2
    assert Path(summary["first_batch"]).is_file()
    assert (Path(summary["source_bundle"]) / "raw" / "index.json").is_file()
    assert {p: p.read_bytes() for p in before} == before
    batches = sorted(Path(summary["output_directory"]).glob("batch-*.md"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in batches)
    assert text.count("https://www.python.org/guide") == 2
    assert "watch?v=demo123" in text
    assert "utm_source" not in text
    for private in (
        "Private profile marker",
        "Sensitive folder marker",
        "secret-marker",
    ):
        assert private not in text
        assert private not in result.stdout


def test_saved_capture_can_be_rebatched_without_recapturing(tmp_path: Path) -> None:
    source = make_profiles(tmp_path)
    application = MindcapApplication.default()
    first = application.prepare_bookmarks(
        PrepareBookmarksCommand(
            user_data_dirs=[source], output_root=tmp_path / "private workspace"
        )
    )
    with patch.object(
        MindcapApplication, "capture", side_effect=AssertionError("recapture")
    ):
        result = CliRunner().invoke(
            app,
            [
                "bookmarks",
                "batches",
                str(first.source_bundle),
                "--output",
                str(tmp_path / "smaller batches"),
                "--batch-size",
                "1",
                "--json",
            ],
        )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["batch_count"] == 3
    assert summary["source_content_hash"] == first.source_content_hash
    assert first.first_batch is not None and first.first_batch.is_file()


def test_prepare_human_summary_explains_next_file(tmp_path: Path) -> None:
    source = make_profiles(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "bookmarks",
            "prepare",
            "--user-data-dir",
            str(source),
            "--output",
            str(tmp_path / "review"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Captured readable profiles" in result.stdout
    assert "First file to inspect" in result.stdout
    assert "batch-0001.md" in result.stdout
    assert "Public guide" not in result.stdout
    assert "secret-marker" not in result.stdout


def test_prepare_default_workspace_and_profile_selection(tmp_path: Path) -> None:
    source = make_profiles(tmp_path)
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = CliRunner().invoke(
            app,
            [
                "bookmarks",
                "prepare",
                "--user-data-dir",
                str(source),
                "--profile",
                "Default",
                "--quiet",
            ],
        )
    assert result.exit_code == 0, result.output
    path = Path(result.stdout.strip())
    assert path.is_relative_to(tmp_path / "Downloads" / "mindcap-bookmarks")
    assert path.name == "batch-0001.md"
    assert path.is_file()


def test_prepare_failure_does_not_print_source_error_content(tmp_path: Path) -> None:
    with patch.object(
        MindcapApplication,
        "capture",
        side_effect=CaptureFailedError("private-path private-title secret-marker"),
    ):
        result = CliRunner().invoke(
            app, ["bookmarks", "prepare", "--output", str(tmp_path / "review")]
        )
    assert result.exit_code == 1
    assert not result.stdout
    assert "--user-data-dir" in result.stderr
    assert "chrome://version" in result.stderr
    assert "secret-marker" not in result.output
    assert not list(tmp_path.rglob("batch-*.md"))


def test_invalid_batch_size_never_captures(tmp_path: Path) -> None:
    with patch.object(MindcapApplication, "capture") as capture:
        result = CliRunner().invoke(
            app,
            ["bookmarks", "prepare", "--batch-size", "0", "--output", str(tmp_path)],
        )
    assert result.exit_code == 2
    capture.assert_not_called()


def test_prepare_refuses_git_workspace_before_capture(tmp_path: Path) -> None:
    repository = tmp_path / "checkout"
    (repository / ".git").mkdir(parents=True)
    with patch.object(MindcapApplication, "capture") as capture:
        result = CliRunner().invoke(
            app, ["bookmarks", "prepare", "--output", str(repository / "bookmarks")]
        )
    assert result.exit_code == 1
    capture.assert_not_called()
    assert not (repository / "bookmarks").exists()
