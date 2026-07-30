from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from mindcap.cli import app
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.plugins.distrokid.plugin import DistroKidPlugin

ALBUM_UUID = "642baa93-568f-47a7-9955-8e4426a9d1d0"
_CAPTURED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _request(tmp_path: Path) -> CaptureRequest:
    return CaptureRequest(
        source_type="distrokid",
        source=ALBUM_UUID,
        provider="distrokid",
        canonical_identifier=ALBUM_UUID,
        canonical_url=f"https://distrokid.com/dashboard/album/?albumuuid={ALBUM_UUID}",
        strategy="browser",
        artifact_root=tmp_path,
    )


def _envelope() -> CaptureEnvelope:
    payload = {
        "albumuuid": ALBUM_UUID,
        "title": "Synthetic Release",
        "tracks": [{"track_number": 1, "title": "Synthetic Track"}],
    }
    return CaptureEnvelope(
        provider="distrokid",
        source_type="release",
        canonical_identifier=ALBUM_UUID,
        canonical_url=f"https://distrokid.com/dashboard/album/?albumuuid={ALBUM_UUID}",
        captured_at=_CAPTURED_AT,
        strategy="browser",
        response_units=[
            RawResponseUnit(
                unit_id="release-000",
                sequence=0,
                media_type="application/json",
                body=json.dumps(payload).encode("utf-8"),
                source_url=f"https://distrokid.com/dashboard/album/?albumuuid={ALBUM_UUID}",
                endpoint_category="release",
                retrieved_at=_CAPTURED_AT,
            )
        ],
        safe_metadata={"capture_complete": True},
    )


def test_plugins_list_includes_distrokid() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["plugins", "list"])

    assert result.exit_code == 0
    assert "distrokid" in result.stdout
    assert "browser" in result.stdout


def test_distrokid_pipeline_creates_verified_bundle(tmp_path: Path) -> None:
    plugin = DistroKidPlugin()
    request = _request(tmp_path)
    envelope = _envelope()
    normalized = plugin.normalize(envelope, ALBUM_UUID)
    transcript = plugin.render(normalized)

    stored = plugin.storage().persist(request, envelope, normalized, transcript)

    assert stored.status == "complete"
    assert (stored.path / "manifest.json").is_file()
    assert (stored.path / "release" / "metadata.json").is_file()
    assert (stored.path / "indexes" / "upc.json").is_file()
    plugin.storage().verify(stored.path)


def test_capture_defaults_to_browser_strategy_for_distrokid(tmp_path: Path) -> None:
    runner = CliRunner()
    envelope = _envelope()
    with patch(
        "mindcap.plugins.distrokid.strategies.browser.DistroKidBrowserCaptureStrategy.capture",
        return_value=envelope,
    ):
        result = runner.invoke(
            app,
            [
                "capture",
                "distrokid",
                ALBUM_UUID,
                "--output",
                str(tmp_path),
                "--quiet",
            ],
        )

    assert result.exit_code == 0
    assert str(
        tmp_path / "workspaces" / "distrokid" / f"distrokid-{ALBUM_UUID}" / "v1"
    ) in result.stdout.replace("\n", "")


def test_inspect_distrokid_archive_command(tmp_path: Path) -> None:
    plugin = DistroKidPlugin()
    request = _request(tmp_path)
    envelope = _envelope()
    normalized = plugin.normalize(envelope, ALBUM_UUID)
    transcript = plugin.render(normalized)
    stored = plugin.storage().persist(request, envelope, normalized, transcript)

    runner = CliRunner()
    result = runner.invoke(app, ["inspect", "distrokid", str(stored.path)])

    assert result.exit_code == 0
    assert "DistroKid Archive" in result.stdout
    assert "Album UUID" in result.stdout
