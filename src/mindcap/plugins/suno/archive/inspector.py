"""Human-friendly archive inspection for a Suno workspace bundle."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from mindcap.core.errors import VerificationError
from mindcap.plugins.suno.archive.verifier import verify_workspace_bundle


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} TB"


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _count_assets(bundle_path: Path, subdir: str, suffix: str) -> int:
    target = bundle_path / subdir
    if not target.is_dir():
        return 0
    return sum(1 for f in target.rglob(f"*{suffix}") if f.is_file())


def inspect_suno_archive(bundle_path: Path, console: Console) -> None:
    """Display a human-readable inspection summary for a Suno archive bundle.

    Parameters
    ----------
    bundle_path:
        Path to a finalized Suno workspace bundle directory (the ``v<N>``
        directory produced by
        :class:`~mindcap.plugins.suno.archive.storage.SunoWorkspaceStorageStrategy`).
    console:
        Rich :class:`~rich.console.Console` to write the report to.
    """
    bundle_path = bundle_path.expanduser().resolve()
    if not bundle_path.is_dir():
        raise VerificationError(f'Archive directory not found: "{bundle_path}"')

    manifest_path = bundle_path / "manifest.json"
    checksums_path = bundle_path / "checksums.json"
    if not manifest_path.is_file():
        raise VerificationError("Bundle manifest is missing.")
    if not checksums_path.is_file():
        raise VerificationError("Bundle checksums are missing.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checksums_data = json.loads(checksums_path.read_text(encoding="utf-8"))

    # Run verification; note result but do not raise.
    verification_ok = True
    verification_detail: list[str] = []

    try:
        verify_workspace_bundle(bundle_path)
        verification_detail.append("[green]✓ Manifest[/green]")
        verification_detail.append("[green]✓ Checksums[/green]")
    except VerificationError as exc:
        verification_ok = False
        verification_detail.append(f"[red]✗ Verification failed: {exc}[/red]")

    # Count assets by type
    clips_path = bundle_path / "clips"
    clip_count = 0
    audio_count = 0
    artwork_count = 0
    video_count = 0

    if clips_path.is_dir():
        clip_dirs = [d for d in clips_path.iterdir() if d.is_dir()]
        clip_count = len(clip_dirs)
        for clip_dir in clip_dirs:
            audio_dir = clip_dir / "audio"
            if audio_dir.is_dir() and any(audio_dir.iterdir()):
                audio_count += 1
            artwork_dir = clip_dir / "artwork"
            if artwork_dir.is_dir() and any(artwork_dir.iterdir()):
                artwork_count += 1
            video_dir = clip_dir / "video"
            if video_dir.is_dir() and any(video_dir.iterdir()):
                video_count += 1

    checksums_file_count = len(checksums_data.get("files", []))
    archive_size = _dir_size(bundle_path)

    # Build main info table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()

    title = manifest.get("title") or manifest.get("workspace_id", "")
    workspace_id = manifest.get("workspace_id", "")
    schema_raw = manifest.get("schema") or ""
    schema_version = schema_raw.split("/")[-1] if schema_raw else "unknown"

    table.add_row("Workspace", title or workspace_id)
    if title and workspace_id and title != workspace_id:
        table.add_row("ID", workspace_id)
    table.add_row("Schema version", schema_version)
    table.add_row("Capture version", str(manifest.get("capture_version", "?")))
    table.add_row("Captured at", str(manifest.get("captured_at", "?")))
    table.add_row("Clips", str(clip_count))
    table.add_row("Audio", str(audio_count))
    table.add_row("Artwork", str(artwork_count))
    table.add_row("Videos", str(video_count))
    table.add_row("Checksummed files", str(checksums_file_count))
    table.add_row("Archive size", _fmt_bytes(archive_size))
    table.add_row(
        "Checksums",
        "[green]PASS[/green]" if verification_ok else "[red]FAIL[/red]",
    )
    table.add_row(
        "Manifest",
        "[green]PASS[/green]" if verification_ok else "[red]FAIL[/red]",
    )

    warnings = manifest.get("warnings") or []
    if warnings:
        table.add_row("Warnings", str(len(warnings)))

    console.rule("[bold]Suno Archive Inspection[/bold]")
    console.print(table)

    if not verification_ok:
        for detail_line in verification_detail:
            console.print(detail_line)

    if warnings:
        console.print()
        console.print("[yellow]Recorded warnings:[/yellow]")
        for w in warnings:
            console.print(f"  [yellow]•[/yellow] {w}")
