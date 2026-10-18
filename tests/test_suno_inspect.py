"""Tests for the Suno archive inspector and updated CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_bytes
from mindcap.plugins.suno.archive.diagnostics import suno_asset_diagnostics
from mindcap.plugins.suno.archive.inspector import inspect_suno_archive
from mindcap_cli.app import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_bundle(root: Path) -> Path:
    """Create a minimal but valid fake Suno bundle for inspector tests."""
    bundle = root / "v1"
    bundle.mkdir(parents=True)

    manifest = {
        "schema": "mindcap.suno-archive/v0.1",
        "provider": "suno",
        "source_type": "workspace",
        "workspace_id": "ws-test-123",
        "source_id": "suno-ws-test-123",
        "canonical_url": None,
        "title": "Test Workspace",
        "capture_version": 1,
        "previous_version": None,
        "captured_at": "2025-01-03T00:00:00+00:00",
        "raw_unit_count": 1,
        "asset_count": 2,
        "clip_count": 1,
        "warnings": [],
        "readme_path": "README.md",
        "checksums_path": "checksums.json",
        "report_json_path": "reports/capture-report.json",
        "report_markdown_path": "reports/capture-report.md",
        "workspace_metadata_path": "workspace/metadata.json",
    }

    # Write the files referenced in the manifest
    (bundle / "README.md").write_text("# Test", encoding="utf-8")
    (bundle / "reports").mkdir()
    (bundle / "reports" / "capture-report.json").write_text("{}", encoding="utf-8")
    (bundle / "reports" / "capture-report.md").write_text("# Report", encoding="utf-8")
    (bundle / "workspace").mkdir()
    (bundle / "workspace" / "metadata.json").write_text("{}", encoding="utf-8")

    # Create clip assets
    clip_dir = bundle / "clips" / "clip-alpha"
    (clip_dir / "audio").mkdir(parents=True)
    (clip_dir / "audio" / "original.mp3").write_bytes(b"fake mp3")
    (clip_dir / "artwork").mkdir()
    (clip_dir / "artwork" / "cover.jpg").write_bytes(b"fake jpg")

    # Checksums (covering the real files)
    from mindcap.core.hashing import sha256_bytes

    files_to_checksum = [
        "README.md",
        "reports/capture-report.json",
        "reports/capture-report.md",
        "workspace/metadata.json",
        "clips/clip-alpha/audio/original.mp3",
        "clips/clip-alpha/artwork/cover.jpg",
    ]
    checksums = []
    for rel in files_to_checksum:
        content = (bundle / rel).read_bytes()
        checksums.append(
            {"path": rel, "sha256": sha256_bytes(content), "byte_size": len(content)}
        )

    (bundle / "checksums.json").write_text(
        json.dumps({"files": checksums}, indent=2), encoding="utf-8"
    )
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    return bundle


def _replace_metadata(bundle: Path, metadata: object) -> None:
    relative = "workspace/metadata.json"
    path = bundle / relative
    path.write_text(json.dumps(metadata), encoding="utf-8")
    checksums_path = bundle / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    for entry in checksums["files"]:
        if entry["path"] == relative:
            entry["sha256"] = sha256_bytes(path.read_bytes())
            entry["byte_size"] = path.stat().st_size
    checksums_path.write_text(json.dumps(checksums), encoding="utf-8")


def test_offline_audio_diagnostics_only_emits_counts(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    marker = "private-credential-marker"
    _replace_metadata(
        bundle,
        {
            "title": marker,
            "provider_metadata": {"cookie": marker},
            "clips": [
                {
                    "clip_id": marker,
                    "audio_url": f"https://cdn1.suno.ai/{marker}.mp3?Signature={marker}",
                    "archived_assets": [
                        {"asset_type": "audio", "capture_status": "verified"}
                    ],
                },
                {
                    "audio_url": f"https://studio-api.prod.suno.com/api/forbidden?token={marker}"
                },
                {"audio_url": "https://studio-api-prod.suno.com/api/forbidden/"},
                {"audio_url": "https://studio-api-prod.suno.com/api/download"},
                {"audio_url": f"https://{marker}.example/audio.mp3"},
                {"audio_url": "https://[invalid"},
                {},
            ],
        },
    )
    with (
        patch("httpx.Client.request", side_effect=AssertionError("network")),
        patch(
            "mindcap.plugins.suno.client.load_suno_auth_state",
            side_effect=AssertionError("auth"),
        ),
    ):
        result = CliRunner().invoke(
            app, ["inspect", "suno", str(bundle), "--asset-diagnostics"]
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data == {
        "schema": "mindcap.suno-asset-diagnostics/v0.1",
        "offline": True,
        "clip_count": 7,
        "clips_with_archived_primary_audio": 1,
        "audio_url_kinds": {
            "missing": 1,
            "invalid": 1,
            "forbidden_endpoint": 2,
            "suno_api": 1,
            "suno_cdn": 1,
            "other_http": 1,
        },
        "audio_urls_with_query": 2,
        "audio_urls_with_signature_keys": 1,
    }
    assert marker not in result.output
    assert str(bundle) not in result.output
    assert "https://" not in result.output


def test_audio_diagnostics_handles_legacy_metadata_fallback(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    _replace_metadata(
        bundle,
        {
            "clips": [
                {
                    "provider_metadata": {
                        "metadata": {"audio_url": "https://cdn1.suno.ai/track.mp3"}
                    }
                },
                {"provider_metadata": None},
                {"audio_url": 5},
            ]
        },
    )
    data = suno_asset_diagnostics(bundle)
    assert data["audio_url_kinds"]["suno_cdn"] == 1
    assert data["audio_url_kinds"]["missing"] == 1
    assert data["audio_url_kinds"]["invalid"] == 1


@pytest.mark.parametrize("metadata", [None, [], {"clips": {}}, {"clips": [None]}])
def test_audio_diagnostics_rejects_invalid_metadata_without_echo(
    tmp_path: Path, metadata: object
) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    _replace_metadata(bundle, metadata)
    result = CliRunner().invoke(
        app, ["inspect", "suno", str(bundle), "--asset-diagnostics"]
    )
    assert result.exit_code == 1
    assert "could not be verified" in result.output
    assert str(bundle) not in result.output


def test_audio_diagnostics_verifies_saved_files(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    _replace_metadata(bundle, {"clips": []})
    (bundle / "clips/clip-alpha/audio/original.mp3").write_bytes(b"tampered")
    with pytest.raises(VerificationError, match="could not be verified"):
        suno_asset_diagnostics(bundle)


def test_audio_diagnostics_handles_malformed_checksum_document(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    (bundle / "checksums.json").write_text("[]", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["inspect", "suno", str(bundle), "--asset-diagnostics"]
    )
    assert result.exit_code == 1
    assert "could not be verified" in result.output


@pytest.mark.parametrize("substitute", [False, True])
def test_audio_diagnostics_requires_checked_canonical_metadata(
    tmp_path: Path, substitute: bool
) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    _replace_metadata(bundle, {"clips": []})
    if substitute:
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["workspace_metadata_path"] = "reports/capture-report.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        checksums_path = bundle / "checksums.json"
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        checksums["files"] = [
            entry
            for entry in checksums["files"]
            if entry["path"] != "workspace/metadata.json"
        ]
        checksums_path.write_text(json.dumps(checksums), encoding="utf-8")
    with pytest.raises(VerificationError, match="could not be verified"):
        suno_asset_diagnostics(bundle)


def test_audio_diagnostics_validates_urls_and_malformed_asset_status(
    tmp_path: Path,
) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    _replace_metadata(
        bundle,
        {
            "clips": [
                {
                    "audio_url": "https://cdn1.suno.ai:bad/audio.mp3",
                    "archived_assets": [{"asset_type": "audio", "capture_status": {}}],
                },
                {"audio_url": "https://user:secret@cdn1.suno.ai/audio.mp3"},
                {"audio_url": "https://cdn1.suno.ai:65536/audio.mp3"},
            ]
        },
    )
    data = suno_asset_diagnostics(bundle)
    assert data["clips_with_archived_primary_audio"] == 0
    assert data["audio_url_kinds"]["invalid"] == 3


def test_legacy_inspection_does_not_claim_capture_complete(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    result = CliRunner().invoke(app, ["inspect", "suno", str(bundle)])
    assert result.exit_code == 0
    assert "unknown (legacy archive)" in result.stdout


def test_inspection_reports_saved_partial_completeness(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["capture_completeness"] = "partial"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = CliRunner().invoke(app, ["inspect", "suno", str(bundle)])
    assert result.exit_code == 0
    assert "Capture completeness" in result.stdout
    assert "partial" in result.stdout


# ---------------------------------------------------------------------------
# inspect_suno_archive unit tests
# ---------------------------------------------------------------------------


def test_inspect_suno_archive_passes_valid_bundle(tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    bundle = _make_minimal_bundle(tmp_path)
    buf = StringIO()
    c = Console(file=buf, highlight=False, markup=False)
    inspect_suno_archive(bundle, c)
    output = buf.getvalue()
    assert "Test Workspace" in output
    assert "PASS" in output


def test_inspect_suno_archive_shows_clip_count(tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    bundle = _make_minimal_bundle(tmp_path)
    buf = StringIO()
    c = Console(file=buf, highlight=False, markup=False)
    inspect_suno_archive(bundle, c)
    output = buf.getvalue()
    # 1 clip directory was created
    assert "1" in output


def test_inspect_suno_archive_missing_dir_raises(tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    c = Console(file=StringIO())
    with pytest.raises(VerificationError, match="not found"):
        inspect_suno_archive(tmp_path / "nonexistent", c)


def test_inspect_suno_archive_missing_manifest_raises(tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    bad_bundle = tmp_path / "v1"
    bad_bundle.mkdir()
    c = Console(file=StringIO())
    with pytest.raises(VerificationError, match="manifest is missing"):
        inspect_suno_archive(bad_bundle, c)


# ---------------------------------------------------------------------------
# CLI inspect command tests
# ---------------------------------------------------------------------------


def test_cli_inspect_suno_success(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["inspect", "suno", str(bundle)])
    assert result.exit_code == 0
    assert "Test Workspace" in result.stdout


def test_cli_inspect_suno_missing_archive_exits_nonzero(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["inspect", "suno", str(tmp_path / "does-not-exist")])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI verify improved output
# ---------------------------------------------------------------------------


def test_cli_verify_shows_checkmarks(tmp_path: Path) -> None:
    bundle = _make_minimal_bundle(tmp_path)
    runner = CliRunner()
    with patch("mindcap_cli.app.verify_bundle") as mock_verify:
        mock_verify.return_value = None
        result = runner.invoke(app, ["verify", str(bundle)])
    assert result.exit_code == 0
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# CLI capture --verbose / --debug flags
# ---------------------------------------------------------------------------


def test_cli_capture_verbose_flag_accepted() -> None:
    """--verbose should be accepted by the capture command without error."""
    runner = CliRunner()
    # Use an invalid source type to short-circuit actual capture logic
    result = runner.invoke(
        app, ["capture", "suno", "test-id", "--verbose", "--output", "/tmp"]
    )
    # The suno plugin will fail trying to authenticate (no auth state),
    # but the flag itself must be accepted (exit code != 2 = CLI parse error)
    assert result.exit_code != 2


def test_cli_capture_debug_flag_accepted() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["capture", "suno", "test-id", "--debug", "--output", "/tmp"]
    )
    assert result.exit_code != 2


# ---------------------------------------------------------------------------
# Media variant naming helper (service unit test)
# ---------------------------------------------------------------------------


def test_descriptive_media_filename_opaque_version_audio() -> None:
    from mindcap.plugins.suno.archive.service import _descriptive_media_filename

    result = _descriptive_media_filename("1.0.0.1.0.0", "audio/mpeg")
    assert result.startswith("variant-")
    assert "1.0.0.1.0.0" not in result
    assert result.endswith(".bin")


def test_descriptive_media_filename_meaningful_encoding() -> None:
    from mindcap.plugins.suno.archive.service import _descriptive_media_filename

    result = _descriptive_media_filename("opus", "audio/ogg")
    assert result == "variant-opus.bin"


def test_descriptive_media_filename_video_variant() -> None:
    from mindcap.plugins.suno.archive.service import _descriptive_media_filename

    result = _descriptive_media_filename("1.0.0", "video/mp4")
    assert result.startswith("variant-")
    assert "1.0.0" not in result
    assert result.endswith(".bin")
