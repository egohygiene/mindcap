"""Human-friendly inspection of SoundCloud archive bundles."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from mindcap.core.errors import VerificationError
from mindcap.plugins.soundcloud.archive.verifier import verify_soundcloud_bundle


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} TB"


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def inspect_soundcloud_archive(bundle_path: Path, console: Console) -> None:
    """Display a human-readable inspection summary for a SoundCloud archive.

    Parameters
    ----------
    bundle_path:
        Path to a finalized SoundCloud bundle directory (the ``v<N>`` directory).
    console:
        Rich console to write the report to.
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

    # Load normalized metadata for richer output.
    metadata: dict = {}  # type: ignore[type-arg]
    meta_path = bundle_path / "source" / "metadata.json"
    if meta_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    # Run offline verification.
    verification_ok = True
    try:
        verify_soundcloud_bundle(bundle_path)
    except VerificationError:
        verification_ok = False

    source_type = manifest.get("source_type", "unknown")
    schema_raw = manifest.get("schema") or ""
    schema_version = schema_raw.split("/")[-1] if schema_raw else "unknown"
    checksums_count = len(checksums_data.get("files", []))
    archive_size = _dir_size(bundle_path)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()

    table.add_row("Source ID", str(manifest.get("source_id", "-")))
    table.add_row("Source type", source_type)
    table.add_row("Schema version", schema_version)
    table.add_row("Capture version", str(manifest.get("capture_version", "?")))
    table.add_row("Captured at", str(manifest.get("captured_at", "?")))

    if source_type == "track":
        track = metadata.get("track") or {}
        table.add_row("Title", str(track.get("title") or "-"))
        table.add_row("Track ID", str(track.get("track_id") or "-"))
        table.add_row("ISRC", str(track.get("isrc") or "-"))
        table.add_row("Visibility", str(track.get("sharing") or "-"))
        table.add_row("Duration", f"{track.get('duration_ms') or '-'} ms")
        table.add_row("Genre", str(track.get("genre") or "-"))
        table.add_row("Plays", str(track.get("playback_count") or "-"))
        table.add_row("Likes", str(track.get("likes_count") or "-"))

    elif source_type == "playlist":
        playlist = metadata.get("playlist") or {}
        table.add_row("Title", str(playlist.get("title") or "-"))
        table.add_row("Playlist ID", str(playlist.get("playlist_id") or "-"))
        is_album = playlist.get("is_album")
        table.add_row("Type", "album" if is_album else "playlist")
        track_ids = playlist.get("track_ids") or []
        table.add_row("Track count", str(len(track_ids)))

    elif source_type == "account":
        account = metadata.get("account") or {}
        tracks = metadata.get("tracks") or []
        playlists = metadata.get("playlists") or []
        table.add_row("Username", str(account.get("username") or "-"))
        table.add_row("User ID", str(account.get("user_id") or "-"))
        table.add_row("Verified", str(account.get("verified") or "-"))
        table.add_row("Track count (provider)", str(account.get("track_count") or "-"))
        table.add_row("Tracks captured", str(len(tracks)))
        table.add_row("Playlists captured", str(len(playlists)))

    table.add_row("Raw units", str(manifest.get("raw_unit_count", "-")))
    table.add_row("Checksummed files", str(checksums_count))
    table.add_row("Archive size", _fmt_bytes(archive_size))
    table.add_row(
        "Verification",
        "[green]PASS[/green]" if verification_ok else "[red]FAIL[/red]",
    )

    warnings = manifest.get("warnings") or []
    if warnings:
        table.add_row("Warnings", str(len(warnings)))

    console.rule("[bold]SoundCloud Archive Inspection[/bold]")
    console.print(table)

    if warnings:
        console.print()
        console.print("[yellow]Recorded warnings:[/yellow]")
        for w in warnings:
            console.print(f"  [yellow]•[/yellow] {w}")
