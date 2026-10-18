from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mindcap.core.models import (
    CapturedAsset,
    CaptureEnvelope,
    CaptureRequest,
    RawResponseUnit,
)
from mindcap.plugins.suno.plugin import SunoPlugin
from mindcap_cli.app import app

WORKSPACE_ID = "8f8fd77f-c5bf-467a-8cb5-558fdbf86386"
WORKSPACE_FIXTURE = Path(__file__).parent / "fixtures" / "suno" / "workspace.json"
_CAPTURED_AT = datetime(2025, 1, 3, tzinfo=UTC)


def _request(tmp_path: Path) -> CaptureRequest:
    return CaptureRequest(
        source_type="suno",
        source=WORKSPACE_ID,
        provider="suno",
        canonical_identifier=WORKSPACE_ID,
        canonical_url=f"https://suno.com/create?wid={WORKSPACE_ID}",
        strategy="api",
        artifact_root=tmp_path,
        options={"audio_format": "mp3", "include_video": True},
    )


def _make_envelope(tmp_path: Path) -> CaptureEnvelope:
    audio_path = tmp_path / "asset-audio.mp3"
    audio_path.write_bytes(b"fake mp3")
    image_path = tmp_path / "asset-cover.jpg"
    image_path.write_bytes(b"fake jpg")
    workspace = json.loads(WORKSPACE_FIXTURE.read_text(encoding="utf-8"))
    lyrics = {"text": "We glow in satellite light"}
    aligned = {"aligned_words": [{"word": "glow", "start_s": 0.0, "end_s": 0.5}]}
    return CaptureEnvelope(
        provider="suno",
        source_type="workspace",
        canonical_identifier=WORKSPACE_ID,
        canonical_url=f"https://suno.com/create?wid={WORKSPACE_ID}",
        captured_at=_CAPTURED_AT,
        strategy="api",
        response_units=[
            RawResponseUnit(
                unit_id="workspace-000",
                sequence=0,
                media_type="application/json",
                body=json.dumps(workspace).encode("utf-8"),
                source_url=f"https://suno.com/api/workspaces/{WORKSPACE_ID}",
                endpoint_category="workspace",
                retrieved_at=_CAPTURED_AT,
            ),
            RawResponseUnit(
                unit_id="lyrics-clip-alpha",
                sequence=1,
                media_type="application/json",
                body=json.dumps(lyrics).encode("utf-8"),
                source_url="https://suno.com/api/gen/clip-alpha/lyrics/",
                endpoint_category="lyrics",
                retrieved_at=_CAPTURED_AT,
            ),
            RawResponseUnit(
                unit_id="aligned-lyrics-clip-alpha",
                sequence=2,
                media_type="application/json",
                body=json.dumps(aligned).encode("utf-8"),
                source_url="https://suno.com/api/gen/clip-alpha/aligned_lyrics/v2/",
                endpoint_category="aligned-lyrics",
                retrieved_at=_CAPTURED_AT,
            ),
        ],
        assets=[
            CapturedAsset(
                asset_id="clip-alpha-audio",
                workspace_id=WORKSPACE_ID,
                clip_id="clip-alpha",
                asset_type="audio",
                media_type="audio/mpeg",
                source_url="https://cdn.example.test/audio.mp3",
                relative_path="clips/clip-alpha/audio/original.mp3",
                temporary_path=audio_path,
                checksum="stub-audio",
                downloaded_at=_CAPTURED_AT,
            ),
            CapturedAsset(
                asset_id="clip-alpha-artwork",
                workspace_id=WORKSPACE_ID,
                clip_id="clip-alpha",
                asset_type="artwork",
                media_type="image/jpeg",
                source_url="https://cdn.example.test/cover.jpg",
                relative_path="clips/clip-alpha/artwork/cover.jpg",
                temporary_path=image_path,
                checksum="stub-image",
                downloaded_at=_CAPTURED_AT,
            ),
        ],
    )


def test_suno_pipeline_creates_verified_workspace_bundle(tmp_path: Path) -> None:
    plugin = SunoPlugin()
    request = _request(tmp_path)
    envelope = _make_envelope(tmp_path)
    normalized = plugin.normalize(envelope, WORKSPACE_ID)
    transcript = plugin.render(normalized)

    stored = plugin.storage().persist(request, envelope, normalized, transcript)

    assert stored.status == "complete"
    assert (stored.path / "manifest.json").is_file()
    assert (stored.path / "workspace" / "metadata.json").is_file()
    assert (stored.path / "clips" / "clip-alpha" / "lyrics" / "lyrics.txt").is_file()
    assert (stored.path / "clips" / "clip-alpha" / "audio" / "original.mp3").is_file()
    readme = (stored.path / "README.md").read_text(encoding="utf-8")
    report_md = (stored.path / "reports" / "capture-report.md").read_text(
        encoding="utf-8"
    )
    assert readme != report_md
    assert "- Model: chirp-v4" in readme
    assert "Major version: unknown" in readme
    assert "Suno Capture Report" in report_md
    plugin.storage().verify(stored.path)


def test_plugins_list_includes_suno() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])

    assert result.exit_code == 0
    assert "suno" in result.stdout
    assert "api" in result.stdout


def test_partial_media_counts_survive_verified_archive_persistence(
    tmp_path: Path,
) -> None:
    plugin = SunoPlugin()
    envelope = _make_envelope(tmp_path)
    envelope.assets = [
        asset for asset in envelope.assets if asset.asset_type != "audio"
    ]
    counts = {
        "audio_count": 0,
        "audio_expected_count": 1,
        "audio_failed_count": 1,
        "asset_expected_count": 2,
        "asset_downloaded_count": 1,
        "asset_failed_count": 1,
    }
    envelope.safe_metadata.update({"capture_complete": False, **counts})
    envelope.warnings.append(
        "Asset download failed for clip-alpha (audio): "
        "Failed to download Suno asset (403)."
    )
    normalized = plugin.normalize(envelope, WORKSPACE_ID)
    stored = plugin.storage().persist(
        _request(tmp_path), envelope, normalized, plugin.render(normalized)
    )

    # A valid archive of a partial capture passes integrity verification.
    assert stored.status == "complete"
    plugin.storage().verify(stored.path)
    for relative_path in (
        "workspace/metadata.json",
        "manifest.json",
        "reports/capture-report.json",
    ):
        artifact = json.loads((stored.path / relative_path).read_text(encoding="utf-8"))
        assert artifact["capture_complete"] is False
        assert artifact["capture_completeness"] == "partial"
        assert {key: artifact[key] for key in counts} == counts
    report = json.loads(
        (stored.path / "reports/capture-report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "partial"
    report_markdown = (stored.path / "reports/capture-report.md").read_text(
        encoding="utf-8"
    )
    assert "Capture completeness: partial" in report_markdown
    assert "Primary audio archived: 0 / 1 (1 failed)" in report_markdown
    assert "Advertised assets archived: 1 / 2 (1 failed)" in report_markdown


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_reports_partial_capture_with_valid_archive(
    tmp_path: Path, json_output: bool
) -> None:
    envelope = _make_envelope(tmp_path)
    envelope.assets = [
        asset for asset in envelope.assets if asset.asset_type != "audio"
    ]
    envelope.safe_metadata.update(
        {
            "clip_count": 2,
            "audio_count": 0,
            "audio_expected_count": 1,
            "audio_failed_count": 1,
            "capture_complete": False,
        }
    )
    envelope.warnings = ["Primary audio download failed (403)."]
    args = ["capture", "suno", WORKSPACE_ID, "--output", str(tmp_path / "archive")]
    if json_output:
        args.append("--json")
    with patch(
        "mindcap.plugins.suno.archive.service.SunoWorkspaceCaptureService.capture",
        return_value=envelope,
    ):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 2, result.output
    if json_output:
        data = json.loads(result.stdout)
        assert data["status"] == "complete"
        assert data["capture_complete"] is False
        assert data["capture_completeness"] == "partial"
        assert "Authenticating" in result.stderr
    else:
        output = " ".join(result.stdout.split())
        assert "Capture Partial" in output
        assert "Audio 0 / 1" in output
        assert "Capture completeness Partial" in output
        assert "Archive integrity PASS" in output


def test_capture_defaults_to_plugin_strategy_for_suno(tmp_path: Path) -> None:
    runner = CliRunner()
    envelope = _make_envelope(tmp_path)
    with patch(
        "mindcap.plugins.suno.archive.service.SunoWorkspaceCaptureService.capture",
        return_value=envelope,
    ):
        result = runner.invoke(
            app,
            [
                "capture",
                "suno",
                WORKSPACE_ID,
                "--output",
                str(tmp_path),
                "--quiet",
            ],
        )

    assert result.exit_code == 0
    assert str(
        tmp_path / "workspaces" / "suno" / f"suno-{WORKSPACE_ID}" / "v1"
    ) in result.stdout.replace("\n", "")
