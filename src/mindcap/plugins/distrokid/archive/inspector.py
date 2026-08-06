from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from mindcap.core.errors import VerificationError
from mindcap.plugins.distrokid.archive.verifier import verify_distrokid_bundle


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_distrokid_archive(archive: Path, console: Console) -> None:
    bundle_path = archive.expanduser().resolve()
    if not bundle_path.is_dir():
        raise VerificationError(f'DistroKid archive does not exist: "{bundle_path}"')

    verify_distrokid_bundle(bundle_path)

    manifest = _load_json(bundle_path / "manifest.json")
    metadata = _load_json(bundle_path / str(manifest["metadata_path"]))
    source_type = str(manifest.get("source_type") or "unknown")

    table = Table(title="DistroKid Archive")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Source type", source_type)
    table.add_row("Source ID", str(manifest.get("source_id", "-")))
    table.add_row("Capture version", str(manifest.get("capture_version", "-")))
    table.add_row("Canonical URL", str(manifest.get("canonical_url", "-")))
    table.add_row("Raw response units", str(manifest.get("raw_unit_count", 0)))
    table.add_row("Warnings", str(len(manifest.get("warnings") or [])))

    if source_type == "library":
        library = metadata.get("library") or {}
        table.add_row("Releases discovered", str(len(library.get("releases") or [])))
        completeness = library.get("completeness") or {}
        table.add_row(
            "Capture complete",
            "yes" if completeness.get("capture_complete") else "no",
        )
    else:
        release = metadata.get("release") or {}
        table.add_row("Album UUID", str(release.get("album_uuid", "-")))
        table.add_row("Tracks", str(len(release.get("tracks") or [])))
        table.add_row("Artwork files", str(len(release.get("artwork") or [])))
        table.add_row(
            "Store destinations", str(len(release.get("store_destinations") or []))
        )

    console.print(table)
