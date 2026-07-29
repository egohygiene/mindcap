from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from mindcap.core.models import CaptureEnvelope, CaptureRequest, StoredBundle


class CaptureStrategy(Protocol):
    name: str

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        """Acquire a source without performing semantic extraction."""
        ...


class SourcePlugin(Protocol):
    source_type: str

    def supports(self, value: str) -> bool: ...

    def canonicalize(self, value: str) -> tuple[str, str | None]: ...

    def strategy(self, name: str) -> CaptureStrategy: ...

    def normalize(
        self, envelope: CaptureEnvelope, requested_identifier: str
    ) -> dict[str, Any]: ...

    def render(self, normalized: dict[str, Any]) -> str: ...


class StorageStrategy(Protocol):
    def persist(
        self,
        request: CaptureRequest,
        envelope: CaptureEnvelope,
        normalized: dict[str, Any],
        transcript: str,
    ) -> StoredBundle: ...

    def verify(self, bundle_path: Path) -> None: ...
