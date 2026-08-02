from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from mindcap.core.models import CaptureRequest
from mindcap.plugins.chrome_bookmarks.diagnostics import (
    collect_chrome_bookmarks_diagnostics,
)
from mindcap.plugins.chrome_bookmarks.discovery import discover_profiles
from mindcap.plugins.chrome_bookmarks.parser import normalize_chrome_timestamp
from mindcap.plugins.chrome_bookmarks.plugin import ChromeBookmarksPlugin
from mindcap_cli.app import app

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "chrome_bookmarks"
MULTI_PROFILE_USER_DATA = FIXTURE_ROOT / "multiple_profiles" / "User Data"
MALFORMED_USER_DATA = FIXTURE_ROOT / "malformed" / "User Data"


def test_plugins_list_includes_chrome_bookmarks() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])

    assert result.exit_code == 0
    assert "chrome-bookmarks" in result.stdout
    assert "filesystem" in result.stdout


def test_doctor_chrome_bookmarks_invokes_diagnostics() -> None:
    runner = CliRunner()
    with patch("mindcap_cli.app.run_chrome_bookmarks_doctor") as mock_doctor:
        result = runner.invoke(app, ["doctor", "chrome-bookmarks", "--verbose"])

    assert result.exit_code == 0
    assert mock_doctor.called


def test_discover_profiles_reads_profile_names_from_local_state() -> None:
    profiles = discover_profiles(user_data_dirs=[str(MULTI_PROFILE_USER_DATA)])

    assert [profile.profile_directory_name for profile in profiles] == [
        "Default",
        "Profile 1",
    ]
    assert [profile.profile_name for profile in profiles] == ["Personal", "Research"]


def test_collect_chrome_bookmarks_diagnostics_reports_counts() -> None:
    with (
        patch(
            "mindcap.plugins.chrome_bookmarks.diagnostics.automatic_user_data_roots",
            return_value=[],
        ),
        patch(
            "mindcap.plugins.chrome_bookmarks.diagnostics.discover_profiles",
            return_value=[],
        ),
    ):
        checks = collect_chrome_bookmarks_diagnostics()

    assert checks[0]["name"] == "automatic_user_data_roots"
    assert checks[1]["name"] == "readable_profiles"


def test_normalize_chrome_timestamp_boundary_values() -> None:
    zero = normalize_chrome_timestamp("0")
    epoch_1970 = normalize_chrome_timestamp("11644473600000000")
    invalid = normalize_chrome_timestamp("not-a-timestamp")

    assert zero["status"] == "zero"
    assert zero["value"] is None
    assert epoch_1970["status"] == "parsed"
    assert epoch_1970["value"] == "1970-01-01T00:00:00Z"
    assert invalid["status"] == "invalid"
    assert invalid["warning"]


def test_normalizer_keeps_valid_bookmarks_when_some_nodes_are_malformed(
    tmp_path: Path,
) -> None:
    plugin = ChromeBookmarksPlugin()
    request = plugin.strategy("filesystem").capture(
        plugin_request(tmp_path, MALFORMED_USER_DATA)
    )
    normalized = plugin.normalize(request, "local")

    assert len(normalized["bookmark_records"]) == 1
    assert normalized["bookmark_records"][0]["url"] == "chrome://bookmarks"
    assert any(
        "Skipped bookmark without URL" in warning for warning in normalized["warnings"]
    )


def test_capture_pipeline_creates_verified_bundle_for_multiple_profiles(
    tmp_path: Path,
) -> None:
    plugin = ChromeBookmarksPlugin()
    request = plugin_request(tmp_path, MULTI_PROFILE_USER_DATA)
    envelope = plugin.strategy("filesystem").capture(request)
    normalized = plugin.normalize(envelope, request.canonical_identifier)
    transcript = plugin.render(normalized)

    stored = plugin.storage().persist(request, envelope, normalized, transcript)

    assert stored.status == "complete"
    assert (stored.path / "manifest.json").is_file()
    assert (stored.path / "normalized" / "bookmarks.json").is_file()
    manifest = json.loads((stored.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "chrome-bookmarks"
    plugin.storage().verify(stored.path)


def test_capture_command_supports_omitted_source_for_chrome_bookmarks(
    tmp_path: Path,
) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "capture",
            "chrome-bookmarks",
            "--user-data-dir",
            str(MULTI_PROFILE_USER_DATA),
            "--output",
            str(tmp_path),
            "--quiet",
        ],
    )

    assert result.exit_code == 0
    assert str(
        tmp_path / "archives" / "chrome-bookmarks" / "chrome-bookmarks-local" / "v1"
    ) in result.stdout.replace("\n", "")


def test_snapshot_uses_backup_when_primary_is_invalid(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "User Data"
    profile_dir = user_data_dir / "Default"
    profile_dir.mkdir(parents=True)
    (profile_dir / "Bookmarks").write_text("{not-json", encoding="utf-8")
    (profile_dir / "Bookmarks.bak").write_text(
        json.dumps(
            {
                "checksum": "bak",
                "roots": {
                    "bookmark_bar": {
                        "children": [],
                        "name": "Bookmarks bar",
                        "type": "folder",
                    },
                    "other": {
                        "children": [],
                        "name": "Other bookmarks",
                        "type": "folder",
                    },
                    "synced": {
                        "children": [],
                        "name": "Mobile bookmarks",
                        "type": "folder",
                    },
                },
                "version": 1,
            }
        ),
        encoding="utf-8",
    )

    plugin = ChromeBookmarksPlugin()
    request = plugin_request(tmp_path, user_data_dir)
    envelope = plugin.strategy("filesystem").capture(request)
    normalized = plugin.normalize(envelope, request.canonical_identifier)

    assert envelope.safe_metadata["profiles"][0]["selected_source"] == "backup"
    assert any("Bookmarks.bak" in warning for warning in normalized["warnings"])


def plugin_request(tmp_path: Path, user_data_dir: Path) -> CaptureRequest:
    return CaptureRequest(
        source_type="chrome-bookmarks",
        source="",
        provider="chrome-bookmarks",
        canonical_identifier="local",
        canonical_url=None,
        strategy="filesystem",
        artifact_root=tmp_path,
        options={"user_data_dirs": [str(user_data_dir)], "channel": "stable"},
    )
