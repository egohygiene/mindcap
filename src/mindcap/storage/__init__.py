"""Mindcap artifact storage strategies."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from mindcap.core.errors import VerificationError


def verify_bundle(bundle_path: Path) -> None:
    manifest_path = bundle_path / "manifest.yaml"
    json_manifest_path = bundle_path / "manifest.json"
    if manifest_path.is_file():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    elif json_manifest_path.is_file():
        manifest = json.loads(json_manifest_path.read_text(encoding="utf-8"))
    else:
        raise VerificationError("Bundle manifest is missing.")

    provider = str(manifest.get("provider") or "")
    if not provider:
        raise VerificationError("Bundle manifest is missing a provider.")

    from mindcap.registry import build_registry

    plugin = build_registry().get(provider)
    plugin.storage().verify(bundle_path)
