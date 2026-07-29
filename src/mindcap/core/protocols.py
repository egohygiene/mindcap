from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from mindcap.core.models import CaptureEnvelope, CaptureRequest, StoredBundle
from mindcap.core.progress import CaptureProgressReporter


class CaptureStrategy(Protocol):
    name: str

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        """Acquire a source without performing semantic extraction."""
        ...


class SourcePlugin(Protocol):
    source_type: str

    def supports(self, value: str) -> bool: ...

    def canonicalize(self, value: str) -> tuple[str, str | None]: ...

    def default_strategy(self) -> str: ...

    def strategies(self) -> tuple[str, ...]: ...

    def strategy(
        self,
        name: str,
        reporter: CaptureProgressReporter | None = None,
    ) -> CaptureStrategy: ...

    def normalize(
        self, envelope: CaptureEnvelope, requested_identifier: str
    ) -> dict[str, Any]: ...

    def render(self, normalized: dict[str, Any]) -> str: ...

    def storage(self) -> StorageStrategy: ...


class StorageStrategy(Protocol):
    def persist(
        self,
        request: CaptureRequest,
        envelope: CaptureEnvelope,
        normalized: dict[str, Any],
        transcript: str,
    ) -> StoredBundle: ...

    def verify(self, bundle_path: Path) -> None: ...
