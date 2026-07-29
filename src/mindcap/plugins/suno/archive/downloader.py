from __future__ import annotations

from pathlib import Path
from typing import Any

from mindcap.plugins.suno.client import SunoClient


class SunoAssetDownloader:
    def __init__(self, client: SunoClient) -> None:
        self._client = client

    def download(self, url: str) -> tuple[Path, dict[str, Any]]:
        return self._client.download_to_file(url)
