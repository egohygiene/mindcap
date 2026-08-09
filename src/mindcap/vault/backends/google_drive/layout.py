from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoogleDriveVaultLayout:
    vault_folder_id: str

    @property
    def locator(self) -> str:
        return f"gdrive://{self.vault_folder_id}"

    @property
    def vault_url(self) -> str:
        return f"https://drive.google.com/drive/folders/{self.vault_folder_id}"
