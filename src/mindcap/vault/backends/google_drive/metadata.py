from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoogleDriveArtifactMetadata:
    vault_id: str
    artifact_kind: str
    artifact_id: str
    sha256: str
    format_version: str

    def to_app_properties(self) -> dict[str, str]:
        return {
            "mindcap_vault_id": self.vault_id,
            "mindcap_artifact_kind": self.artifact_kind,
            "mindcap_artifact_id": self.artifact_id,
            "mindcap_sha256": self.sha256,
            "mindcap_format_version": self.format_version,
        }
