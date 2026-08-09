from __future__ import annotations

from typing import Any

from mindcap.core.errors import InvalidSourceError
from mindcap.core.models import CaptureEnvelope
from mindcap.core.progress import CaptureProgressReporter
from mindcap.core.protocols import CaptureStrategy
from mindcap.plugins.suno.archive.storage import SunoWorkspaceStorageStrategy
from mindcap.plugins.suno.identifiers import (
    canonicalize_suno_identifier,
    supports_suno_source,
)
from mindcap.plugins.suno.normalizer import normalize_suno
from mindcap.plugins.suno.renderer import render_suno_markdown
from mindcap.plugins.suno.strategies.api import SunoApiCaptureStrategy


class SunoPlugin:
    source_type = "suno"

    def supports(self, value: str) -> bool:
        return supports_suno_source(value)

    def canonicalize(self, value: str) -> tuple[str, str | None]:
        return canonicalize_suno_identifier(value)

    def default_strategy(self) -> str:
        return "api"

    def strategies(self) -> tuple[str, ...]:
        return ("api",)

    def strategy(
        self,
        name: str,
        reporter: CaptureProgressReporter | None = None,
    ) -> CaptureStrategy:
        strategies: dict[str, CaptureStrategy] = {
            "api": SunoApiCaptureStrategy(reporter=reporter)
        }
        try:
            return strategies[name]
        except KeyError as error:
            available = ", ".join(sorted(strategies))
            raise InvalidSourceError(
                f'Unknown Suno strategy "{name}". Available: {available}'
            ) from error

    def normalize(
        self, envelope: CaptureEnvelope, requested_identifier: str
    ) -> dict[str, Any]:
        return normalize_suno(envelope, requested_identifier)

    def render(self, normalized: dict[str, Any]) -> str:
        return render_suno_markdown(normalized)

    def storage(self) -> SunoWorkspaceStorageStrategy:
        return SunoWorkspaceStorageStrategy()

    def vault_adapter(self):
        from mindcap.plugins.suno.archive.vault import SunoVaultArchiveAdapter

        return SunoVaultArchiveAdapter()
