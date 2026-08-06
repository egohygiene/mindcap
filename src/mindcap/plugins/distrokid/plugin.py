from __future__ import annotations

from typing import Any

from mindcap.core.errors import InvalidSourceError
from mindcap.core.models import CaptureEnvelope
from mindcap.core.progress import CaptureProgressReporter
from mindcap.core.protocols import CaptureStrategy
from mindcap.plugins.distrokid.archive.storage import DistroKidArchiveStorageStrategy
from mindcap.plugins.distrokid.identifiers import (
    canonicalize_distrokid_identifier,
    supports_distrokid_source,
)
from mindcap.plugins.distrokid.normalizer import normalize_distrokid
from mindcap.plugins.distrokid.renderer import render_distrokid_markdown
from mindcap.plugins.distrokid.strategies.browser import DistroKidBrowserCaptureStrategy


class DistroKidPlugin:
    source_type = "distrokid"

    def supports(self, value: str) -> bool:
        return supports_distrokid_source(value)

    def canonicalize(self, value: str) -> tuple[str, str | None]:
        return canonicalize_distrokid_identifier(value)

    def default_strategy(self) -> str:
        return "browser"

    def strategies(self) -> tuple[str, ...]:
        return ("browser",)

    def strategy(
        self,
        name: str,
        reporter: CaptureProgressReporter | None = None,
    ) -> CaptureStrategy:
        strategies: dict[str, CaptureStrategy] = {
            "browser": DistroKidBrowserCaptureStrategy(reporter=reporter)
        }
        try:
            return strategies[name]
        except KeyError as error:
            available = ", ".join(sorted(strategies))
            raise InvalidSourceError(
                f'Unknown DistroKid strategy "{name}". Available: {available}'
            ) from error

    def normalize(
        self, envelope: CaptureEnvelope, requested_identifier: str
    ) -> dict[str, Any]:
        return normalize_distrokid(envelope, requested_identifier)

    def render(self, normalized: dict[str, Any]) -> str:
        return render_distrokid_markdown(normalized)

    def storage(self) -> DistroKidArchiveStorageStrategy:
        return DistroKidArchiveStorageStrategy()
