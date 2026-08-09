from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_bytes
from mindcap.plugins.suno.archive.vault import SunoVaultArchiveAdapter
from mindcap.plugins.suno.archive.verifier import verify_workspace_bundle
from mindcap.vault.errors import (
    SourceMutationError,
    StaleVaultLockError,
    VaultLockError,
)
from mindcap.vault.layout import load_vault_metadata, writer_lock_path
from mindcap.vault.service import VaultService
from mindcap_cli.app import app

_CAPTURED_AT = "2025-01-03T00:00:00+00:00"


def _make_suno_bundle(
    root: Path,
    *,
    source_id: str,
    version: int,
    title: str = "Vault Test Workspace",
    audio_payload: bytes = b"fake mp3",
    artwork_payload: bytes = b"fake jpg",
    extra_duplicates: bool = False,
) -> Path:
    bundle = root / source_id / f"v{version}"
    bundle.mkdir(parents=True)
    manifest: dict[str, object] = {
        "schema": "mindcap.suno-archive/v0.1",
        "provider": "suno",
        "source_type": "workspace",
        "workspace_id": source_id.removeprefix("suno-"),
        "source_id": source_id,
        "canonical_url": None,
        "title": title,
        "capture_version": version,
        "previous_version": version - 1 if version > 1 else None,
        "captured_at": _CAPTURED_AT,
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
    (bundle / "README.md").write_text("# Test\n", encoding="utf-8")
    (bundle / "reports").mkdir()
    (bundle / "reports" / "capture-report.json").write_text("{}", encoding="utf-8")
    (bundle / "reports" / "capture-report.md").write_text(
        "# Report\n", encoding="utf-8"
    )
    (bundle / "workspace").mkdir()
    workspace_metadata = {
        "source_id": source_id,
        "workspace_id": manifest["workspace_id"],
        "title": title,
        "captured_at": _CAPTURED_AT,
    }
    (bundle / "workspace" / "metadata.json").write_text(
        json.dumps(workspace_metadata, indent=2), encoding="utf-8"
    )
    clip_dir = bundle / "clips" / "clip-alpha"
    (clip_dir / "audio").mkdir(parents=True)
    (clip_dir / "audio" / "original.mp3").write_bytes(audio_payload)
    (clip_dir / "artwork").mkdir()
    (clip_dir / "artwork" / "cover.jpg").write_bytes(artwork_payload)
    (clip_dir / "metadata.json").write_text(
        json.dumps({"clip_id": "clip-alpha", "title": "Alpha"}, indent=2),
        encoding="utf-8",
    )
    if extra_duplicates:
        (clip_dir / "lyrics").mkdir()
        (clip_dir / "lyrics" / "lyrics.txt").write_bytes(audio_payload)
    files_to_checksum = [
        "README.md",
        "reports/capture-report.json",
        "reports/capture-report.md",
        "workspace/metadata.json",
        "clips/clip-alpha/metadata.json",
        "clips/clip-alpha/audio/original.mp3",
        "clips/clip-alpha/artwork/cover.jpg",
    ]
    if extra_duplicates:
        files_to_checksum.append("clips/clip-alpha/lyrics/lyrics.txt")
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


def _snapshot_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def test_suno_vault_adapter_discovers_workspace_roots_and_bundle_directories(
    tmp_path: Path,
) -> None:
    adapter = SunoVaultArchiveAdapter()
    bundle_a = _make_suno_bundle(
        tmp_path / "workspaces", source_id="suno-ws-a", version=1
    )
    bundle_b = _make_suno_bundle(
        tmp_path / "workspaces", source_id="suno-ws-b", version=2
    )

    discovered_root = list(adapter.discover(tmp_path / "workspaces"))
    discovered_bundle = list(adapter.discover(bundle_a))

    assert discovered_root == [bundle_a, bundle_b]
    assert discovered_bundle == [bundle_a]


def test_vault_ingest_verify_and_restore_round_trip(tmp_path: Path) -> None:
    source_root = tmp_path / "fixture source with spaces" / "sünø"
    bundle = _make_suno_bundle(source_root, source_id="suno-ws-roundtrip", version=1)
    destination = tmp_path / "Drive Path With Spaces" / "suno.mindcap-vault"
    service = VaultService()
    source_snapshot = _snapshot_tree(source_root)

    summary = service.ingest(
        provider="suno",
        source=source_root,
        destination=destination,
        pack_size_mib=1,
    )

    assert summary.imported_archives == 1
    assert summary.pack_files_created == 1
    assert summary.catalog_generation_published == 1
    assert summary.import_receipt_path is not None
    assert _snapshot_tree(source_root) == source_snapshot
    metadata = load_vault_metadata(destination)
    assert metadata.format == "mindcap.vault/v1"

    inspect_summary = service.inspect(destination)
    assert inspect_summary.archive_units == 1
    assert inspect_summary.logical_bytes >= inspect_summary.physical_bytes

    verify_fast = service.verify(destination)
    verify_deep = service.verify(destination, deep=True)
    assert verify_fast.valid is True
    assert verify_deep.valid is True

    db_path = destination / "catalog" / "generations" / "catalog-00000001.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        files = {
            row[0]
            for row in conn.execute(
                "SELECT relative_path FROM archive_files ORDER BY relative_path"
            )
        }
    finally:
        conn.close()
    assert "manifest.json" in files
    assert "checksums.json" in files

    restored_root = tmp_path / "restored"
    restored = service.restore(
        vault_path=destination,
        provider="suno",
        source_id="suno-ws-roundtrip",
        capture_version="1",
        destination=restored_root,
    )
    assert restored.restored_bundle_path == restored_root / "suno-ws-roundtrip" / "v1"
    verify_workspace_bundle(restored.restored_bundle_path)
    assert _snapshot_tree(bundle) == _snapshot_tree(restored.restored_bundle_path)


def test_vault_dry_run_does_not_create_destination_metadata(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _make_suno_bundle(source_root, source_id="suno-ws-dry-run", version=1)
    destination = tmp_path / "vault"

    summary = VaultService().ingest(
        provider="suno",
        source=source_root,
        destination=destination,
        dry_run=True,
    )

    assert summary.dry_run is True
    assert not (destination / "vault.json").exists()


def test_vault_rerun_is_noop_and_leaves_vault_unchanged(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _make_suno_bundle(source_root, source_id="suno-ws-noop", version=1)
    destination = tmp_path / "vault"
    service = VaultService()

    first = service.ingest(provider="suno", source=source_root, destination=destination)
    snapshot = _snapshot_tree(destination)
    second = service.ingest(
        provider="suno", source=source_root, destination=destination
    )

    assert first.imported_archives == 1
    assert second.imported_archives == 0
    assert second.already_present_archives == 1
    assert second.pack_files_created == 0
    assert second.catalog_generation_published is None
    assert _snapshot_tree(destination) == snapshot


def test_vault_incremental_ingest_deduplicates_across_versions(tmp_path: Path) -> None:
    source_root = tmp_path / "workspace-root"
    audio_payload = b"same-audio"
    _make_suno_bundle(
        source_root,
        source_id="suno-ws-incremental",
        version=1,
        audio_payload=audio_payload,
    )
    destination = tmp_path / "vault"
    service = VaultService()
    first = service.ingest(provider="suno", source=source_root, destination=destination)
    assert first.imported_archives == 1

    _make_suno_bundle(
        source_root,
        source_id="suno-ws-incremental",
        version=2,
        audio_payload=audio_payload,
        artwork_payload=b"new-artwork",
    )
    second = service.ingest(
        provider="suno", source=source_root, destination=destination
    )

    assert second.imported_archives == 1
    assert second.already_present_archives == 1
    assert second.objects_deduplicated >= 1
    inspect_summary = service.inspect(destination)
    assert inspect_summary.archive_units == 2
    assert inspect_summary.deduplicated_bytes_saved > 0


def test_vault_pack_rotation_and_large_object_handling(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    large = b"a" * (1024 * 1024 + 1024)
    medium_a = b"b" * 700_000
    medium_b = b"c" * 700_000
    _make_suno_bundle(
        source_root, source_id="suno-ws-large", version=1, audio_payload=large
    )
    _make_suno_bundle(
        source_root, source_id="suno-ws-a", version=1, audio_payload=medium_a
    )
    _make_suno_bundle(
        source_root, source_id="suno-ws-b", version=1, audio_payload=medium_b
    )
    destination = tmp_path / "vault"

    summary = VaultService().ingest(
        provider="suno",
        source=source_root,
        destination=destination,
        pack_size_mib=1,
    )

    assert summary.pack_files_created >= 3


def test_vault_detects_source_mutation_during_pack_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    bundle = _make_suno_bundle(source_root, source_id="suno-ws-mutation", version=1)
    target = bundle / "clips" / "clip-alpha" / "audio" / "original.mp3"
    from mindcap.vault.ingest import hash_source_file as original

    mutated = False

    def mutate_once(path: Path) -> tuple[str, int]:
        nonlocal mutated
        digest, size = original(path)
        if path == target and not mutated:
            path.write_bytes(b"changed after planning")
            mutated = True
        return (digest, size)

    monkeypatch.setattr("mindcap.vault.service.hash_source_file", mutate_once)

    with pytest.raises(SourceMutationError):
        VaultService().ingest(
            provider="suno",
            source=source_root,
            destination=tmp_path / "vault",
        )


def test_vault_reuses_sealed_packs_after_interrupted_catalog_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    _make_suno_bundle(source_root, source_id="suno-ws-reuse", version=1)
    destination = tmp_path / "vault"
    service = VaultService()

    def fail_publish(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated publish failure")

    monkeypatch.setattr(service, "_publish_catalog_generation", fail_publish)
    with pytest.raises(RuntimeError, match="simulated publish failure"):
        service.ingest(provider="suno", source=source_root, destination=destination)
    pack_files = sorted((destination / "packs").glob("*.zip"))
    assert len(pack_files) == 1

    retry = VaultService().ingest(
        provider="suno", source=source_root, destination=destination
    )
    assert retry.imported_archives == 1
    assert retry.pack_files_created == 0
    assert len(sorted((destination / "packs").glob("*.zip"))) == 1


def test_vault_active_and_stale_writer_locks(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _make_suno_bundle(source_root, source_id="suno-ws-locks", version=1)
    destination = tmp_path / "vault"
    service = VaultService(stale_lock_timeout=timedelta(seconds=1))
    lock_path = writer_lock_path(destination)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "owner": "writer-now",
                "created_at": datetime.now(UTC).isoformat(),
                "pid": 123,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(VaultLockError):
        service.ingest(provider="suno", source=source_root, destination=destination)

    lock_path.write_text(
        json.dumps(
            {
                "owner": "writer-old",
                "created_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "pid": 123,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StaleVaultLockError):
        service.ingest(provider="suno", source=source_root, destination=destination)
    service.recover_stale_lock(destination)
    summary = service.ingest(
        provider="suno", source=source_root, destination=destination
    )
    assert summary.imported_archives == 1


def test_vault_cli_outputs_human_and_json(tmp_path: Path) -> None:
    source_root = tmp_path / "cli source"
    _make_suno_bundle(source_root, source_id="suno-ws-cli", version=1)
    vault = tmp_path / "cli vault"
    runner = CliRunner()

    ingest_result = runner.invoke(
        app,
        [
            "vault",
            "ingest",
            "--provider",
            "suno",
            "--source",
            str(source_root),
            "--destination",
            str(vault),
        ],
    )
    assert ingest_result.exit_code == 0
    assert "Archive units imported" in ingest_result.stdout

    inspect_result = runner.invoke(
        app, ["vault", "inspect", "--vault", str(vault), "--json"]
    )
    assert inspect_result.exit_code == 0
    inspect_payload = json.loads(inspect_result.stdout)
    assert inspect_payload["archive_units"] == 1

    verify_result = runner.invoke(
        app, ["vault", "verify", "--vault", str(vault), "--deep"]
    )
    assert verify_result.exit_code == 0
    assert "PASS" in verify_result.stdout

    restore_root = tmp_path / "restore-cli"
    restore_result = runner.invoke(
        app,
        [
            "vault",
            "restore",
            "--vault",
            str(vault),
            "--provider",
            "suno",
            "--source-id",
            "suno-ws-cli",
            "--capture-version",
            "1",
            "--destination",
            str(restore_root),
            "--json",
        ],
    )
    assert restore_result.exit_code == 0
    restore_payload = json.loads(restore_result.stdout)
    assert restore_payload["restored_files"] >= 1


def test_taskfile_vault_tasks_forward_cli_args_without_wrapping() -> None:
    taskfile = Path(__file__).resolve().parents[1] / "Taskfile.yml"
    text = taskfile.read_text(encoding="utf-8")

    assert "uv run mindcap vault ingest {{.CLI_ARGS}}" in text
    assert "uv run mindcap vault inspect {{.CLI_ARGS}}" in text
    assert "uv run mindcap vault verify {{.CLI_ARGS}}" in text
    assert "uv run mindcap vault restore {{.CLI_ARGS}}" in text
    assert 'uv run mindcap vault ingest "{{.CLI_ARGS}}"' not in text


def test_unsupported_vault_version_is_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "vault.json").write_text(
        json.dumps(
            {
                "vault_id": "vault-123",
                "format": "mindcap.vault/v1",
                "format_version": 999,
                "created_at": _CAPTURED_AT,
                "hashing_algorithm": "sha256",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="Unsupported vault format"):
        VaultService().inspect(vault)


def test_invalid_suno_bundle_is_rejected(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    bundle = _make_suno_bundle(source_root, source_id="suno-ws-invalid", version=1)
    (bundle / "clips" / "clip-alpha" / "audio" / "original.mp3").write_bytes(
        b"tampered"
    )

    with pytest.raises(VerificationError):
        VaultService().ingest(
            provider="suno", source=source_root, destination=tmp_path / "vault"
        )
