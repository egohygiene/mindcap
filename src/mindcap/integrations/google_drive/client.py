from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GoogleDriveClient:
    """Minimal placeholder for an injected Drive API client.

    The vault backend consumes this interface indirectly so tests can supply a
    deterministic fake implementation without network access.
    """

    account_email: str

    def list_files(
        self, query: str, *, page_token: str | None = None
    ) -> dict[str, Any]:
        _ = (query, page_token)
        raise NotImplementedError
